import json
from pathlib import Path

import pytest

from epipilot.benchfirst.offline import MockBudget, digest
from epipilot.benchfirst.s0 import S0Config, reconcile_s0, run_s0


def config() -> dict[str, object]:
    base = Path(__file__).resolve().parents[2]
    sample = json.loads((base / "experiments/benchmark_first/s0_example.json").read_text())
    arms = [a for a in sample["adapters"] if a["id"] != "autoresearch"]
    return {
        "schema_version": 1,
        "model": "mock-only",
        "budget": MockBudget().model_dump(),
        "images": {a["id"]: "sha256:" + "a" * 64 for a in arms},
        "limits": {},
        "evaluator_sha256": digest(base / "src/epipilot/benchfirst/offline.py"),
        "task": {
            "task_id": "synthetic-linear",
            "seed": 17,
            "row_ids": ["a"],
            "x": [1],
            "dev_y": [2],
        },
        "adapters": arms,
    }


def test_paid_transport_unknown_arms_and_evaluator_change_fail_closed(tmp_path: Path) -> None:
    payload = config()
    payload["model"] = "paid-provider"
    with pytest.raises(ValueError):
        S0Config.model_validate(payload)
    payload = config()
    payload["evaluator_sha256"] = "f" * 64
    file = tmp_path / "config.json"
    file.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="evaluator SHA"):
        run_s0(file, tmp_path, tmp_path / "output")


def test_crash_reconciliation_keeps_missing_cells_and_never_retries(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "config.json").write_text(json.dumps(config()))
    (output / "aide_ml").mkdir()
    (output / "aide_ml/prepared.json").write_text("{}")
    target = reconcile_s0(tmp_path, output)
    contents = target.read_text()
    assert "interrupted_unknown_outcome_no_retry" in contents
    assert "not_started_no_refill" in contents
    assert reconcile_s0(tmp_path, output).read_text() == contents
    with pytest.raises(ValueError, match="overwritten"):
        run_s0(output / "config.json", tmp_path, output)
