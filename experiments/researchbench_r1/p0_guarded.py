"""Guarded, zero-model entrypoint for public R1 P0 calibration only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from experiments.researchbench_r1.resource_guard import guarded_run, single_runner_lock


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded R1 P0 diagnostic, no model calls")
    parser.add_argument("--cases-per-regime", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if not output.parent.is_dir() or output.exists():
        raise ValueError("choose a new result file inside an existing directory")
    with single_runner_lock():
        result = guarded_run(
            [
                sys.executable,
                "-m",
                "experiments.researchbench_r1.p0_audit",
                "--cases-per-regime",
                str(args.cases_per_regime),
                "--output",
                str(output),
            ],
            cwd=root,
            timeout=180,
        )
    # Exit 2 is the expected fail-closed P0 status, not a process crash.
    if result.returncode not in (0, 2) or not output.is_file():
        raise RuntimeError(f"P0 subprocess failed: {result.stderr[-700:]}")
    print(
        json.dumps(
            {
                "p0_status": "P0_BLOCKED",
                "calibration_receipt": str(output),
                "audit_exit_code": result.returncode,
                "elapsed_seconds": result.elapsed_seconds,
                "peak_sampled_host_memory_percent": result.peak_mac_used_percent,
                "minimum_sampled_available_gib": result.minimum_mac_available_gib,
                "qualification": (
                    "Sampled monitor cannot guarantee absence of transient RAM peaks. "
                    "No private scoring or paid P1 agent run was performed."
                ),
            },
            indent=2,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
