"""Manual Mac T1 control: offline checks, a one-cell probe, or four-policy Pilot.

GitHub Actions runs this module only on a trusted macOS self-hosted runner.
The host is not itself a secret-free sandbox. The public T1 reference holdout
must be independently isolated before any formal research claim is made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Make the checked-out repository sources importable for both direct CLI use and
# the isolated Mac runner without installing or changing user-global packages.
REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT / "src", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from experiments.researchbench_t1.pilot_controller import run_pilot  # noqa: E402
from experiments.researchbench_t1.resource_guard import (  # noqa: E402
    ResourceRefusal,
    guarded_run,
    inspect_host,
    single_runner_lock,
)
from experiments.researchbench_t1.t1 import (  # noqa: E402
    BenchmarkError,
    canonical,
    normalize_candidate,
)

T1 = Path(__file__).resolve().parent / "t1.py"
MODEL_TIMEOUT = 150
REQUIRED_MODEL = "gpt-6-luna"
REQUIRED_REASONING_EFFORT = "max"
ALLOWED_PROVIDERS = ("codex_chatgpt", "ccswitch_codex")
ALLOWED_MODES = (
    "diagnose",
    "resource_probe",
    "offline_smoke",
    "pilot_one_cell",
    "pilot_prepare",
    "pilot",
)
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./():-]{0,99}$")


class MacRunBlocked(RuntimeError):
    """Fail-closed error without printing provider outputs or credentials."""


def _model_name(model: str) -> str:
    """Never use a default, alternate, or provider-fallback model for T1."""
    if model != REQUIRED_MODEL:
        raise MacRunBlocked(
            f"T1 permits only model {REQUIRED_MODEL} with reasoning effort "
            f"{REQUIRED_REASONING_EFFORT}"
        )
    return REQUIRED_MODEL


def _codex_command(codex: str, response: Path) -> list[str]:
    """Freeze the exact model slug and independent reasoning-effort setting."""
    return [
        codex,
        "--no-daemon",
        "exec",
        "--model",
        REQUIRED_MODEL,
        "-c",
        'model_reasoning_effort="max"',
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--output-last-message",
        str(response),
    ]


def parse_frozen_candidate_reply(raw: str) -> tuple[dict[str, Any], str]:
    """Accept exactly one allowlisted JSON candidate, never executable instructions."""
    message = raw.strip()
    if not message or len(raw.encode("utf-8")) > 4096:
        raise ValueError("missing or oversized model reply")
    try:
        return normalize_candidate(json.loads(message)), "strict_json"
    except (ValueError, TypeError):
        pass

    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", message, flags=re.DOTALL)
    if fenced is not None:
        try:
            return normalize_candidate(json.loads(fenced.group(1))), "fenced_json"
        except (ValueError, TypeError):
            pass

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for index, character in enumerate(message):
        if character != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(message[index:])
            candidate = normalize_candidate(parsed)
        except (ValueError, TypeError):
            continue
        candidates.append(candidate)
    if len(candidates) != 1:
        raise ValueError("no unique allowlisted candidate in output")
    return candidates[0], "embedded_json"


def _python_ready() -> bool:
    return sys.version_info >= (3, 11)


def _codex() -> str:
    found = shutil.which("codex")
    bundled = Path(
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
    )
    if found:
        return found
    if bundled.is_file():
        return str(bundled)
    raise MacRunBlocked("Codex CLI not installed in PATH or the known ChatGPT app bundle")


def _check_chatgpt_login(codex: str) -> bool:
    """Check authentication without retaining or displaying the CLI response."""
    try:
        outcome = guarded_run([codex, "login", "status"], timeout=15)
    except (OSError, subprocess.TimeoutExpired, ResourceRefusal):
        return False
    return outcome.returncode == 0


def _run(
    argv: list[str], *, timeout: int, cwd: Path | None = None, env: dict[str, str] | None = None
) -> str:
    """Run frozen argv without shell interpolation or raw tool-output logging."""
    process = guarded_run(argv, cwd=cwd, env=env, timeout=timeout)
    if process.returncode != 0:
        raise MacRunBlocked("a local command failed; inspect the runner configuration")
    if len(process.stdout) > 200_000:
        raise MacRunBlocked("local command output exceeded safe size")
    return process.stdout


def _t1(*args: str, timeout: int = 60) -> dict[str, Any]:
    try:
        return json.loads(_run([sys.executable, str(T1), *args], timeout=timeout))
    except json.JSONDecodeError as exc:
        raise MacRunBlocked("T1 CLI output did not match its JSON protocol") from exc


def _redacted_agent_env() -> dict[str, str]:
    """Exclude GitHub job credentials from the subprocess when possible."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GITHUB_", "ACTIONS_", "RUNNER_"))
        and key
        not in {
            "GH_TOKEN",
            "GIT_ASKPASS",
            "SSH_AUTH_SOCK",
            "ACTIONS_RUNTIME_TOKEN",
        }
    } | {"GIT_TERMINAL_PROMPT": "0", "CI": "1"}


