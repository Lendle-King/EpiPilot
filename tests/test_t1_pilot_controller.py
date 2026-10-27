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
    ledger,
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


def _conditional_v2_response(
    *, baseline: float = 0.8, candidate_auroc: float = 0.9
) -> dict[str, Any]:
    return {
        "decisions": [
            {
                "hypothesis": f"Estimate four outcomes for candidate {index}",
                "candidate": _candidate(c),
                "probabilities": [0.7, 0.1, 0.1, 0.1],
                "candidate_auroc_by_outcome": [
                    candidate_auroc,
                    baseline,
                    baseline,
                    candidate_auroc,
                ],
            }
            for index, c in enumerate((0.5, 2.0), 1)
        ]
    }


def test_conditional_v2_uses_auroc_evsi_and_baseline_fallback() -> None:
    baseline = _candidate(1.0)
    history = [
        {
            "run_id": "trial-0001",
            "candidate": baseline,
            "candidate_sha256": pilot_controller.digest(pilot_controller.canonical(baseline)),
            "dev_auroc": 0.8,
            "seconds": 0.2,
            "metric_sha256": "a" * 64,
            "role": "common_baseline",
        }
    ]
    candidate, decision = pilot_controller.choose_conditional_v2(
        _conditional_v2_response(), history, 1
    )
    assert candidate == _candidate(0.5)
    assert decision["utility_unit"].startswith("absolute development AUROC")
    assert decision["calibration_samples"] == 0
    assert decision["model_forecast_tables"][0]["effective_candidate_auroc_by_outcome"] == [
        0.9,
        0.8,
        0.8,
        0.8,
    ]
    assert decision["decision_scores"][0]["value_of_information"] == pytest.approx(0.07)
    assert decision["decision_scores"][0]["normalized_cost"] == pytest.approx(0.3)
    assert decision["decision_scores"][0]["risk_penalty"] == 0
    assert decision["decision_scores"][0]["net_value"] == pytest.approx(0.07)

    fallback, stopped = pilot_controller.choose_conditional_v2(
        _conditional_v2_response(baseline=0.8, candidate_auroc=0.7), history, 1
    )
    assert fallback == baseline
    assert stopped["fallback"] == "common_baseline"
    assert stopped["stopped_for_nonpositive_value"] is True


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


