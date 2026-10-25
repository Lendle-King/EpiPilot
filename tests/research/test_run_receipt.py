"""Integration checks for frozen argv execution and result lineage."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_research_plugin import setup_store

from epipilot.core.models import utc_now
from epipilot.research.cli import main
from epipilot.research.models import ContractStatus, RunLaunch
from epipilot.research.store import ResearchStore


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


def cli(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def prepare(root: Path) -> tuple[ResearchStore, str]:
    assert (
        cli(
            root,
            "preregister",
            "--proposal",
            "P",
            "--baseline",
            "baseline-v1",
            "--config",
            "config.json",
            "--dataset",
            "data.json",
            "--control",
            "matched seed",
            "--decision-rule",
            "score >= 0.8 supports the bounded improvement",
            "--falsification",
            "score < 0.8 refutes the bounded improvement",
            "--metric-rule",
            "score:at_least:0.8",
            "--artifact",
            "results/metrics.json",
            "--max-cost",
            "1",
            "--runner-spec",
            "runner.json",
            "--verifier",
            sys.executable,
            "-m",
            "json.tool",
            "{artifact}",
        )
        == 0
    )
    store = ResearchStore(root / ".research")
    contract_id = next(iter(store.load()[0].contracts))
    assert cli(root, "start", "--contract", contract_id) == 0
    return store, contract_id


def test_controlled_run_receipt_and_verified_experiment(root: Path) -> None:
    store, contract_id = prepare(root)
    assert cli(root, "execute", "--contract", contract_id) == 0
    state, _ = store.load()
    receipt = state.run_receipts[contract_id]
    assert receipt.id == state.run_launches[contract_id].id
    assert receipt.exit_code == 0
    assert receipt.wall_seconds >= 0
    assert receipt.argv == state.contracts[contract_id].runner_argv
    metrics_file = root / "results/metrics.json"
    assert receipt.artifact_sha256 == hashlib.sha256(metrics_file.read_bytes()).hexdigest()
    assert (
        cli(
            root,
            "record",
            "--contract",
            contract_id,
            "--summary",
            "Controlled benchmark",
            "--outcome",
            "improved",
            "--cost",
            "0.5",
            "--metric",
            "score=0.9",
        )
        == 0
    )
    exp_id = next(iter(store.load()[0].experiments))
    assert store.load()[0].experiments[exp_id].run_receipt_id == receipt.id
    assert cli(root, "verify", "--experiment", exp_id) == 0
    assert store.load()[0].contracts[contract_id].status is ContractStatus.COMPLETED


def test_mutating_controlled_artifact_blocks_record_or_verification(root: Path) -> None:
    store, contract_id = prepare(root)
    assert cli(root, "execute", "--contract", contract_id) == 0
    path = root / "results/metrics.json"
    original = path.read_bytes()
    path.write_text('{"metrics":{"score":0.99}}', encoding="utf-8")
    assert (
        cli(
            root,
            "record",
            "--contract",
            contract_id,
            "--summary",
            "Tampered",
            "--outcome",
            "improved",
            "--cost",
            "0.2",
        )
        == 2
    )
    assert not store.load()[0].experiments
    path.write_bytes(original)
    assert (
        cli(
            root,
            "record",
            "--contract",
            contract_id,
            "--summary",
            "Actual controlled run",
            "--outcome",
            "improved",
            "--cost",
            "0.2",
        )
        == 0
    )
    exp_id = next(iter(store.load()[0].experiments))
    path.write_text('{"metrics":{"score":0.99}}', encoding="utf-8")
    assert cli(root, "verify", "--experiment", exp_id) == 2
    assert store.load()[0].contracts[contract_id].status is ContractStatus.BLOCKED


def test_runner_does_not_relaunch_or_overwrite_existing_metrics(root: Path) -> None:
    store, contract_id = prepare(root)
    path = root / "results/metrics.json"
    path.parent.mkdir()
    path.write_text('{"metrics":{"score":0.8}}', encoding="utf-8")
    assert cli(root, "execute", "--contract", contract_id) == 2
    assert not store.load()[0].run_launches
    path.unlink()
    assert cli(root, "execute", "--contract", contract_id) == 0
    assert cli(root, "execute", "--contract", contract_id) == 2
    assert len(store.load()[0].run_receipts) == 1


def test_unfinished_runner_requires_manual_reconciliation(root: Path) -> None:
    store, contract_id = prepare(root)
    store.add(
        "runner_started",
        RunLaunch(id="RUN-interrupted", contract_id=contract_id, started_at=utc_now()),
    )
    assert cli(root, "execute", "--contract", contract_id) == 2
    assert store.load()[0].run_launches[contract_id].id == "RUN-interrupted"
    assert (
        cli(
            root,
            "abort",
            "--contract",
            contract_id,
            "--reason",
            "Interrupted process; reconciled with operator",
            "--cost",
            "0.2",
        )
        == 0
    )
    assert store.load()[0].contracts[contract_id].status is ContractStatus.ABORTED


def test_runner_failure_can_be_accounted_without_fake_success(root: Path) -> None:
    runner_spec = root / "runner.json"
    runner_spec.write_text(
        json.dumps(
            {"argv": [sys.executable, "-c", "import sys;sys.exit(7)"], "timeout_seconds": 10}
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "runner.json"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "negative runner case"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    store, contract_id = prepare(root)
    assert cli(root, "execute", "--contract", contract_id) == 2
    assert store.load()[0].run_receipts[contract_id].exit_code == 7
    assert (
        cli(
            root,
            "record",
            "--contract",
            contract_id,
            "--summary",
            "Runner failed",
            "--outcome",
            "infrastructure_failure",
            "--cost",
            "0.4",
        )
        == 0
    )
    assert store.load()[0].contracts[contract_id].status is ContractStatus.BLOCKED