def _count_pilot_calls(root: Path) -> int:
    events = root / "pilot_work" / "events.jsonl"
    if not events.is_file():
        return 0
    try:
        return sum(
            json.loads(line).get("kind") == "agent_call_started"
            for line in events.read_text(encoding="utf-8").splitlines()
        )
    except (OSError, ValueError):
        return 0


def _count_pilot_processes(root: Path) -> int:
    events = root / "pilot_work" / "events.jsonl"
    if not events.is_file():
        return 0
    try:
        return sum(
            json.loads(line).get("process_started") is True
            for line in events.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("kind") == "agent_call_finished"
        )
    except (OSError, ValueError):
        return 0


def diagnose(provider: str) -> dict[str, Any]:
    """No inference; neither a model subscription nor a remote proxy is contacted."""
    codex = shutil.which("codex")
    status: dict[str, Any] = {
        "macos": platform.system() == "Darwin",
        "architecture": platform.machine(),
        "python_3_11_or_newer": _python_ready(),
        "codex_installed": codex is not None,
        "chatgpt_login_status": "not_checked",
        "provider_profile_usable": "not_verified",
        "model_usable": "not_verified",
        "model_calls": 0,
    }
    if codex and provider == "codex_chatgpt":
        status["chatgpt_login_status"] = (
            "authenticated" if _check_chatgpt_login(codex) else "not_authenticated"
        )
    if not status["macos"] or not status["python_3_11_or_newer"]:
        status["readiness"] = "blocked"
    elif not codex:
        status["readiness"] = "codex_missing"
    elif provider == "codex_chatgpt" and status["chatgpt_login_status"] != "authenticated":
        status["readiness"] = "login_required"
    else:
        status["readiness"] = "eligible_for_manual_probe"
    return status


def offline_smoke(root: Path) -> dict[str, Any]:
    work, vault = root / "offline_work", root / "offline_vault"
    output = _t1(
        "smoke",
        "--work",
        str(work),
        "--vault",
        str(vault),
        timeout=180,
    )
    reference = output["final"]["holdout_auroc"]["offline_baseline__wdbc__42"]
    return {
        "model_calls": 0,
        "training_attempts": len(output["trials"]),
        "offline_holdout_auroc": reference,
        "development_seconds": output["status"]["reserved_or_spent_seconds"],
        "qualification": "Offline data/algorithm preflight only; NOT a four-policy result",
    }


