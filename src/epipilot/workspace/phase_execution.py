"""Isolated bridge to the ETF repository's pinned phase-grid computer."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured

PHASE_COMPARISON_ADAPTER_ID = "quant_etf_phase_comparison_v1"
PHASE_COMPARISON_CODE_PATHS = (
    "src/quant_etf/allocation.py",
    "src/quant_etf/carts_phase_computer.py",
    "src/quant_etf/engine.py",
    "src/quant_etf/metrics.py",
    "src/quant_etf/turnover.py",
    "src/quant_etf/v2_engine.py",
)
PHASE_COMPARISON_TIMEOUT_SECONDS = 7200

_RUNNER = """
import sys
from pathlib import Path
from epipilot.research.frontier import MeasurementContract
from quant_etf.carts_phase_computer import PhaseComparisonComputer

contract = MeasurementContract.model_validate_json(sys.stdin.read())
measurement = PhaseComparisonComputer().measure(Path.cwd(), contract)
sys.stdout.write(measurement.model_dump_json())
"""


class SubprocessPhaseComparisonComputer:
    """Run the fixed phase-grid adapter with a restricted environment."""

    quality = Quality.MEASUREMENT

    def __init__(self, python_executable: Path, epipilot_source: Path) -> None:
        self.python_executable = python_executable.resolve()
        self.epipilot_source = epipilot_source.resolve()

    def _validate_code(self, root: Path, revision: str) -> None:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "diff",
                "--quiet",
                revision,
                "--",
                *PHASE_COMPARISON_CODE_PATHS,
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            raise ValueError("phase-comparison code differs from its pinned revision")

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != PHASE_COMPARISON_ADAPTER_ID:
            raise ValueError("contract uses another phase adapter")
        if len(contract.inputs) != 2:
            raise ValueError("phase comparison requires the phase panel and event inputs")
        repository = root.resolve()
        self._validate_code(repository, contract.git_revision)

        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}
        }
        env["PYTHONPATH"] = os.pathsep.join((str(repository / "src"), str(self.epipilot_source)))
        process = subprocess.run(
            [str(self.python_executable), "-c", _RUNNER],
            input=contract.model_dump_json(),
            cwd=repository,
            env=env,
            text=True,
            capture_output=True,
            timeout=PHASE_COMPARISON_TIMEOUT_SECONDS,
            check=False,
        )
        if process.returncode != 0:
            raise ValueError(f"phase-comparison process failed (exit {process.returncode})")
        try:
            measured = Measured.model_validate_json(process.stdout)
        except ValueError:
            raise ValueError("phase-comparison process returned invalid measurement JSON") from None
        adapter_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        return Measured(
            metrics=measured.metrics,
            samples=measured.samples,
            details=(*measured.details, f"epipilot_adapter_sha256={adapter_digest}"),
        )


def phase_comparison_computers(
    python_executable: Path, epipilot_source: Path
) -> dict[str, Computer]:
    """Register only the exact adapter backed by the phase-grid subprocess."""
    return {
        PHASE_COMPARISON_ADAPTER_ID: SubprocessPhaseComparisonComputer(
            python_executable, epipilot_source
        )
    }
