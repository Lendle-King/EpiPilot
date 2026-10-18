"""Minimal CARTS CLI, e.g. python -m epipilot.research --help."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from epipilot.research.budget import BudgetManager
from epipilot.research.engine import ResearchEngine
from epipilot.research.etf import ETFAdapter, historical_replay
from epipilot.research.models import (
    CostVector,
    ExperimentProposal,
    Observation,
    ResearchDefinition,
    Strategy,
)
from epipilot.research.reporting import report, write_report
from epipilot.research.synthetic import benchmark
from epipilot.runtime.sqlite_event_store import SqliteEventStore


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "agent":
        from epipilot.research.agent_cli import main as agent_main

        raise SystemExit(agent_main(sys.argv[2:]))
    parser = argparse.ArgumentParser(description="Cost-Aware Research Tree Search")
    parser.add_argument(
        "command",
        choices=(
            "init-etf",
            "init",
            "propose",
            "cancel",
            "show",
            "rank",
            "dry-run",
            "run-next",
            "observe",
            "verify",
            "budget",
            "resume",
            "report",
            "benchmark",
            "etf-replay",
            "etf-pilot",
        ),
    )
    parser.add_argument(
        "--db", type=Path, default=Path.home() / ".local/share/epipilot/workspace.sqlite3"
    )
    parser.add_argument("--project", default="883d9597-4566-43b3-862e-1e2fb599b674")
    parser.add_argument(
        "--repository", type=Path, default=Path("/Users/lendleblues/Documents/quant_etf_real")
    )
    parser.add_argument("--output", type=Path, default=Path(".epipilot/carts"))
    parser.add_argument("--strategy", type=Strategy, choices=list(Strategy), default=Strategy.CARTS)
    parser.add_argument("--seeds", type=int, default=50)
    parser.add_argument("--start-seed", type=int, default=0)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--observation", type=Path)
    parser.add_argument("--definition", type=Path)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--basis-ref")
    parser.add_argument("--proposal-id")
    parser.add_argument("--actual-cost", type=Path)
    parser.add_argument("--reason")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 100:
        parser.error("rounds must be 1..100")
    adapter = ETFAdapter(args.repository)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.command == "benchmark":
        data = benchmark(args.seeds, args.start_seed)
        write_report(args.output / "benchmark.json", data, "Paired synthetic CARTS benchmark")
        print(
            json.dumps(
                {"report": str(args.output / "benchmark.json"), "summary": data["summary"]},
                indent=2,
            )
        )
        return
    if args.command == "etf-replay":
        data = historical_replay(adapter)
        write_report(args.output / "etf-replay.json", data, "ETF chronological evidence replay")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    engine = ResearchEngine(SqliteEventStore(args.db), args.project, args.output / "artifacts")
    if args.command == "init":
        if args.definition is None:
            parser.error("init requires --definition JSON (ResearchDefinition)")
        definition = ResearchDefinition.model_validate_json(args.definition.read_bytes())
        engine.initialize(definition.goal, definition.nodes, definition.proposals)
    if args.command == "propose":
        if args.proposal is None or args.basis_ref is None:
            parser.error("propose requires --proposal JSON and --basis-ref canonical ID")
        engine.propose(
            ExperimentProposal.model_validate_json(args.proposal.read_bytes()), args.basis_ref
        )
    if args.command == "cancel":
        if args.proposal_id is None or args.actual_cost is None or not args.reason:
            parser.error(
                "cancel requires --proposal-id, --actual-cost JSON {costs:[...]}, and --reason"
            )
        engine.cancel(
            args.proposal_id,
            CostVector.model_validate_json(args.actual_cost.read_bytes()).costs,
            args.reason,
        )
    if args.command in {"init-etf", "etf-pilot"}:
        adapter.initialize(engine)
    if args.command in {"run-next", "etf-pilot"}:
        before = report(engine)
        for _ in range(args.rounds if args.command == "etf-pilot" else 1):
            observation = engine.run_next(adapter, adapter, args.strategy)
            if observation is None:
                break
            assert engine.state.research is not None
            write_report(
                args.output / f"round-{len(engine.state.research.settlements):03d}.json",
                report(engine),
            )
        data = report(engine)
        data["before"] = before
        data["tree_changes"] = [
            {"label": a["label"], "before": a["status"], "after": b["status"]}
            for a, b in zip(before["tree"], data["tree"], strict=True)
            if a != b
        ]
    elif args.command in {"resume", "verify"}:
        engine.resume(adapter)
        data = report(engine)
    elif args.command == "observe":
        if args.observation is None:
            parser.error(
                "observe requires --observation (typed raw Observation JSON; never self-verifies)"
            )
        engine.observe(Observation.model_validate_json(args.observation.read_bytes()))
        data = report(engine)
    elif args.command in {"rank", "dry-run"}:
        ranking = engine.ranking(args.strategy)
        data = ranking.model_dump(mode="json")
        if args.command == "dry-run":
            assert engine.state.research is not None
            costs = (
                BudgetManager(engine.state.research).reservation(
                    engine.state.research.proposal(ranking.ranked[0].proposal_id)
                )
                if ranking.ranked
                else ()
            )
            data = {
                "ranking": data,
                "budget": engine.budget(),
                "reservation_preview": [c.model_dump(mode="json") for c in costs],
                "mutated": False,
            }
    elif args.command == "budget":
        data = engine.budget()
    else:
        data = report(engine)
    write_report(args.output / "report.json", data)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