def one_cell(root: Path, provider: str, model: str, confirm: str, seed: int = 42) -> dict[str, Any]:
    """One independent agent-only development cell; never run two seeds in a job."""
    if seed not in (42, 137):
        raise MacRunBlocked("pilot cell seed must be one of the frozen qualification seeds")
    if confirm != "RUN_ONE_CELL":
        raise MacRunBlocked("one-cell agent invocation requires explicit RUN_ONE_CELL")
    codex = _codex()
    if provider == "codex_chatgpt" and not _check_chatgpt_login(codex):
        raise MacRunBlocked("Codex ChatGPT login not verified for this runner user")
    _model_name(model)
    work = root / "pilot_work"
    vault = root / "pilot_vault"
    _t1(
        "init",
        "--stage",
        "pilot",
        "--allow-agent-stage",
        "--work",
        str(work),
        "--vault",
        str(vault),
        timeout=90,
    )
    _run(
        [
            sys.executable,
            str(T1),
            "task",
            "--work",
            str(work),
            "--arm",
            "agent_only",
            "--dataset",
            "wdbc",
            "--seed",
            str(seed),
        ],
        timeout=20,
    )
    agent_dir = root / "agent_read_only_workspace"
    agent_dir.mkdir(mode=0o700)
    _run(["git", "init", "-q", str(agent_dir)], timeout=10)
    request = (
        "Select ONE safe hyperparameter candidate for a bounded experiment on Wisconsin "
        "Breast Cancer Diagnostic, binary classification. Optimize development AUROC; "
        "the held-out test remains sealed. Do not browse, run tools, execute commands, "
        "explain, or add markdown. Your entire answer MUST be a single JSON object. "
        "Choose either "
        '{"model":"logistic","feature_mode":"all","C":1.0,"class_weight":"none"} '
        "(C must be 0.01 to 10) or "
        '{"model":"forest","feature_mode":"all","n_estimators":64,'
        '"max_depth":7,"min_samples_leaf":2,"class_weight":"balanced"} '
        "(20 <= n_estimators <= 120, 2 <= max_depth <= 16, "
        "1 <= min_samples_leaf <= 10). "
        "feature_mode may be all or numeric_only, class_weight none or balanced. "
        "No other keys or text; this is a ONE-TRY pilot with a fixed budget."
    )
    response = root / "codex_response_PRIVATE.txt"
    command = _codex_command(codex, response)
    command.append(request)
    # The guard itself writes the attempt marker only after the OS starts Codex.
    before = time.monotonic()
    outcome = guarded_run(
        command,
        cwd=agent_dir,
        env=_redacted_agent_env(),
        timeout=MODEL_TIMEOUT,
        started_marker=root / "agent_attempt.started",
    )
    elapsed = time.monotonic() - before
    if outcome.returncode != 0 or not response.is_file():
        raise MacRunBlocked(
            "Codex call failed or selected model unavailable; do not silently fall back"
        )
    if response.stat().st_size > 4096:
        raise MacRunBlocked("model proposal exceeded 4 KiB")
    try:
        raw_response = response.read_text(encoding="utf-8")
        candidate, reply_format = parse_frozen_candidate_reply(raw_response)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise MacRunBlocked("model output has no unique allowlisted candidate JSON") from exc
    candidate_path = root / "selected_candidate_PRIVATE.json"
    candidate_path.write_text(json.dumps(candidate, sort_keys=True), encoding="utf-8")
    trial = _t1(
        "trial",
        "--work",
        str(work),
        "--arm",
        "agent_only",
        "--dataset",
        "wdbc",
        "--seed",
        str(seed),
        "--candidate",
        str(candidate_path),
        timeout=80,
    )
    if trial["exit_code"] != 0:
        raise MacRunBlocked("bounded development trial failed; do not record a success")
    submission = _t1(
        "submit",
        "--work",
        str(work),
        "--arm",
        "agent_only",
        "--dataset",
        "wdbc",
        "--seed",
        str(seed),
        timeout=20,
    )
    measured = validated_development_metrics(trial, submission, candidate, seed)
    return {
        "model_calls": 1,
        "model_attempt_wall_seconds": round(elapsed, 3),
        "peak_mac_memory_used_percent": outcome.peak_mac_used_percent,
        "minimum_mac_available_gib": outcome.minimum_mac_available_gib,
        "backend": provider,
        "requested_model": model,
        "model_reasoning_effort": REQUIRED_REASONING_EFFORT,
        "model_output_format": reply_format,
        "trial_completed": True,
        "seed": seed,
        "arm": "agent_only",
        **measured,
        "qualification": (
            "ONE agent_only development cell; no final test, no four-policy comparison. "
            "A Codex CLI invocation can issue multiple unobserved provider API requests."
        ),
    }


