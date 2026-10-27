from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from experiments.researchbench_t1 import mac_agent_runner as mac
from experiments.researchbench_t1 import pilot_controller
from experiments.researchbench_t1 import t1 as t1_module
from experiments.researchbench_t1.t1 import (
    finalize,
    perform_trial,
    read_json,
    record_agent_call_finished,
    record_agent_call_started,
    record_cell_terminal,
    select,
    setup,
    status,
)


def _candidate(c: float) -> dict[str, Any]:
    return {"model": "logistic", "feature_mode": "all", "C": c, "class_weight": "none"}


def _heuristic_response(call: int) -> dict[str, Any]:
    proposals = []
    for index, c in enumerate((0.6 + call * 0.1, 1.3 + call * 0.1), 1):
        proposals.append(
            {
                "hypothesis": f"Compare bounded root candidate {index} against current history",
                "candidate": _candidate(c),
                "kind": "explore" if index == 1 else "optimize",
                "information_gain": 0.8 if index == 1 else 0.3,
                "expected_improvement": 0.4 if index == 1 else 0.8,
                "unlock_value": 0.2,
                "novelty": 0.7 if index == 1 else 0.2,
                "risk": 0.05,
                "followup_hypothesis": f"Check a bounded follow-up for root candidate {index}",
                "followup_candidate": _candidate(c + 0.2),
                "followup_kind": "optimize",
                "followup_information_gain": 0.3,
                "followup_expected_improvement": 0.5,
                "followup_unlock_value": 0.2,
                "followup_novelty": 0.3,
                "followup_risk": 0.1,
            }
        )
    return {"proposals": proposals}


def _conditional_response() -> dict[str, Any]:
    decisions = []
    utilities = [
        {"stop": 0.0, "alternative": 1.0},
        {"stop": 0.0, "alternative": 1.0},
        {"stop": 1.0, "alternative": 0.0},
        {"stop": 1.0, "alternative": 0.0},
    ]
    for index, c in enumerate((1.0, 0.5), 1):
        decisions.append(
            {
                "hypothesis": f"Estimate four outcomes before candidate {index}",
                "candidate": _candidate(c),
                "risk": 0.0,
                "probabilities": [0.25, 0.25, 0.25, 0.25],
                "branch_utilities": utilities,
            }
        )
    return {"decisions": decisions}