def test_protocol_v2_trains_common_baseline_and_evaluates_only_after_all_freeze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    root = tmp_path / "pilot-v2-results"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(mac.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mac, "_python_ready", lambda: True)
    monkeypatch.setattr(mac, "_codex", lambda: "/Applications/ChatGPT.app/.../codex")
    monkeypatch.setattr(mac, "inspect_host", lambda: None)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "b" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "b" * 40)

    call_purposes: defaultdict[tuple[str, int], list[str]] = defaultdict(list)
    model_active = 0
    development_workers = 0
    holdout_workers = 0
    holdout_visible = False

    def fake_guarded_call(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int, started_marker: Path
    ) -> Any:
        nonlocal model_active
        model_active += 1
        started_marker.write_text("started", encoding="utf-8")
        assert argv[argv.index("--model") + 1] == "gpt-6-luna"
        assert 'model_reasoning_effort="max"' in [
            argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-c"
        ]
        config_values = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-c"]
        assert "features.shell_tool=false" in config_values
        assert 'web_search="disabled"' in config_values
        assert "mcp_servers={}" in config_values
        assert "model_providers.cc-switch-official.request_max_retries=0" in config_values
        assert "model_providers.cc-switch-official.stream_max_retries=0" in config_values
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in argv and "--json" in argv
        assert timeout == 150
        assert "GITHUB_TOKEN" not in env and "SSH_AUTH_SOCK" not in env
        prompt = argv[-1]
        assert "pilot_vault" not in prompt and "private_test" not in prompt
        assert "Cell information (no train/dev rows or labels):\n" in prompt
        context = json.loads(
            prompt.split("Cell information (no train/dev rows or labels):\n", 1)[1]
        )
        arm, seed = context["strategy"], context["seed"]
        schema_path = Path(argv[argv.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if "note" in schema.get("properties", {}):
            verified = context["verified_trial"]
            response = {
                "note": (
                    f"On frozen split, run {verified['run_id']}, candidate sha256 "
                    f"{verified['candidate_sha256']}, development AUROC "
                    f"{verified['dev_auroc']:.6f}; scope=current cell only."
                )
            }
            call_purposes[(arm, seed)].append("memory_note")
        elif "candidate" in schema.get("properties", {}):
            response = {"candidate": _candidate(0.5 if arm == "agent_only" else 1.5)}
            call_purposes[(arm, seed)].append("decision")
        elif "proposals" in schema.get("properties", {}):
            response = _heuristic_response(1)
            call_purposes[(arm, seed)].append("decision")
        elif "decisions" in schema.get("properties", {}):
            response = _conditional_v2_response(candidate_auroc=0.99)
            call_purposes[(arm, seed)].append("decision")
        else:
            raise AssertionError("unknown protocol-v2 strict schema")
        Path(argv[argv.index("--output-last-message") + 1]).write_text(
            json.dumps(response), encoding="utf-8"
        )
        stdout = json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 111,
                    "output_tokens": 23,
                    "input_tokens_details": {"cached_tokens": 7},
                },
            }
        )
        model_active -= 1
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(mac, "guarded_run", fake_guarded_call)

    def fake_worker(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal development_workers, holdout_workers, holdout_visible
        eval_path = Path(argv[argv.index("--eval") + 1])
        work = root / "pilot_work"
        if eval_path.name == "test.csv":
            holdout_workers += 1
            assert model_active == 0
            events = ledger(work)
            assert len([row for row in events if row["kind"] == "cell_terminal"]) == 8
            assert len(list((work / "submissions").glob("*.json"))) == 8
            assert (work / "final.started").is_file()
            holdout_visible = any(
                eval_path.name in str(item)
                for row in events
                if row["kind"] == "strategy_decision"
                for item in row["decision"].values()
            )
        else:
            development_workers += 1
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"auroc":0.91}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(t1_module.subprocess, "run", fake_worker)

    def fake_t1(*args: str, timeout: int = 60) -> dict[str, Any]:
        command, values = args[0], list(args[1:])
        if command == "init":
            assert timeout == 90
            return setup(
                Path(values[values.index("--work") + 1]),
                Path(values[values.index("--vault") + 1]),
                "pilot_v2",
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
        if command == "finalize":
            assert timeout == 200
            return finalize(
                Path(values[values.index("--work") + 1]),
                Path(values[values.index("--vault") + 1]),
                Path(values[values.index("--audit") + 1]),
            )
        raise AssertionError(command)

    monkeypatch.setattr(mac, "_t1", fake_t1)
    prepared = mac.run("pilot_v2_prepare", "ccswitch_codex", "gpt-6-luna", "", root)
    assert prepared["protocol_version"] == 2
    assert prepared["holdout_materialized"] is False
    assert prepared["model_calls_started"] == 0
    assert prepared["execution_order_seed"] == 20271028
    outcome = mac.run(
        "pilot_v2",
        "ccswitch_codex",
        "gpt-6-luna",
        "RUN_T1_PILOT_V2",
        root,
    )
    assert outcome["pilot_run_status"] == "completed"
    assert outcome["cell_count_completed_or_terminal"] == 8
    assert outcome["training_trials"] == development_workers
    assert 8 <= outcome["training_trials"] <= 16
    assert holdout_workers == 10
    assert outcome["model_call_attempts"] <= 32
    assert outcome["model_call_usage"]["available_call_count"] == outcome["model_call_attempts"]
    assert outcome["model_call_usage"]["input_tokens"] == 111 * outcome["model_call_attempts"]
    assert outcome["holdout_evaluated"] is True
    assert len(outcome["final_evaluation"]["holdout_auroc"]) == 8
    assert outcome["final_evaluation"]["common_baseline_holdout_auroc"] == {
        "42": 0.91,
        "137": 0.91,
    }
    assert not holdout_visible
    assert all(
        cell["model_call_usage"]["available_call_count"] == cell["model_calls"]
        and cell["model_call_usage"]["missing_call_count"] == 0
        for cell in outcome["cells"]
    )
    events = ledger(root / "pilot_work")
    baseline_events = [row for row in events if row["kind"] == "common_baseline_validated"]
    assert len(baseline_events) == 8
    assert all(
        row["candidate_sha256"] == prepared["baseline_candidate_sha256"] for row in baseline_events
    )
    trial_counts: defaultdict[tuple[str, str, int], int] = defaultdict(int)
    call_counts: defaultdict[tuple[str, str, int], int] = defaultdict(int)
    for row in events:
        cell = (row.get("arm"), row.get("dataset"), row.get("seed"))
        if row["kind"] == "started":
            trial_counts[cell] += 1
            assert row["time_limit"] == 15
        elif row["kind"] == "agent_call_started":
            call_counts[cell] += 1
            assert row["model"] == "gpt-6-luna"
            assert row["model_reasoning_effort"] == "max"
    assert len(trial_counts) == 8 and all(1 <= count <= 2 for count in trial_counts.values())
    assert all(count <= 4 for count in call_counts.values())
    for cell, count in call_counts.items():
        expected = ["memory_note", "decision"] if cell[0] == "memory_only" else ["decision"]
        recorded_purposes = call_purposes[(cell[0], cell[2])]
        assert recorded_purposes == expected, (cell, count, recorded_purposes)
        assert count == len(expected), (cell, count, expected)


def test_protocol_v2_infrastructure_failure_submits_baseline_and_stops_next_cell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    root = tmp_path / "pilot-v2-failure"
    root.mkdir(mode=0o700)
    work, vault = root / "pilot_work", root / "pilot_vault"
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "c" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "c" * 40)
    setup(work, vault, "pilot_v2", {}, True)
    worker_calls = 0
    model_calls = 0

    def fake_worker(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal worker_calls
        worker_calls += 1
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"auroc":0.92}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(t1_module.subprocess, "run", fake_worker)

    def failed_cli(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int, started_marker: Path
    ) -> Any:
        nonlocal model_calls
        model_calls += 1
        started_marker.write_text("started", encoding="utf-8")
        error = {
            "type": "error",
            "error": {"type": "provider_error", "code": "unavailable", "message": "offline"},
        }
        return SimpleNamespace(returncode=1, stdout=json.dumps(error), stderr="offline")

    def t1_call(*args: str, timeout: int = 60) -> dict[str, Any]:
        command, values = args[0], list(args[1:])
        if command == "trial":
            return perform_trial(
                Path(values[values.index("--work") + 1]),
                values[values.index("--arm") + 1],
                values[values.index("--dataset") + 1],
                int(values[values.index("--seed") + 1]),
                Path(values[values.index("--candidate") + 1]),
            )
        raise AssertionError(f"unexpected T1 command: {command}")

    outcome = pilot_controller.run_pilot_v2(
        root=root,
        codex="codex",
        confirm="RUN_T1_PILOT_V2",
        guarded_call=failed_cli,
        t1_call=t1_call,
        inspect=lambda: None,
    )
    events = ledger(work)
    terminals = [row for row in events if row["kind"] == "cell_terminal"]
    assert outcome["pilot_run_status"] == "paused_for_infrastructure"
    assert len(terminals) == 1 and terminals[0]["outcome"] == "submitted"
    assert len([row for row in events if row["kind"] == "started"]) == 1
    assert len([row for row in events if row["kind"] == "agent_call_started"]) == 1
    assert model_calls == 1 and worker_calls == 1
    assert status(work)["submitted"] == 1
    assert status(work)["holdout_evaluated"] is False
    assert not (vault / "wdbc" / "test.csv").exists()


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
        output_rows = [
            {"type": "info", "message": "Initialize " + ("startup-noise " * 100)},
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "code": "bad_schema",
                    "param": "text.format.schema",
                    "message": (
                        "schema rejected; Authorization: Bearer literal-secret-token; "
                        "OPENAI_API_KEY=env-secret-value-for-redaction"
                    ),
                },
            },
            {
                "type": "turn.failed",
                "error": {
                    "type": "invalid_request_error",
                    "code": "bad_schema",
                    "message": "turn failed",
                },
            },
        ]
        return SimpleNamespace(
            returncode=73,
            stdout="\n".join(json.dumps(row) for row in output_rows),
            stderr=(
                "INITIAL_STDERR_PREFIX_ONLY"
                + ("x" * 2400)
                + "\nFINAL_DIAGNOSTIC: schema validation stopped; "
                + "Authorization: Bearer literal-secret-token; "
                + "OPENAI_API_KEY=env-secret-value-for-redaction"
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
    assert finished["stdout_event_types"] == ["info", "error", "turn.failed"]
    assert finished["stdout_error_events"] == [
        {
            "event_type": "error",
            "error_type": "invalid_request_error",
            "code": "bad_schema",
            "param": "text.format.schema",
            "message": (
                "schema rejected; Authorization: Bearer [REDACTED]; OPENAI_API_KEY=[REDACTED]"
            ),
        },
        {
            "event_type": "turn.failed",
            "error_type": "invalid_request_error",
            "code": "bad_schema",
            "message": "turn failed",
        },
    ]
    assert "literal-secret-token" not in finished["stderr_excerpt_redacted"]
    assert "env-secret-value-for-redaction" not in finished["stderr_excerpt_redacted"]
    assert "FINAL_DIAGNOSTIC: schema validation stopped" in finished["stderr_excerpt_redacted"]
    assert "INITIAL_STDERR_PREFIX_ONLY" not in finished["stderr_excerpt_redacted"]
    assert finished["stderr_excerpt_redacted"].count("[REDACTED") >= 2
    assert status(work)["model_call_attempts"] == 1
    assert status(work)["terminal_cells"] == 1


def test_guard_timeout_preserves_prior_candidate_and_allows_next_cell_inspection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    root = tmp_path / "pilot-timeout-results"
    root.mkdir(mode=0o700)
    work, vault = root / "pilot_work", root / "pilot_vault"
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "d" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "d" * 40)
    setup(work, vault, "pilot", {}, True)
    calls = 0
    inspections = 0

    def fake_model(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int, started_marker: Path
    ) -> Any:
        nonlocal calls
        calls += 1
        started_marker.write_text("started", encoding="utf-8")
        assert argv[argv.index("--model") + 1] == "gpt-6-luna"
        assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="max"'
        assert timeout == 150
        if calls == 2:
            raise pilot_controller.ResourceRefusal(
                "research subprocess exceeded fixed wall timeout"
            )
        schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_text())
        if "candidate" in schema.get("properties", {}):
            payload = {"candidate": _candidate(1.1)}
        elif "proposals" in schema.get("properties", {}):
            payload = _heuristic_response(1)
        elif "decisions" in schema.get("properties", {}):
            payload = _conditional_response()
        else:
            raise AssertionError("unexpected pilot schema")
        Path(argv[argv.index("--output-last-message") + 1]).write_text(
            json.dumps(payload), encoding="utf-8"
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                }
            ),
            stderr="",
        )

    def fake_worker(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"auroc":0.83}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(t1_module.subprocess, "run", fake_worker)

    def fake_t1(*args: str, timeout: int = 60) -> dict[str, Any]:
        assert args[0] == "trial" and timeout == 80
        values = list(args[1:])
        return perform_trial(
            Path(values[values.index("--work") + 1]),
            values[values.index("--arm") + 1],
            values[values.index("--dataset") + 1],
            int(values[values.index("--seed") + 1]),
            Path(values[values.index("--candidate") + 1]),
        )

    def inspect() -> None:
        nonlocal inspections
        inspections += 1
        if inspections == 2:
            raise pilot_controller.ResourceRefusal(
                "Mac whole-machine physical memory usage exceeds 95%"
            )

    result = pilot_controller.run_pilot(
        root=root,
        codex="/mock/codex",
        confirm="RUN_FOUR_POLICY_PILOT",
        guarded_call=fake_model,
        t1_call=fake_t1,
        inspect=inspect,
    )
    first = tuple(read_json(work / "meta.json")["execution_order"][0])
    events = [json.loads(row) for row in (work / "events.jsonl").read_text().splitlines()]
    call_finishes = [row for row in events if row["kind"] == "agent_call_finished"]
    terminal = next(row for row in events if row["kind"] == "cell_terminal")
    submission = read_json(work / "submissions" / f"{first[0]}__{first[1]}__{first[2]}.json")
    assert calls == 2 and inspections == 2
    assert call_finishes[-1]["outcome"] == "timeout"
    assert call_finishes[-1]["failure_classification"] == "guard_timeout"
    assert terminal["outcome"] == "submitted"
    assert "valid_prior_candidate_with_timeout" in terminal["reason"]
    assert submission["run_id"] == "trial-0001"
    assert result["isolated_timeouts"] == [
        {
            "arm": first[0],
            "dataset": first[1],
            "seed": first[2],
            "call_id": f"{first[0]}-{first[1]}-{first[2]}-call-2",
            "classification": "guard_timeout",
        }
    ]
    assert result["cell_count_completed_or_terminal"] == 1
    assert result["stopped_for_resource_guard"] is True
    assert result["training_trials"] == 1
    assert result["holdout_evaluated"] is False
    assert status(work)["model_call_attempts"] == 2


