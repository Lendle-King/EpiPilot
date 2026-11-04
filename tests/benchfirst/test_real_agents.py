from pathlib import Path

import pytest

from epipilot.benchfirst.real_agents import RealTask, aide_command, codex_command, summarize_codex


def task() -> RealTask:
    return RealTask(
        task_id="uci-bike-hourly-calibration",
        objective="Minimize development RMSE",
        dataset_citation="Fanaee-T (2013), doi:10.24432/C5W894",
        data_license="CC-BY-4.0",
        public_files={
            "train.csv": "a" * 64,
            "dev.csv": "b" * 64,
            "test.csv": "c" * 64,
            "candidate.py": "d" * 64,
        },
        training_seconds=600,
        steps=2,
    )


def test_real_commands_preserve_native_search_and_use_quota_gateway() -> None:
    cmd = codex_command(task(), "test-model")
    assert cmd[:2] == ("codex", "exec")
    assert "--json" in cmd and "--ignore-user-config" in cmd
    assert any("request_max_retries=0" in x for x in cmd)
    assert "aide" not in " ".join(cmd)
    aide = aide_command(task(), "test-model")
    assert aide[:4] == ("python", "-m", "epipilot.benchfirst.real_agents", "aide")
    assert "agent.steps=2" in aide
    assert not any("native_smoke" in x for x in aide)


def test_task_rejects_unsafe_paths_and_missing_data() -> None:
    with pytest.raises(ValueError):
        task().model_copy(update={"public_files": {"../labels": "a" * 64}}).validate_paths()


def test_codex_receipt_has_native_edits_commands_and_usage(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text(
        '{"type":"item.completed","item":{"type":"file_change","changes":[]}}\n'
        '{"type":"item.completed","item":{"type":"command_execution","exit_code":0}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":30,"output_tokens":4}}\n'
    )
    receipt = summarize_codex(log)
    assert receipt.file_change_events == 1 and receipt.command_events == 1
    assert receipt.input_tokens == 30 and receipt.output_tokens == 4
    assert receipt.api_request_count is None
