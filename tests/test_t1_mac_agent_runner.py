"""Mac T1 dispatcher fail-closed checks; never requires Codex or paid inference in CI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from experiments.researchbench_t1 import mac_agent_runner as mac


def test_model_selection_rejects_shell_or_unrecognized_chars() -> None:
    assert mac._model_name("gpt-6-luna(max)") == "gpt-6-luna(max)"
    for model in ("", "default", "gpt-6", "gpt-6-luna max", "$(id)", "\nmodel", " "):
        with pytest.raises(mac.MacRunBlocked):
            mac._model_name(model)


def test_diagnose_is_no_inference_and_reports_unverified_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mac.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mac.shutil, "which", lambda binary: None)
    report = mac.run("diagnose", "ccswitch_codex", "gpt-6-luna(max)", "", Path("/unused"))
    assert report["model_calls"] == 0
    assert report["model_usable"] == "not_verified"
    assert report["readiness"] == "codex_missing"


def test_one_cell_requires_explicit_authorization_before_any_agent_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden() -> str:
        raise AssertionError("Codex should never be contacted before explicit opt-in")

    monkeypatch.setattr(mac, "_codex", forbidden)
    with pytest.raises(mac.MacRunBlocked, match="RUN_ONE_CELL"):
        mac.one_cell(tmp_path, "codex_chatgpt", mac.REQUIRED_MODEL, "")


def test_offline_smoke_uses_only_local_t1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    commands = []

    def fake_t1(*args: str, timeout: int = 0) -> dict[str, object]:
        commands.append(args[0])
        assert timeout <= 180
        return {
            "final": {"holdout_auroc": {"offline_baseline__wdbc__42": 0.8}},
            "trials": [{}, {}],
            "status": {"reserved_or_spent_seconds": 4.0},
        }

    monkeypatch.setattr(mac, "_t1", fake_t1)
    monkeypatch.setattr(mac, "_codex", lambda: (_ for _ in ()).throw(AssertionError))
    result = mac.offline_smoke(tmp_path)
    assert commands == ["smoke"]
    assert result["model_calls"] == 0
    assert result["training_attempts"] == 2


@pytest.mark.parametrize("seed", [42, 137])
def test_one_cell_never_executes_model_proposed_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, seed: int
) -> None:
    model_calls = []
    stages = []

    monkeypatch.setattr(mac, "_codex", lambda: "/opt/homebrew/bin/codex")
    monkeypatch.setattr(mac, "_check_chatgpt_login", lambda binary: True)

    candidate_sha = hashlib.sha256(
        mac.canonical(
            {"model": "logistic", "feature_mode": "all", "C": 1.0, "class_weight": "none"}
        )
    ).hexdigest()

    def fake_t1(*args: str, timeout: int = 0) -> dict[str, object]:
        stages.append(args[0])
        if args[0] in {"task", "trial", "submit"}:
            assert args[args.index("--seed") + 1] == str(seed)
        if args[0] == "trial":
            return {
                "id": "trial-0001",
                "exit_code": 0,
                "seconds": 2.0,
                "dev_auroc": 0.87654,
            }
        if args[0] == "submit":
            return {
                "run_id": "trial-0001",
                "arm": "agent_only",
                "dataset": "wdbc",
                "seed": seed,
                "dev_auroc": 0.87654,
                "candidate_sha256": candidate_sha,
            }
        return {}

    def fake_run(argv: list[str], **kwargs: object) -> str:
        if "task" in argv:
            return "Optimize held-out-free development AUROC, return only candidate JSON"
        return ""

    def fake_codex(argv: list[str], **kwargs: object) -> SimpleNamespace:
        model_calls.append((argv, kwargs))
        output = Path(argv[argv.index("--output-last-message") + 1])
        output.write_text(
            '{"model":"logistic","feature_mode":"all","C":1.0,"class_weight":"none"}',
            encoding="utf-8",
        )
        return SimpleNamespace(
            returncode=0, peak_mac_used_percent=47.5, minimum_mac_available_gib=12.6
        )

    monkeypatch.setattr(mac, "_t1", fake_t1)
    monkeypatch.setattr(mac, "_run", fake_run)
    monkeypatch.setattr(mac, "guarded_run", fake_codex)
    result = mac.one_cell(tmp_path, "codex_chatgpt", "gpt-6-luna(max)", "RUN_ONE_CELL", seed)
    assert stages == ["init", "trial", "submit"]
    assert len(model_calls) == 1
    argv, kwargs = model_calls[0]
    assert argv[0].endswith("codex")
    assert argv[1:4] == ["exec", "--sandbox", "read-only"]
    assert argv[argv.index("--model") + 1] == mac.REQUIRED_MODEL
    assert kwargs["timeout"] == mac.MODEL_TIMEOUT
    assert "shell" not in kwargs
    assert result["model_calls"] == 1
    assert result["trial_completed"] is True
    assert result["model_output_format"] == "strict_json"
    assert result["development_auroc"] == 0.87654
    assert result["development_cost_seconds"] == 2.0
    assert result["peak_mac_memory_used_percent"] == 47.5
    assert result["minimum_mac_available_gib"] == 12.6
    assert result["trial_id"] == "trial-0001"
    assert result["seed"] == seed
    assert result["arm"] == "agent_only"
    assert result["candidate_sha256"] == candidate_sha


def test_bad_model_reply_cannot_trigger_trial_or_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []

    monkeypatch.setattr(mac, "_codex", lambda: "codex")
    monkeypatch.setattr(mac, "_check_chatgpt_login", lambda binary: True)

    def fake_t1(*args: str, timeout: int = 0) -> dict[str, object]:
        calls.append(args[0])
        return {}

    def fake_run(argv: list[str], **kwargs: object) -> str:
        return "propose one candidate"

    def fake_codex(argv: list[str], **kwargs: object) -> SimpleNamespace:
        dest = Path(argv[argv.index("--output-last-message") + 1])
        dest.write_text('{"model":"exec","command":"touch /tmp/unsafe"}', encoding="utf-8")
        return SimpleNamespace(
            returncode=0, peak_mac_used_percent=47.5, minimum_mac_available_gib=12.6
        )

    monkeypatch.setattr(mac, "_t1", fake_t1)
    monkeypatch.setattr(mac, "_run", fake_run)
    monkeypatch.setattr(mac, "guarded_run", fake_codex)
    with pytest.raises(mac.MacRunBlocked, match="unique allowlisted candidate"):
        mac.one_cell(tmp_path, "codex_chatgpt", mac.REQUIRED_MODEL, "RUN_ONE_CELL")
    assert calls == ["init"]


def test_denied_mode_records_sanitized_receipt_without_paid_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mac.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mac, "_codex", lambda: (_ for _ in ()).throw(AssertionError))
    output = tmp_path / "receipt"
    assert (
        mac.main(
            [
                "--mode",
                "pilot_one_cell",
                "--provider",
                "codex_chatgpt",
                "--model",
                "gpt-6-luna(max)",
                "--results",
                str(output),
            ]
        )
        == 2
    )
    data = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert data["status"] == "blocked"
    assert data["codex_process_attempts"] == 0
    assert data["provider_request_count"] == "not_observable"
    assert "RUN_ONE_CELL" in data["safe_reason"]


def test_environment_scrubs_github_and_ssh_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "not-to-be-inherited")
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "not-to-be-inherited")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/key-agent")
    environment = mac._redacted_agent_env()
    assert "GITHUB_TOKEN" not in environment
    assert "ACTIONS_RUNTIME_TOKEN" not in environment
    assert "SSH_AUTH_SOCK" not in environment
    assert environment["CI"] == "1"


def test_candidate_reply_is_strictly_allowlisted_even_with_wrappers() -> None:
    raw = '{"model":"logistic","feature_mode":"all","C":1.0,"class_weight":"none"}'
    for candidate_text, expected_mode in (
        (raw, "strict_json"),
        ("```json\n" + raw + "\n```", "fenced_json"),
        ("The best bounded setting is: " + raw, "embedded_json"),
    ):
        candidate, mode = mac.parse_frozen_candidate_reply(candidate_text)
        assert candidate["model"] == "logistic"
        assert mode == expected_mode
    for bad in (
        "",
        "I cannot run this task.",
        raw + "\n" + raw,
        '{"model":"exec","command":"touch /tmp/unsafe"}',
        raw + ("x" * 5000),
    ):
        with pytest.raises(ValueError):
            mac.parse_frozen_candidate_reply(bad)


def test_candidate_parser_rejects_nonfinite_numeric_parameters() -> None:
    with pytest.raises(ValueError):
        mac.parse_frozen_candidate_reply(
            '{"model":"logistic","feature_mode":"all","C":NaN,"class_weight":"none"}'
        )


def test_trial_and_submission_metrics_fail_closed_on_inconsistent_provenance() -> None:
    candidate = {"model": "logistic", "feature_mode": "all", "C": 1.0, "class_weight": "none"}
    frozen_hash = hashlib.sha256(mac.canonical(candidate)).hexdigest()
    trial = {
        "id": "trial-0001",
        "exit_code": 0,
        "seconds": 1.2,
        "dev_auroc": 0.824,
    }
    submission = {
        "arm": "agent_only",
        "dataset": "wdbc",
        "seed": 42,
        "run_id": "trial-0001",
        "candidate_sha256": frozen_hash,
        "dev_auroc": 0.824,
    }
    report = mac.validated_development_metrics(trial, submission, candidate)
    assert report["development_cost_seconds"] == 1.2
    assert report["development_auroc"] == 0.824
    assert report["candidate_sha256"] == frozen_hash
    for key, bad_value in (
        ("run_id", "trial-9999"),
        ("candidate_sha256", "0" * 64),
        ("dev_auroc", 0.98),
        ("seed", 99),
    ):
        corrupted = {**submission, key: bad_value}
        with pytest.raises(mac.MacRunBlocked):
            mac.validated_development_metrics(trial, corrupted, candidate)
    for key, bad_value in (("seconds", -1), ("dev_auroc", float("nan"))):
        corrupted = {**trial, key: bad_value}
        with pytest.raises(mac.MacRunBlocked):
            mac.validated_development_metrics(corrupted, submission, candidate)


def test_pilot_cannot_contact_codex_when_mac_memory_usage_over_95_percent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(mac.platform, "system", lambda: "Darwin")

    def refuse(*args: object, **kwargs: object) -> object:
        raise mac.ResourceRefusal("Mac whole-machine physical memory usage exceeds 95%")

    monkeypatch.setattr(mac, "inspect_host", refuse)
    monkeypatch.setattr(mac, "one_cell", lambda *args: (_ for _ in ()).throw(AssertionError))
    with pytest.raises(mac.ResourceRefusal, match="exceeds 95%"):
        mac.run(
            "pilot_one_cell", "codex_chatgpt", mac.REQUIRED_MODEL,
            "RUN_ONE_CELL", tmp_path, 137
        )

def test_different_model_is_rejected_before_codex_or_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        mac, "inspect_host", lambda: (_ for _ in ()).throw(AssertionError("probed"))
    )
    monkeypatch.setattr(
        mac, "one_cell", lambda *args: (_ for _ in ()).throw(AssertionError("Codex"))
    )
    for model in ("default", "gpt-6", "gpt-6-luna", "gpt-6-luna max"):
        with pytest.raises(mac.MacRunBlocked, match="only the exact Codex model"):
            mac.run("pilot_one_cell", "codex_chatgpt", model, "RUN_ONE_CELL", tmp_path)