def test_guard_timeout_without_candidate_remains_failed_and_has_no_submission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    root = tmp_path / "pilot-timeout-no-candidate"
    root.mkdir(mode=0o700)
    work, vault = root / "pilot_work", root / "pilot_vault"
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "e" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "e" * 40)
    setup(work, vault, "pilot", {}, True)
    inspections = 0

    def timeout_model(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int, started_marker: Path
    ) -> Any:
        started_marker.write_text("started", encoding="utf-8")
        raise pilot_controller.ResourceRefusal("research subprocess exceeded fixed wall timeout")

    def no_training(*_args: str, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("a timed-out request cannot launch training")

    def inspect() -> None:
        nonlocal inspections
        inspections += 1
        if inspections == 2:
            raise pilot_controller.ResourceRefusal(
                "Mac whole-machine physical memory usage exceeds 95%"
            )

    result = pilot_controller.run_pilot(
        root=root,
        codex="/mock/codex",
        confirm="RUN_FOUR_POLICY_PILOT",
        guarded_call=timeout_model,
        t1_call=no_training,
        inspect=inspect,
    )
    first = tuple(read_json(work / "meta.json")["execution_order"][0])
    events = [json.loads(row) for row in (work / "events.jsonl").read_text().splitlines()]
    terminal = next(row for row in events if row["kind"] == "cell_terminal")
    assert terminal["outcome"] == "failed"
    assert "timeout_without_valid_prior_candidate" in terminal["reason"]
    assert not (work / "submissions" / f"{first[0]}__{first[1]}__{first[2]}.json").exists()
    assert result["training_trials"] == 0
    assert result["holdout_evaluated"] is False
    assert status(work)["model_call_attempts"] == 1


def test_cli_output_schemas_are_strict_and_preserve_candidate_semantics() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    supported_keywords = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "anyOf",
        "enum",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
        "items",
        "minLength",
        "maxLength",
        "format",
        "pattern",
    }
    unsupported_keywords = {
        "oneOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "dependentRequired",
        "dependentSchemas",
        "patternProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "propertyNames",
        "minProperties",
        "maxProperties",
        "contains",
        "minContains",
        "maxContains",
        "uniqueItems",
        "const",
    }

    def assert_strict_subset(node: Any) -> None:
        if isinstance(node, list):
            for value in node:
                assert_strict_subset(value)
            return
        if not isinstance(node, dict):
            return
        assert set(node).isdisjoint(unsupported_keywords)
        assert set(node) <= supported_keywords
        if "enum" in node:
            assert node.get("type") == "string"
        if node.get("type") == "object":
            properties = node.get("properties", {})
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(properties)
        for value in node.get("properties", {}).values():
            assert_strict_subset(value)
        if "items" in node:
            assert_strict_subset(node["items"])
        for branch in node.get("anyOf", []):
            assert_strict_subset(branch)

    for protocol_version in (1, 2):
        for arm in ("agent_only", "memory_only", "epipilot_heuristic", "epipilot_conditional"):
            for purpose in ("decision", "memory_note"):
                schema = pilot_controller._schema(arm, purpose, protocol_version=protocol_version)
                jsonschema.Draft202012Validator.check_schema(schema)
                assert_strict_subset(schema)

    assert set(pilot_controller.CandidateSchema) == {"anyOf"}
    assert [
        branch["properties"]["model"]["enum"]
        for branch in pilot_controller.CandidateSchema["anyOf"]
    ] == [
        ["logistic"],
        ["forest"],
    ]
    candidate_validator = jsonschema.Draft202012Validator(pilot_controller.CandidateSchema)
    allowed_candidates = (
        _candidate(1.25),
        {
            "model": "forest",
            "feature_mode": "numeric_only",
            "n_estimators": 40,
            "max_depth": 8,
            "min_samples_leaf": 2,
            "class_weight": "balanced",
        },
    )
    for candidate in allowed_candidates:
        assert candidate_validator.is_valid(candidate)
        assert t1_module.normalize_candidate(candidate)

    invalid_candidate = {**allowed_candidates[0], "n_estimators": 40}
    assert not candidate_validator.is_valid(invalid_candidate)
    with pytest.raises(t1_module.BenchmarkError, match="inconsistent parameter schema"):
        t1_module.normalize_candidate(invalid_candidate)

    v2_validator = jsonschema.Draft202012Validator(pilot_controller.CandidateSchemaV2)
    assert v2_validator.is_valid(_candidate(1.0))
    assert not v2_validator.is_valid({**_candidate(1.0), "feature_mode": "numeric_only"})
    assert t1_module.normalize_candidate(_candidate(1)) == _candidate(1.0)
    assert pilot_controller.digest(
        t1_module.canonical(t1_module.normalize_candidate(_candidate(1)))
    ) == pilot_controller.digest(
        t1_module.canonical(t1_module.normalize_candidate(_candidate(1.0)))
    )


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


