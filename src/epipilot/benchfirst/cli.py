"""Zero-spend CLI for preparing and auditing mature research-agent comparisons."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from epipilot.benchfirst.contracts import read_plan
from epipilot.benchfirst.evaluation import compare_receipts
from epipilot.benchfirst.preflight import catalog, source_preflight


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(
        prog="epi-benchmark",
        description="Benchmark-first adapter qualification and matched-result auditing",
    )
    sub = app.add_subparsers(dest="action", required=True)
    sub.add_parser("catalog", help="show verified candidate repository metadata, not a winner")
    validate = sub.add_parser("validate", help="validate frozen benchmark manifest offline")
    validate.add_argument("--manifest", type=Path, required=True)
    preflight = sub.add_parser("preflight", help="check exact local source checkouts without running")
    preflight.add_argument("--manifest", type=Path, required=True)
    preflight.add_argument("--workspace", type=Path, required=True)
    compare = sub.add_parser("compare", help="descriptive, complete-paired result audit")
    compare.add_argument("--manifest", type=Path, required=True)
    compare.add_argument("--ledger", type=Path, required=True)
    return app


def dispatch(args: argparse.Namespace) -> object:
    if args.action == "catalog":
        return {"frameworks": catalog(), "external_calls": 0}
    plan = read_plan(args.manifest)
    if args.action == "validate":
        return {
            "name": plan.name,
            "stage": plan.stage,
            "track": plan.track,
            "models": (
                plan.model.model_dump(mode="json") if plan.model is not None else "per_adapter"
            ),
            "tasks": [t.id for t in plan.tasks],
            "adapters": [a.id for a in plan.adapters],
            "planned_cells": len(plan.tasks) * len(plan.adapters) * plan.replicates,
            "max_model_attempts_total": (
                len(plan.tasks) * len(plan.adapters) * plan.replicates
                * plan.budget.max_model_attempts
            ),
            "source_pins_and_private_evaluator_checked": False,
            "status": "schema_valid_only",
            "qualification": (
                "No execution, model calls, isolation or effectiveness is implied. "
                "Approval in a manifest does not authorize actions in this CLI."
            ),
        }
    if args.action == "preflight":
        return source_preflight(plan, args.workspace)
    if args.action == "compare":
        return compare_receipts(plan, args.ledger)
    raise ValueError("unsupported benchmark action")


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        print(json.dumps(dispatch(args), ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, ValidationError, json.JSONDecodeError) as error:
        print(f"epi-benchmark: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