def validated_development_metrics(
    trial: dict[str, Any],
    submission: dict[str, Any],
    candidate: dict[str, Any],
    seed: int = 42,
) -> dict[str, object]:
    """Require real T1 run and submitted candidate to agree before reporting numbers."""
    score = trial.get("dev_auroc")
    seconds = trial.get("seconds")
    selected = submission.get("dev_auroc")
    run_id = trial.get("id")
    if (
        trial.get("exit_code") != 0
        or not isinstance(run_id, str)
        or not run_id.startswith("trial-")
        or submission.get("run_id") != run_id
        or submission.get("arm") != "agent_only"
        or submission.get("dataset") != "wdbc"
        or type(submission.get("seed")) is not int
        or submission.get("seed") != seed
    ):
        raise MacRunBlocked("submitted benchmark record disagrees with the real trial")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in (score, selected, seconds)
    ):
        raise MacRunBlocked("invalid development metric or runtime in T1 receipt")
    if not 0 <= score <= 1 or not 0 <= selected <= 1 or seconds < 0:
        raise MacRunBlocked("development metric or charged seconds out of valid range")
    if not math.isclose(score, selected, rel_tol=0, abs_tol=1e-12):
        raise MacRunBlocked("development AUROC differs between trial and submission")
    expected_sha = hashlib.sha256(canonical(candidate)).hexdigest()
    if submission.get("candidate_sha256") != expected_sha:
        raise MacRunBlocked("submitted candidate hash differs from frozen model selection")
    return {
        "trial_id": run_id,
        "candidate_sha256": expected_sha,
        "development_auroc": float(score),
        "development_cost_seconds": round(float(seconds), 6),
    }