def test_single_step_recovery_preserves_lineage_and_caps_call_and_training_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    origin = tmp_path / "T1_Pilot_Results"
    origin.mkdir(mode=0o700)
    origin_work, origin_vault = origin / "pilot_work", origin / "pilot_vault"
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "a" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "a" * 40)
    setup(origin_work, origin_vault, "pilot", {}, True)
    origin_meta = read_json(origin_work / "meta.json")
    for arm, dataset, seed in origin_meta["execution_order"]:
        call_id = f"{arm}-{dataset}-{seed}-call-1"
        record_agent_call_started(origin_work, arm, dataset, seed, call_id, "decision")
        record_agent_call_finished(
            origin_work,
            call_id,
            "failed",
            {"usage_available": False, "process_started": True, "turn_count": 0},
        )
        record_cell_terminal(origin_work, arm, dataset, seed, "failed", "origin failure")
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

    prior = tmp_path / "T1_Pilot_Recovery_1"
    prior.mkdir(mode=0o700)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "b" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "b" * 40)

    def setup_t1(*args: str, timeout: int = 60) -> dict[str, Any]:
        assert args[0] == "init" and timeout == 90
        values = list(args[1:])
        return setup(
            Path(values[values.index("--work") + 1]),
            Path(values[values.index("--vault") + 1]),
            "pilot",
            {},
            True,
        )

    pilot_controller.prepare_pilot_recovery(root=prior, origin_root=origin, t1_call=setup_t1)
    prior_work = prior / "pilot_work"
    target = ("memory_only", "wdbc", 42)
    second_id = "memory_only-wdbc-42-call-2"
    record_agent_call_started(prior_work, *target, second_id, "decision")
    record_agent_call_finished(
        prior_work,
        second_id,
        "failed",
        {"usage_available": False, "process_started": True, "turn_count": 0},
    )
    record_cell_terminal(prior_work, *target, "failed", "recovery failure")
    (prior / "summary.json").write_text(
        json.dumps(
            {
                "status": "paused_for_infrastructure",
                "pilot_run_status": "paused_for_infrastructure",
                "model_call_attempts": 9,
                "training_trials": 0,
                "holdout_evaluated": False,
            }
        ),
        encoding="utf-8",
    )

    recovery2 = tmp_path / "T1_Pilot_Recovery_2"
    recovery2.mkdir(mode=0o700)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "c" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "c" * 40)
    pilot_controller.prepare_pilot_single_step_recovery(
        root=recovery2,
        origin_root=origin,
        prior_root=prior,
        t1_call=setup_t1,
        arm=target[0],
        dataset=target[1],
        seed=target[2],
    )

    recovery2_work = recovery2 / "pilot_work"
    third_id = "memory_only-wdbc-42-call-3"
    record_agent_call_started(recovery2_work, *target, third_id, "decision")
    record_agent_call_finished(
        recovery2_work,
        third_id,
        "failed",
        {"usage_available": False, "process_started": True, "turn_count": 0},
    )
    record_cell_terminal(recovery2_work, *target, "failed", "second recovery failure")
    (recovery2 / "summary.json").write_text(
        json.dumps(
            {
                "status": "paused_for_infrastructure",
                "pilot_run_status": "paused_for_infrastructure",
                "model_call_attempts": 10,
                "training_trials": 0,
                "holdout_evaluated": False,
            }
        ),
        encoding="utf-8",
    )

    recovery3 = tmp_path / "T1_Pilot_Recovery_3"
    recovery3.mkdir(mode=0o700)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "d" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "d" * 40)
    memory_prepared = pilot_controller.prepare_pilot_single_step_recovery(
        root=recovery3,
        origin_root=origin,
        prior_root=recovery2,
        t1_call=setup_t1,
        arm=target[0],
        dataset=target[1],
        seed=target[2],
    )
    assert memory_prepared["prior_call_attempts"] == 10
    assert memory_prepared["target_prior_call_attempts"] == 3
    assert memory_prepared["remaining_total_call_budget"] == 22
    recovery3_work = recovery3 / "pilot_work"
    fourth_id = "memory_only-wdbc-42-call-4"
    record_agent_call_started(recovery3_work, *target, fourth_id, "decision")
    record_agent_call_finished(
        recovery3_work,
        fourth_id,
        "failed",
        {"usage_available": False, "process_started": True, "turn_count": 0},
    )
    (recovery3 / "summary.json").write_text(
        json.dumps(
            {
                "status": "paused_for_infrastructure",
                "pilot_run_status": "paused_for_infrastructure",
                "model_call_attempts": 11,
                "training_trials": 0,
                "holdout_evaluated": False,
            }
        ),
        encoding="utf-8",
    )

    recovery = tmp_path / "T1_Pilot_Recovery_4"
    recovery.mkdir(mode=0o700)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "e" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "e" * 40)
    step_target = ("agent_only", "wdbc", 42)
    prepared = pilot_controller.prepare_pilot_single_step_recovery(
        root=recovery,
        origin_root=origin,
        prior_root=recovery3,
        t1_call=setup_t1,
        arm=step_target[0],
        dataset=step_target[1],
        seed=step_target[2],
    )
    meta, work, vault = pilot_controller._load_frozen_pilot(recovery)
    assert prepared["prior_call_attempts"] == 11
    assert prepared["remaining_total_call_budget"] == 21
    assert prepared["target_prior_call_attempts"] == 1
    assert prepared["target_remaining_call_budget"] == 3
    assert meta["code_revision"] == "e" * 40
    assert sum(meta["recovery"]["prior_call_attempts_by_cell"].values()) == 11
    assert len(meta["recovery"]["prior_call_ids"]) == 11
    assert len(set(meta["recovery"]["prior_call_ids"])) == 11
    assert meta["recovery"]["recovery_ancestor_directories"] == [
        "T1_Pilot_Recovery_1",
        "T1_Pilot_Recovery_2",
        "T1_Pilot_Recovery_3",
    ]
    assert meta["recovery"]["prior_call_attempts_by_cell"]["memory_only__wdbc__42"] == 4
    assert meta["recovery"]["prior_call_attempts_by_cell"]["agent_only__wdbc__42"] == 1
    assert meta["recovery"]["single_step_target"] == {
        "arm": "agent_only",
        "dataset": "wdbc",
        "seed": 42,
    }
    assert not (vault / "wdbc" / "test.csv").exists()

    with pytest.raises(t1_module.BenchmarkError, match="duplicate model call identifier"):
        record_agent_call_started(work, *step_target, "agent_only-wdbc-42-call-1", "decision")

    model_calls: list[str] = []

    def fake_model(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int, started_marker: Path
    ) -> Any:
        model_calls.append(argv[argv.index("--model") + 1])
        started_marker.write_text("started", encoding="utf-8")
        assert argv[argv.index("--model") + 1] == "gpt-6-luna"
        assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="max"'
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in argv and "--json" in argv
        response_path = Path(argv[argv.index("--output-last-message") + 1])
        response_path.write_text(json.dumps({"candidate": _candidate(1.25)}), encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 18,
                        "input_tokens_details": {"cached_tokens": 12},
                    },
                }
            ),
            stderr="",
        )

    def step_t1(*args: str, timeout: int = 60) -> dict[str, Any]:
        assert args[0] == "trial" and timeout == 80
        values = list(args[1:])
        return perform_trial(
            Path(values[values.index("--work") + 1]),
            values[values.index("--arm") + 1],
            values[values.index("--dataset") + 1],
            int(values[values.index("--seed") + 1]),
            Path(values[values.index("--candidate") + 1]),
        )

    def fake_dev_worker(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"auroc":0.83}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(t1_module.subprocess, "run", fake_dev_worker)
    result = pilot_controller.run_pilot_single_step(
        root=recovery,
        codex="/mock/codex",
        confirm="RUN_ONE_PILOT_STEP",
        guarded_call=fake_model,
        t1_call=step_t1,
        inspect=lambda: None,
        arm=step_target[0],
        dataset=step_target[1],
        seed=step_target[2],
    )
    assert model_calls == ["gpt-6-luna"]
    assert result["model_calls_this_invocation"] == 1
    assert result["training_trials_this_invocation"] == 1
    assert result["candidate"] == _candidate(1.25)
    assert result["dev_auroc"] == 0.83
    assert result["training_seconds"] <= 15
    assert result["usage"]["usage_available"] is True
    assert result["usage"]["input_tokens"] == 120
    assert result["pilot_run_status"] == "checkpointed"
    assert read_json(work / "checkpoints" / "agent_only__wdbc__42-call-2.json")["status"] == (
        "trial_validated"
    )
    assert status(work)["model_call_attempts"] == 12
    assert status(work)["completed"] == 1
    assert status(work)["holdout_evaluated"] is False
    assert not (work / "final.started").exists()

    with pytest.raises(t1_module.BenchmarkError, match="bounded pilot_step mode"):
        pilot_controller.run_pilot(
            root=recovery,
            codex="/mock/codex",
            confirm="RUN_FOUR_POLICY_PILOT",
            guarded_call=fake_model,
            t1_call=step_t1,
            inspect=lambda: None,
        )
    assert model_calls == ["gpt-6-luna"]

    for ordinal in (3, 4):
        call_id = f"agent_only-wdbc-42-call-{ordinal}"
        assert record_agent_call_started(work, *step_target, call_id, "decision") == ordinal
        record_agent_call_finished(work, call_id, "failed", {"usage_available": False})
    with pytest.raises(
        t1_module.BenchmarkError, match="per-cell model decision-call budget exhausted"
    ):
        record_agent_call_started(work, *step_target, "agent_only-wdbc-42-call-5", "decision")
    assert status(work)["model_call_attempts"] == 14


