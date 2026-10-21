"""The phase-grid adapter is isolated, code-pinned and returns typed measurements."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.research.measurement import Measured
from epipilot.research.models import Metric
from epipilot.workspace.phase_execution import (
    PHASE_COMPARISON_ADAPTER_ID,
    SubprocessPhaseComparisonComputer,
    phase_comparison_computers,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _contract(root: Path) -> MeasurementContract:
    _git(root, "init", "--quiet")
    phase = root / "phase.csv"
    events = root / "events.csv"
    phase.write_text("phase fixture\n", encoding="utf-8")
    events.write_text("event fixture\n", encoding="utf-8")
    source = root / "src/quant_etf/carts_phase_computer.py"
    source.parent.mkdir(parents=True)
    source.write_text("pinned phase implementation\n", encoding="utf-8")
    _git(root, "add", "phase.csv", "events.csv", "src")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    revision = _git(root, "rev-parse", "HEAD")
    return MeasurementContract(
        proposal_id="phase-grid-test",
        purpose=Purpose.EXPLORE,
        adapter="quant_etf_phase_comparison_v1",
        git_revision=revision,
        inputs=(
            InputFile(path="phase.csv", sha256=hashlib.sha256(phase.read_bytes()).hexdigest()),
            InputFile(path="events.csv", sha256=hashlib.sha256(events.read_bytes()).hexdigest()),
        ),
        rules=(Rule(metric="lag1_minus_lag0_fee5_cagr", minimum=0.003),),
    )


def test_phase_computer_runs_only_the_registered_adapter_and_parses_typed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    result = Measured(metrics=(Metric(name="lag1_minus_lag0_fee5_cagr", value=0.01),), samples=1800)
    calls: list[tuple[list[str], dict[str, object]]] = []
    real_run = subprocess.run

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] == "git":
            return real_run(args, **kwargs)  # type: ignore[arg-type]
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=result.model_dump_json(), stderr="")

    monkeypatch.setattr("epipilot.workspace.phase_execution.subprocess.run", run)
    computer = SubprocessPhaseComparisonComputer(
        Path(sys.executable), Path(__file__).parents[1] / "src"
    )

    measured = computer.measure(tmp_path, contract)

    assert measured.metrics == result.metrics
    assert measured.samples == result.samples
    assert measured.details[0].startswith("epipilot_adapter_sha256=")
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == [str(Path(sys.executable).resolve()), "-c"]
    assert kwargs["input"] == contract.model_dump_json()
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == 7200
    assert kwargs["capture_output"] is True
    assert "PYTHONPATH" in kwargs["env"]  # type: ignore[operator]
    assert "EPIPILOT_ALGORITHM_PYTHON" not in kwargs["env"]  # type: ignore[operator]


def test_phase_computer_rejects_contract_for_another_adapter(tmp_path: Path) -> None:
    contract = _contract(tmp_path).model_copy(update={"adapter": "unregistered"})
    computer = SubprocessPhaseComparisonComputer(
        Path(sys.executable), Path(__file__).parents[1] / "src"
    )

    with pytest.raises(ValueError, match="another phase adapter"):
        computer.measure(tmp_path, contract)


def test_phase_computer_rejects_modified_pinned_measurement_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = _contract(tmp_path)
    source = tmp_path / "src/quant_etf/carts_phase_computer.py"
    source.write_text("changed phase implementation\n", encoding="utf-8")
    called = False
    real_run = subprocess.run

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        if args[0] != "git":
            called = True
        return real_run(args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("epipilot.workspace.phase_execution.subprocess.run", run)
    computer = SubprocessPhaseComparisonComputer(
        Path(sys.executable), Path(__file__).parents[1] / "src"
    )

    with pytest.raises(ValueError, match="code differs from its pinned revision"):
        computer.measure(tmp_path, contract)

    assert not called


def test_phase_computer_is_registered_under_its_exact_contract_id() -> None:
    computers = phase_comparison_computers(Path(sys.executable), Path(__file__).parents[1] / "src")

    assert set(computers) == {PHASE_COMPARISON_ADAPTER_ID}
    assert isinstance(computers[PHASE_COMPARISON_ADAPTER_ID], SubprocessPhaseComparisonComputer)
