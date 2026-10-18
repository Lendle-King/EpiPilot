"""Real-file receipt checks reject self-report, tampering and stale executions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from epipilot.workspace.algorithm_execution import (
    ExecutionRequest,
    LocalAlgorithmExecutor,
    verify_execution,
)


def receipt_fixture(repo: Path) -> Path:
    folder = repo / "results/v3/round_26_route26"
    folder.mkdir(parents=True)
    daily = folder / "candidate_daily.csv"
    daily.write_text(
        "date,return,equity,turnover\n2020-01-02,0,100,0\n"
        "2020-01-03,0.1,110,1\n2020-01-06,-0.1,99,0\n",
        encoding="utf-8",
    )
    artifact = {
        "path": str(daily.relative_to(repo)),
        "sha256": hashlib.sha256(daily.read_bytes()).hexdigest(),
    }
    receipt = folder / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "route_run_id": "route26",
                "round_index": 26,
                "algorithm_id": "dual_momentum",
                "started_at_utc": datetime.now(UTC).isoformat(),
                "finished_at_utc": datetime.now(UTC).isoformat(),
                "scope": "exploratory_public_history",
                "initial_cash": 100,
                "input_files": [artifact],
                "artifacts": [artifact],
                "metrics": [
                    {
                        "variant": "candidate",
                        "cost_bps": 5,
                        "daily_path": artifact["path"],
                        "cagr": 0.99**84 - 1,
                        "max_drawdown": -0.1,
                        "annualized_turnover": 84.0,
                    }
                ],
                "protocol": "Synthetic fixed protocol, no forward evidence.",
            }
        ),
        encoding="utf-8",
    )
    payload = json.loads(receipt.read_text())
    payload["metrics"].append({**payload["metrics"][0], "variant": "baseline"})
    receipt.write_text(json.dumps(payload))
    return receipt


def test_receipt_recomputes_metrics_from_real_daily_artifact(tmp_path: Path) -> None:
    path = receipt_fixture(tmp_path)
    receipt = verify_execution(tmp_path, path, "route26", 26)
    assert receipt.metrics[0].max_drawdown == pytest.approx(-0.1)


def test_uncommitted_adapter_is_rejected_before_subprocess(tmp_path: Path) -> None:
    import subprocess
    import sys

    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/run_epipilot_algorithm_round.py").write_text("raise AssertionError()")
    with pytest.raises(ValueError, match="committed and clean"):
        LocalAlgorithmExecutor(tmp_path, Path(sys.executable)).execute(
            ExecutionRequest(route_run_id="test", round_index=26, algorithm_id="dual_momentum")
        )


def test_receipt_requires_measured_baseline_and_candidate(tmp_path: Path) -> None:
    path = receipt_fixture(tmp_path)
    payload = json.loads(path.read_text())
    payload["metrics"] = payload["metrics"][:1]
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="baseline"):
        verify_execution(tmp_path, path, "route26", 26)


@pytest.mark.parametrize("mutation", ["hash", "metric", "route", "path", "future", "equity"])
def test_receipt_rejects_invalid_evidence(tmp_path: Path, mutation: str) -> None:
    path = receipt_fixture(tmp_path)
    payload = json.loads(path.read_text())
    if mutation == "hash":
        payload["artifacts"][0]["sha256"] = "0" * 64
    elif mutation == "metric":
        payload["metrics"][0]["cagr"] = 0.15
    elif mutation == "route":
        payload["route_run_id"] = "old-route"
    elif mutation == "path":
        payload["artifacts"][0]["path"] = "../outside.csv"
    elif mutation == "future":
        payload["finished_at_utc"] = "2999-01-01T00:00:00+00:00"
    else:
        daily = tmp_path / payload["artifacts"][0]["path"]
        daily.write_text(daily.read_text().replace(",99,", ",199,"))
        digest = hashlib.sha256(daily.read_bytes()).hexdigest()
        payload["artifacts"][0]["sha256"] = digest
        payload["input_files"][0]["sha256"] = digest
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        verify_execution(tmp_path, path, "route26", 26)