def test_serial_recovery_imports_verified_successful_trial_without_retraining(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    origin = tmp_path / "T1_Pilot_Results"
    origin.mkdir(mode=0o700)
    origin_work, origin_vault = origin / "pilot_work", origin / "pilot_vault"
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "a" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "a" * 40)
    setup(origin_work, origin_vault, "pilot", {}, True)
    origin_meta = read_json(origin_work / "meta.json")
    for arm, dataset, seed in origin_meta["execution_order"]:
        call_id = f"{arm}-{dataset}-{seed}-call-1"
        record_agent_call_started(origin_work, arm, dataset, seed, call_id, "decision")
        record_agent_call_finished(
            origin_work,
            call_id,
            "failed",
            {"usage_available": False, "process_started": True, "turn_count": 0},
        )
        record_cell_terminal(origin_work, arm, dataset, seed, "failed", "origin failure")
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

    def setup_t1(*args: str, timeout: int = 60) -> dict[str, Any]:
        assert args[0] == "init" and timeout == 90
        values = list(args[1:])
        return setup(
            Path(values[values.index("--work") + 1]),
            Path(values[values.index("--vault") + 1]),
            "pilot",
            {},
            True,
        )

    first = tuple(origin_meta["execution_order"][0])
    prior = tmp_path / "T1_Pilot_Recovery_1"
    prior.mkdir(mode=0o700)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "b" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "b" * 40)
    pilot_controller.prepare_pilot_recovery(root=prior, origin_root=origin, t1_call=setup_t1)
    prior_work = prior / "pilot_work"
    prior_call = f"{first[0]}-{first[1]}-{first[2]}-call-2"
    record_agent_call_started(prior_work, *first, prior_call, "decision")
    record_agent_call_finished(
        prior_work,
        prior_call,
        "success",
        {"usage_available": True, "process_started": True, "turn_count": 1},
    )
    original_candidate = _candidate(0.75)
    candidate_input = prior / "candidate.json"
    candidate_input.write_bytes(t1_module.canonical(original_candidate))
    scores = iter((0.91, 0.84))
    worker_calls: list[str] = []

    def fake_dev_worker(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"auroc": next(scores)}), encoding="utf-8")
        worker_calls.append(output_path.parent.name)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(t1_module.subprocess, "run", fake_dev_worker)
    prior_trial = perform_trial(prior_work, *first, candidate_input)
    candidate_input.unlink()
    assert prior_trial["id"] == "trial-0001"
    (prior / "summary.json").write_text(
        json.dumps(
            {
                "status": "checkpointed",
                "model_call_attempts": 9,
                "training_trials": 1,
                "holdout_evaluated": False,
            }
        ),
        encoding="utf-8",
    )

    recovery = tmp_path / "T1_Pilot_Recovery_2"
    recovery.mkdir(mode=0o700)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "c" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "c" * 40)
    prepared = pilot_controller.prepare_pilot_single_step_recovery(
        root=recovery,
        origin_root=origin,
        prior_root=prior,
        t1_call=setup_t1,
        arm=first[0],
        dataset=first[1],
        seed=first[2],
        serial_resume=True,
    )
    meta, work, vault = pilot_controller._load_frozen_pilot(recovery)
    key = f"{first[0]}__{first[1]}__{first[2]}"
    assert prepared["prior_call_attempts"] == 9
    assert prepared["prior_training_attempts"] == 1
    assert prepared["prior_successful_trials"] == 1
    assert meta["recovery"]["prior_training_attempts_by_cell"][key] == 1
    assert meta["recovery"]["prior_successful_trials_by_cell"][key][0]["dev_auroc"] == 0.91
    assert status(work)["completed"] == 1
    assert not (vault / "wdbc" / "test.csv").exists()
    history, _notes = pilot_controller._single_step_history(work, *first)
    assert len(history) == 1
    assert history[0]["candidate"] == original_candidate
    assert history[0]["dev_auroc"] == 0.91

    provider_calls = 0

    def fake_serial_model(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: int, started_marker: Path
    ) -> Any:
        nonlocal provider_calls
        provider_calls += 1
        started_marker.write_text("started", encoding="utf-8")
        prompt = argv[-1]
        context_text = prompt.split("Cell information (no train/dev rows or labels):\n", 1)[1]
        context = json.loads(context_text)
        strategy = context["strategy"]
        if "verified_trial" in context:
            verified = context["verified_trial"]
            payload = {
                "note": (
                    f"On frozen split, run {verified['run_id']}, candidate sha256 "
                    f"{verified['candidate_sha256']}, development AUROC "
                    f"{verified['dev_auroc']:.6f}; scope=current cell only."
                )
            }
        elif strategy == "epipilot_heuristic":
            payload = _heuristic_response(provider_calls)
        elif strategy == "epipilot_conditional":
            payload = _conditional_response()
        elif strategy == "agent_only" and context["seed"] == 137:
            payload = {"candidate": {"model": "outside-allowlist"}}
        else:
            payload = {"candidate": _candidate(0.3 + provider_calls / 10)}
        response_path = Path(argv[argv.index("--output-last-message") + 1])
        response_path.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 80,
                        "output_tokens": 20,
                        "input_tokens_details": {"cached_tokens": 0},
                    },
                }
            ),
            stderr="",
        )

    score_counter = 1

    def fake_serial_worker(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal score_counter
        score_counter += 1
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({"auroc": 0.7 + score_counter / 100}), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(t1_module.subprocess, "run", fake_serial_worker)
    result = pilot_controller.run_pilot(
        root=recovery,
        codex="/mock/codex",
        confirm="RUN_FOUR_POLICY_PILOT",
        guarded_call=fake_serial_model,
        t1_call=lambda *args, timeout=60: perform_trial(
            Path(list(args)[list(args).index("--work") + 1]),
            list(args)[list(args).index("--arm") + 1],
            list(args)[list(args).index("--dataset") + 1],
            int(list(args)[list(args).index("--seed") + 1]),
            Path(list(args)[list(args).index("--candidate") + 1]),
        ),
        inspect=lambda: None,
    )
    assert result["cell_count_completed_or_terminal"] == 8
    assert result["model_call_attempts"] <= 32
    assert result["model_call_attempts"] == 9 + provider_calls
    assert result["pilot_run_status"] == "incomplete_missing_cells"
    assert result["holdout_evaluated"] is False
    assert result["final_evaluation"] is None
    assert not (work / "final.started").exists()
    assert status(work)["submitted"] == 7
    assert status(work)["completed"] >= 2
    assert worker_calls.count("trial-0001") == 1


def test_serial_resume_preserves_valid_candidate_after_timeout_and_skips_terminal_cell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("sklearn")
    pytest.importorskip("pandas")
    origin = tmp_path / "T1_Pilot_Results"
    origin.mkdir(mode=0o700)
    origin_work, origin_vault = origin / "pilot_work", origin / "pilot_vault"
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "a" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "a" * 40)
    setup(origin_work, origin_vault, "pilot", {}, True)
    origin_meta = read_json(origin_work / "meta.json")
    for arm, dataset, seed in origin_meta["execution_order"]:
        call_id = f"{arm}-{dataset}-{seed}-call-1"
        record_agent_call_started(origin_work, arm, dataset, seed, call_id, "decision")
        record_agent_call_finished(
            origin_work,
            call_id,
            "failed",
            {"usage_available": False, "process_started": True, "turn_count": 0},
        )
        record_cell_terminal(origin_work, arm, dataset, seed, "failed", "origin request failed")
    (origin / "summary.json").write_text(
        json.dumps(
            {
                "status": "paused_for_infrastructure",
                "model_call_attempts": 8,
                "training_trials": 0,
                "holdout_evaluated": False,
            }
        ),
        encoding="utf-8",
    )

    def setup_t1(*args: str, timeout: int = 60) -> dict[str, Any]:
        assert args[0] == "init" and timeout == 90
        values = list(args[1:])
        return setup(
            Path(values[values.index("--work") + 1]),
            Path(values[values.index("--vault") + 1]),
            "pilot",
            {},
            True,
        )

    prior = tmp_path / "T1_Pilot_Recovery_1"
    prior.mkdir(mode=0o700)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "b" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "b" * 40)
    pilot_controller.prepare_pilot_recovery(root=prior, origin_root=origin, t1_call=setup_t1)
    prior_work = prior / "pilot_work"
    first = tuple(origin_meta["execution_order"][0])
    second = tuple(origin_meta["execution_order"][1])
    timeout_call_id = f"{first[0]}-{first[1]}-{first[2]}-call-2"
    record_agent_call_started(prior_work, *first, timeout_call_id, "decision")
    record_agent_call_finished(
        prior_work,
        timeout_call_id,
        "timeout",
        {
            "usage_available": False,
            "process_started": True,
            "turn_count": 0,
            "exit_code": None,
            "failure_classification": "guard_timeout",
            "stderr_excerpt_redacted": "research subprocess exceeded fixed wall timeout",
            "stdout_event_types": [],
            "stdout_error_events": [],
            "elapsed_seconds": 150.2254,
        },
    )

    def fake_worker(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        output_path = Path(argv[argv.index("--output") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text('{"auroc":0.87}', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(t1_module.subprocess, "run", fake_worker)
    candidate_path = prior / "candidate.json"
    candidate_path.write_bytes(t1_module.canonical(_candidate(1.25)))
    trial = perform_trial(prior_work, *first, candidate_path)
    candidate_path.unlink()
    prior_submission = select(prior_work, *first)
    record_cell_terminal(
        prior_work,
        *first,
        "failed",
        "whole-Mac resource guard reported timeout after a validated prior trial",
    )
    (prior / "summary.json").write_text(
        json.dumps(
            {
                "status": "paused_for_resource_guard",
                "model_call_attempts": 9,
                "training_trials": 1,
                "holdout_evaluated": False,
            }
        ),
        encoding="utf-8",
    )

    recovery = tmp_path / "T1_Pilot_Recovery_2"
    recovery.mkdir(mode=0o700)
    monkeypatch.setattr(t1_module, "_code_revision", lambda: "c" * 40)
    monkeypatch.setattr(pilot_controller, "_code_revision", lambda: "c" * 40)
    prepared = pilot_controller.prepare_pilot_single_step_recovery(
        root=recovery,
        origin_root=origin,
        prior_root=prior,
        t1_call=setup_t1,
        arm=second[0],
        dataset=second[1],
        seed=second[2],
        serial_resume=True,
    )
    pilot_controller._load_frozen_pilot(recovery)
    recovery_work = recovery / "pilot_work"
    first_key = f"{first[0]}__{first[1]}__{first[2]}"
    audit = prepared["prior_terminal_audit"][first_key]
    terminal = next(
        row
        for row in ledger(recovery_work)
        if row.get("kind") == "cell_terminal"
        and (row.get("arm"), row.get("dataset"), row.get("seed")) == first
    )
    recovered_submission = read_json(recovery_work / "submissions" / f"{first_key}.json")
    assert prepared["prior_call_attempts"] == 9
    assert prepared["remaining_total_call_budget"] == 23
    assert prepared["resume_start_cell"] == list(second)
    assert prepared["resume_policy_deviation"].startswith(
        "isolated model-call timeouts remain failed"
    )
    assert audit["classification"] == "valid_prior_candidate_with_timeout"
    assert audit["source_submission"]["valid"] is True
    assert audit["source_submission"]["artifact_sha256"] == t1_module.file_digest(
        prior_work / "submissions" / f"{first_key}.json"
    )
    assert audit["source_submission"]["run_id"] == prior_submission["run_id"] == trial["id"]
    assert audit["source_submission"]["candidate_sha256"] == prior_submission["candidate_sha256"]
    assert terminal["outcome"] == "submitted"
    assert "valid_prior_candidate_with_timeout" in terminal["reason"]
    assert recovered_submission["candidate_sha256"] == prior_submission["candidate_sha256"]
    assert not any(
        row.get("kind") == "cell_terminal"
        and (row.get("arm"), row.get("dataset"), row.get("seed")) == second
        for row in ledger(recovery_work)
    )
    assert status(recovery_work)["model_call_attempts"] == 9
    assert status(recovery_work)["holdout_evaluated"] is False
    assert not (recovery / "pilot_vault" / "wdbc" / "test.csv").exists()