def test_formal_pilot_entry_runs_all_strategies_and_freezes_before_holdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    root = tmp_path / "pilot-results"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(mac.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mac, "_python_ready", lambda: True)
    monkeypatch.setattr(mac, "_codex", lambda: "/Applications/ChatGPT.app/.../codex")
    monkeypatch.setattr(mac, "inspect_host", lambda: None)

    decisions: defaultdict[tuple[str, int], int] = defaultdict(int)
    dev_worker_calls = 0
    final_worker_calls = 0
    holdout_visible_to_model: list[bool] = []
    active_model_calls = 0

    def fake_guarded_call(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int, started_marker: Path
    ) -> Any:
        nonlocal active_model_calls
        active_model_calls += 1
        started_marker.write_text("started", encoding="utf-8")
        assert argv[argv.index("--model") + 1] == "gpt-6-luna"
        assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="max"'
        config_values = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "-c"]
        assert "features.shell_tool=false" in config_values
        assert 'web_search="disabled"' in config_values
        assert "mcp_servers={}" in config_values
        assert "model_providers.cc-switch-official.request_max_retries=0" in config_values
        assert "model_providers.cc-switch-official.stream_max_retries=0" in config_values
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in argv and "--json" in argv
        assert argv[argv.index("--output-schema") + 1].endswith("schema.json")
        assert timeout == 150
        assert "GITHUB_TOKEN" not in env and "SSH_AUTH_SOCK" not in env
        prompt = argv[-1]
        assert "pilot_vault" not in prompt and "final/" not in prompt
        assert "holdout remains sealed" in prompt or "holdout" in prompt
        holdout_visible_to_model.append(False)
        context = json.loads(
            prompt.split("Cell information (no train/dev rows or labels):\n", 1)[1]
        )
        arm, seed = context["strategy"], context["seed"]
        schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
        if "note" in schema.get("properties", {}):
            verified = context["verified_trial"]
            note = (
                f"On frozen split, run {verified['run_id']}, candidate sha256 "
                f"{verified['candidate_sha256']}, "
                f"development AUROC {verified['dev_auroc']:.6f}; scope=current cell only."
            )
            response = {"note": note}
        elif "candidate" in schema.get("properties", {}):
            decisions[(arm, seed)] += 1
            response = {"candidate": _candidate(1.0 if decisions[(arm, seed)] == 1 else 0.5)}
        elif "proposals" in schema.get("properties", {}):
            decisions[(arm, seed)] += 1
            response = _heuristic_response(decisions[(arm, seed)])
        elif "decisions" in schema.get("properties", {}):
            response = _conditional_response()
        else:
            raise AssertionError("unknown frozen response schema")
        Path(argv[argv.index("--output-last-message") + 1]).write_text(
            json.dumps(response), encoding="utf-8"
        )
        stdout = json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "input_tokens_details": {"cached_tokens": 10},
                },
            }
        )
        active_model_calls -= 1
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(mac, "guarded_run", fake_guarded_call)

    def fake_worker(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal dev_worker_calls, final_worker_calls
        evaluation_path = Path(argv[argv.index("--eval") + 1])
        if evaluation_path.name == "test.csv":
            final_worker_calls += 1
            assert active_model_calls == 0
            work = root / "pilot_work"
            assert len(list((work / "submissions").glob("*.json"))) == 8
            assert not any(holdout_visible_to_model)
        else:
            dev_worker_calls += 1
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"auroc":0.91}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(t1_module.subprocess, "run", fake_worker)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "a" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "a" * 40)

    def fake_t1(*args: str, timeout: int = 60) -> dict[str, Any]:
        command, values = args[0], list(args[1:])
        if command == "init":
            assert timeout == 90
            return setup(
                Path(values[values.index("--work") + 1]),
                Path(values[values.index("--vault") + 1]),
                "pilot",
                {},
                True,
            )
        if command == "trial":
            return perform_trial(
                Path(values[values.index("--work") + 1]),
                values[values.index("--arm") + 1],
                values[values.index("--dataset") + 1],
                int(values[values.index("--seed") + 1]),
                Path(values[values.index("--candidate") + 1]),
            )
        if command == "submit":
            return select(
                Path(values[values.index("--work") + 1]),
                values[values.index("--arm") + 1],
                values[values.index("--dataset") + 1],
                int(values[values.index("--seed") + 1]),
            )
        if command == "finalize":
            assert timeout == 200
            return finalize(
                Path(values[values.index("--work") + 1]),
                Path(values[values.index("--vault") + 1]),
                Path(values[values.index("--audit") + 1]),
            )
        raise AssertionError(command)

    monkeypatch.setattr(mac, "_t1", fake_t1)
    prepared = mac.run("pilot_prepare", "ccswitch_codex", "gpt-6-luna", "", root)
    assert prepared["holdout_materialized"] is False
    assert prepared["model_calls_started"] == 0
    assert prepared["code_revision"] == "a" * 40
    outcome = mac.run(
        "pilot",
        "ccswitch_codex",
        "gpt-6-luna",
        "RUN_FOUR_POLICY_PILOT",
        root,
    )
    assert outcome["cell_count_completed_or_terminal"] == 8
    assert outcome["training_trials"] == dev_worker_calls
    assert final_worker_calls == 8
    assert outcome["model_call_attempts"] <= 32
    assert outcome["codex_process_starts"] == outcome["model_call_attempts"]
    assert outcome["model_call_usage"]["available_call_count"] == outcome["model_call_attempts"]
    assert outcome["model_call_usage"]["input_tokens"] == 100 * outcome["model_call_attempts"]
    assert outcome["holdout_evaluated"] is True
    assert len(outcome["final_evaluation"]["holdout_auroc"]) == 8
    assert not holdout_visible_to_model or all(not value for value in holdout_visible_to_model)
    events = [
        json.loads(line) for line in (root / "pilot_work" / "events.jsonl").read_text().splitlines()
    ]
    call_rows = [row for row in events if row["kind"] == "agent_call_started"]
    call_counts: defaultdict[tuple[str, str, int], int] = defaultdict(int)
    for row in call_rows:
        call_counts[(row["arm"], row["dataset"], row["seed"])] += 1
        assert row["model"] == "gpt-6-luna"
        assert row["model_reasoning_effort"] == "max"
    assert all(count <= 4 for count in call_counts.values())
    trial_counts: defaultdict[tuple[str, str, int], int] = defaultdict(int)
    for row in events:
        if row["kind"] == "started":
            trial_counts[(row["arm"], row["dataset"], row["seed"])] += 1
            assert row["time_limit"] == 15
    assert len(trial_counts) == 8 and all(count <= 2 for count in trial_counts.values())
    strategy_rows = [
        row["decision"]["selection_policy"] for row in events if row["kind"] == "strategy_decision"
    ]
    assert any(value.endswith("rank_proposals") for value in strategy_rows)
    assert any(value.endswith("conditional')") for value in strategy_rows)
    assert outcome["manual_interventions"] == 0
    assert outcome["unsupported_claims"] == 0