def run(
    mode: str, provider: str, model: str, confirm: str, root: Path, seed: int = 42
) -> dict[str, Any]:
    if mode not in ALLOWED_MODES or provider not in ALLOWED_PROVIDERS:
        raise MacRunBlocked("unknown mode or provider")
    _model_name(model)
    if mode == "diagnose":
        return diagnose(provider)
    if platform.system() != "Darwin":
        raise MacRunBlocked("Mac research jobs are restricted to macOS")
    if not _python_ready():
        raise MacRunBlocked("Python 3.11+ is required")
    if mode == "resource_probe":
        sample = inspect_host()
        return {
            "model_calls": 0,
            "required_model": REQUIRED_MODEL,
            "total_memory_gib": sample.total_gib,
            "available_memory_gib": sample.available_gib,
            "minimum_available_gib": sample.required_free_gib,
            "host_memory_used_percent": sample.used_percent,
            "host_memory_limit_percent": sample.max_used_percent,
            "codex_launch_eligible": True,
            "resource_guard": "ready",
            "qualification": "Only global Mac RAM checked; no model/ML subprocess run",
        }
    if mode == "offline_smoke":
        return offline_smoke(root)
    if mode == "pilot_prepare":
        inspect_host()
        try:
            prepared = _t1(
                "init",
                "--stage",
                "pilot",
                "--allow-agent-stage",
                "--work",
                str(root / "pilot_work"),
                "--vault",
                str(root / "pilot_vault"),
                timeout=90,
            )
        except MacRunBlocked as exc:
            raise BenchmarkError("guarded Pilot preparation failed") from exc
        frozen = json.loads((root / "pilot_work" / "meta.json").read_text(encoding="utf-8"))
        return {
            **prepared,
            "code_revision": frozen["code_revision"],
            "execution_order_seed": frozen["execution_order_seed"],
            "execution_order": frozen["execution_order"],
            "per_cell_trial_budget_seconds": frozen["per_cell_trial_budget_seconds"],
            "required_development_seconds_for_all_cells": frozen[
                "required_development_seconds_for_all_cells"
            ],
            "model_call_cap_per_cell": frozen["profile"]["max_model_decision_calls_per_cell"],
            "holdout_materialized": False,
            "model_calls_started": 0,
        }
    if mode == "pilot":
        if confirm != "RUN_FOUR_POLICY_PILOT":
            raise MacRunBlocked("four-policy Pilot requires RUN_FOUR_POLICY_PILOT")
        inspect_host()
        codex = _codex()

        def pilot_t1_call(*args: str, timeout: int = 60) -> dict[str, Any]:
            try:
                return _t1(*args, timeout=timeout)
            except MacRunBlocked as exc:
                raise BenchmarkError("guarded local T1 command failed") from exc

        return run_pilot(
            root=root,
            codex=codex,
            confirm=confirm,
            guarded_call=guarded_run,
            t1_call=pilot_t1_call,
            inspect=inspect_host,
        )
    if confirm != "RUN_ONE_CELL":
        raise MacRunBlocked("one-cell agent invocation requires explicit RUN_ONE_CELL")
    # Refuse before paid inference if physical RAM use already exceeds 95%.
    # No per-process RSS, CPU, or process-count caps apply.
    inspect_host()
    return one_cell(root, provider, model, confirm, seed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=ALLOWED_MODES, required=True)
    parser.add_argument("--provider", choices=ALLOWED_PROVIDERS, default="codex_chatgpt")
    parser.add_argument("--model", choices=[REQUIRED_MODEL], default=REQUIRED_MODEL)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--seed", type=int, choices=[42, 137], default=42)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.results.absolute()
    if args.mode == "pilot":
        expected_entries = {"pilot_work", "pilot_vault", "prepare_summary.json"}
        if (
            not root.is_dir()
            or root.is_symlink()
            or {path.name for path in root.iterdir()} != expected_entries
        ):
            print(
                json.dumps(
                    {
                        "status": "blocked",
                        "mode": args.mode,
                        "safe_reason": (
                            "run pilot_prepare first and use its untouched results directory"
                        ),
                    }
                )
            )
            return 2
    else:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
    started = datetime.now(UTC).isoformat()
    began = time.monotonic()
    summary: dict[str, Any] = {
        "mode": args.mode,
        "provider": args.provider,
        "requested_model": args.model,
        "required_model": REQUIRED_MODEL,
        "model_reasoning_effort": REQUIRED_REASONING_EFFORT,
        "started_at_utc": started,
        "status": "blocked",
        "provider_request_count": "not_observable",
        "paid_provider_cost": "not_metered",
    }
    try:
        with single_runner_lock():
            outcome = run(args.mode, args.provider, args.model, args.confirm, root, args.seed)
        summary.update(outcome)
        summary["status"] = "completed"
        returncode = 0
    except (MacRunBlocked, ResourceRefusal, ValueError, KeyError, OSError) as exc:
        summary["safe_reason"] = str(exc)
        returncode = 2
    finally:
        summary["elapsed_wall_seconds"] = round(time.monotonic() - began, 3)
        pilot_calls = _count_pilot_calls(root)
        pilot_processes = _count_pilot_processes(root)
        one_cell_started = (root / "agent_attempt.started").exists()
        summary["model_attempt_started"] = bool(pilot_processes or one_cell_started)
        summary["model_call_attempts"] = pilot_calls
        summary["codex_process_attempts"] = pilot_processes + int(one_cell_started)
        summary_name = "prepare_summary.json" if args.mode == "pilot_prepare" else "summary.json"
        (root / summary_name).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "status": summary["status"],
                    "mode": args.mode,
                    "readiness": summary.get("readiness"),
                    "model_attempt_started": summary["model_attempt_started"],
                    "safe_reason": summary.get("safe_reason"),
                    "summary_json": str(root / summary_name),
                },
                ensure_ascii=False,
            )
        )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
