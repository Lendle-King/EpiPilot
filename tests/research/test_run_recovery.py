"""Durable controlled-run receipt sidecar recovery after interrupted CLI calls."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_research_plugin import setup_store
from test_run_receipt import cli, prepare

from epipilot.core.models import utc_now
from epipilot.research.models import RunLaunch
from epipilot.research.runner import execute_frozen_runner, receipt_file


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "controlled"
    path.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "epipilot@example.invalid"],
        ["config", "user.name", "EpiPilot Tests"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "config.json").write_text('{"seed":42}', encoding="utf-8")
    (path / "data.json").write_text('{"split":"train"}', encoding="utf-8")
    script = (
        "from pathlib import Path\n"
        "target = Path('results/metrics.json')\n"
        "target.parent.mkdir(exist_ok=True)\n"
        "target.write_text('{\\\"metrics\\\":{\\\"score\\\":0.9}}', encoding='utf-8')\n"
    )
    (path / "run_trial.py").write_text(script, encoding="utf-8")
    (path / "runner.json").write_text(
        json.dumps({"argv": [sys.executable, "run_trial.py"], "timeout_seconds": 20}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "frozen study"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    setup_store(path)
    return path


def test_reconcile_finished_sidecar_without_rerunning_process(root: Path) -> None:
    store, contract_id = prepare(root)
    contract = store.load()[0].contracts[contract_id]
    launch = RunLaunch(id="RUN-crash-window", contract_id=contract_id, started_at=utc_now())
    store.add("runner_started", launch)
    receipt = execute_frozen_runner(root, contract, launch)
    assert receipt_file(root, launch.id).exists()
    assert contract_id not in store.load()[0].run_receipts
    assert cli(root, "reconcile", "--contract", contract_id) == 0
    state, _ = store.load()
    assert state.run_receipts[contract_id] == receipt
    assert cli(root, "reconcile", "--contract", contract_id) == 0
    assert cli(root, "execute", "--contract", contract_id) == 2
    assert (
        cli(
            root,
            "record",
            "--contract",
            contract_id,
            "--summary",
            "Recovered actual controlled output",
            "--outcome",
            "improved",
            "--cost",
            "0.5",
        )
        == 0
    )
    exp_id = next(iter(store.load()[0].experiments))
    assert cli(root, "verify", "--experiment", exp_id) == 0


def test_reconcile_rejects_changed_artifact_without_promoting_receipt(root: Path) -> None:
    store, contract_id = prepare(root)
    contract = store.load()[0].contracts[contract_id]
    launch = RunLaunch(id="RUN-invalid-artifact", contract_id=contract_id, started_at=utc_now())
    store.add("runner_started", launch)
    execute_frozen_runner(root, contract, launch)
    (root / contract.artifact_ref).write_text('{"metrics":{"score":0.98}}', encoding="utf-8")
    assert cli(root, "reconcile", "--contract", contract_id) == 2
    assert not store.load()[0].run_receipts


def test_reconcile_missing_sidecar_is_fail_closed(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, contract_id = prepare(root)
    launch = RunLaunch(id="RUN-running-unknown", contract_id=contract_id, started_at=utc_now())
    store.add("runner_started", launch)
    assert cli(root, "reconcile", "--contract", contract_id) == 2
    assert not store.load()[0].run_receipts
    assert cli(root, "iterate") == 0
    # The CLI describes uncertainty without incorrectly starting a new run.
    assert "runner_unreconciled" in str(capsys.readouterr().out)


def test_invalid_sidecar_schema_is_rejected(root: Path) -> None:
    store, contract_id = prepare(root)
    launch = RunLaunch(id="RUN-bad-sidecar", contract_id=contract_id, started_at=utc_now())
    store.add("runner_started", launch)
    dest = receipt_file(root, launch.id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"contract_id": contract_id}), encoding="utf-8")
    assert cli(root, "reconcile", "--contract", contract_id) == 2
    assert not store.load()[0].run_receipts