def test_nonzero_cli_exit_is_redacted_and_pauses_after_first_cell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    root = tmp_path / "pilot-results"
    root.mkdir(mode=0o700)
    work, vault = root / "pilot_work", root / "pilot_vault"
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "c" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "c" * 40)
    setup(work, vault, "pilot", {}, True)
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret-value-for-redaction")
    call_count = 0

    def failed_cli(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int, started_marker: Path
    ) -> Any:
        nonlocal call_count
        call_count += 1
        started_marker.write_text("started", encoding="utf-8")
        assert argv[argv.index("--model") + 1] == "gpt-6-luna"
        assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="max"'
        assert timeout == 150
        assert env["OPENAI_API_KEY"] == "env-secret-value-for-redaction"
        return SimpleNamespace(
            returncode=73,
            stdout='{"type":"error"}\n',
            stderr=(
                "Authorization: Bearer literal-secret-token; "
                "OPENAI_API_KEY=env-secret-value-for-redaction"
            ),
        )

    def unexpected_trial(*_args: str, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("a failed infrastructure call must not start training")

    result = pilot_controller.run_pilot(
        root=root,
        codex="/mock/codex",
        confirm="RUN_FOUR_POLICY_PILOT",
        guarded_call=failed_cli,
        t1_call=unexpected_trial,
        inspect=lambda: None,
    )
    events = [
        json.loads(row) for row in (work / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    finished = next(row for row in events if row["kind"] == "agent_call_finished")
    assert call_count == 1
    assert result["pilot_run_status"] == "paused_for_infrastructure"
    assert result["training_trials"] == 0
    assert result["holdout_evaluated"] is False
    assert finished["exit_code"] == 73
    assert finished["failure_classification"] == "cli_nonzero_exit"
    assert finished["stdout_event_types"] == ["error"]
    assert "literal-secret-token" not in finished["stderr_excerpt_redacted"]
    assert "env-secret-value-for-redaction" not in finished["stderr_excerpt_redacted"]
    assert finished["stderr_excerpt_redacted"].count("[REDACTED") >= 2
    assert status(work)["model_call_attempts"] == 1
    assert status(work)["terminal_cells"] == 1


def test_cli_output_schemas_are_valid_json_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    for arm in ("agent_only", "memory_only", "epipilot_heuristic", "epipilot_conditional"):
        for purpose in ("decision", "memory_note"):
            jsonschema.Draft202012Validator.check_schema(pilot_controller._schema(arm, purpose))


def test_recovery_manifest_links_prior_failures_and_cumulative_call_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    origin = tmp_path / "T1_Pilot_Results"
    origin.mkdir(mode=0o700)
    origin_work, origin_vault = origin / "pilot_work", origin / "pilot_vault"
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "a" * 40)
    setup(origin_work, origin_vault, "pilot", {}, True)
    origin_meta = read_json(origin_work / "meta.json")
    for arm, dataset, seed in origin_meta["execution_order"]:
        call_id = f"{arm}-{dataset}-{seed}-call-1"
        record_agent_call_started(origin_work, arm, dataset, seed, call_id, "decision")
        record_agent_call_finished(
            origin_work,
            call_id,
            "failed",
            {
                "usage_available": False,
                "process_started": True,
                "turn_count": 0,
                "tool_event_count": 0,
            },
        )
        record_cell_terminal(origin_work, arm, dataset, seed, "failed", "synthetic origin failure")
    (origin / "summary.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "model_call_attempts": 8,
                "training_trials": 0,
                "holdout_evaluated": False,
            }
        ),
        encoding="utf-8",
    )

    recovery = tmp_path / "T1_Pilot_Recovery"
    recovery.mkdir(mode=0o700)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "b" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "b" * 40)

    def fake_t1(*args: str, timeout: int = 60) -> dict[str, Any]:
        assert args[0] == "init" and timeout == 90
        values = list(args[1:])
        return setup(
            Path(values[values.index("--work") + 1]),
            Path(values[values.index("--vault") + 1]),
            "pilot",
            {},
            True,
        )

    prepared = pilot_controller.prepare_pilot_recovery(
        root=recovery, origin_root=origin, t1_call=fake_t1
    )
    meta, recovery_work, _recovery_vault = pilot_controller._load_frozen_pilot(recovery)
    first_arm, first_dataset, first_seed = meta["execution_order"][0]
    call_id = f"{first_arm}-{first_dataset}-{first_seed}-call-2"
    ordinal = record_agent_call_started(
        recovery_work, first_arm, first_dataset, first_seed, call_id, "decision"
    )
    assert ordinal == 2
    assert prepared["prior_call_attempts"] == 8
    assert prepared["remaining_total_call_budget"] == 24
    assert sum(meta["recovery"]["prior_call_attempts_by_cell"].values()) == 8
    assert sum(meta["recovery"]["prior_call_attempts_by_cell"].values()) + ordinal - 1 == 9
    assert status(recovery_work)["model_call_attempts"] == 9
