"""Portable coding-agent protocol over the canonical SQLite event stream."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from epipilot.core.events import EventType
from epipilot.research.agent_service import AgentService
from epipilot.research.discovery import (
    DiscoveryBatch,
    apply_discovery,
    enable_frontier,
    git,
    safe_input,
)
from epipilot.research.engine import ResearchEngine, stable_id
from epipilot.research.frontier import InputFile, QuestionResolved, Resolution
from epipilot.research.models import Cost, CostVector, Observation, ResearchGoal
from epipilot.research.reporting import report
from epipilot.runtime.event_store import EventStoreError
from epipilot.runtime.sqlite_event_store import SqliteEventStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--db", type=Path, default=Path.home() / ".local/share/epipilot/workspace.sqlite3"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--goal")
    init.add_argument("--wall-seconds", type=float, default=120.0)
    init.add_argument("--exploration-fraction", type=float, default=0.2)
    for name in (
        "iterate",
        "next",
        "run",
        "start",
        "verify",
        "resume",
        "status",
        "report",
        "schema",
    ):
        commands.add_parser(name)
    commands.add_parser("onboard")
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--file", action="append", required=True)
    discovery = commands.add_parser("discover")
    discovery.add_argument("--file", type=Path, required=True)
    record = commands.add_parser("record")
    record.add_argument("--file", type=Path, required=True)
    cancel = commands.add_parser("cancel")
    cancel.add_argument("--proposal-id", required=True)
    cancel.add_argument("--cost-file", type=Path, required=True)
    cancel.add_argument("--reason", required=True)
    resolve = commands.add_parser("resolve")
    resolve.add_argument("--unknown-id", type=UUID, required=True)
    resolve.add_argument("--evidence-id", type=UUID, required=True)
    resolve.add_argument("--reopen", action="store_true")
    resolve.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "schema":
            print(json.dumps(DiscoveryBatch.model_json_schema(), ensure_ascii=False, indent=2))
            return 0
        root = args.root.resolve()
        if args.command == "onboard":
            revision = git(root, "rev-parse", "HEAD")
            paths = git(root, "ls-files").splitlines()
            visible = [
                p
                for p in paths
                if not any(
                    x.startswith(".env") or x.endswith((".pem", ".key")) for x in Path(p).parts
                )
            ]
            print(
                json.dumps(
                    {
                        "root": str(root),
                        "git_revision": revision,
                        "tracked_paths": visible[:120],
                        "omitted_paths": max(0, len(visible) - 120),
                        "instruction": (
                            "Read primary files to discover unknowns; "
                            "inventory is not scientific evidence."
                        ),
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "snapshot":
            inputs = []
            for path in args.file:
                with safe_input(root, path).open("rb") as handle:
                    inputs.append(
                        InputFile(
                            path=path, sha256=hashlib.file_digest(handle, "sha256").hexdigest()
                        ).model_dump(mode="json")
                    )
            print(
                json.dumps(
                    {"git_revision": git(root, "rev-parse", "HEAD"), "inputs": inputs}, indent=2
                )
            )
            return 0
        if args.command != "init" and not args.db.is_file():
            raise ValueError("canonical database missing; explicit init required")
        output = args.output or args.db.parent / "carts" / str(stable_id(args.project, "artifacts"))
        # Project identifiers cannot escape the artifact directory.
        if not args.project or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in args.project
        ):
            raise ValueError("project must be an alphanumeric identifier")
        engine = ResearchEngine(SqliteEventStore(args.db), args.project, output)
        if args.command == "init":
            if engine.state.research is None:
                if not args.goal:
                    raise ValueError("a new project requires an explicit --goal")
                engine.initialize(
                    ResearchGoal(
                        statement=args.goal,
                        budgets=(Cost(unit="wall_seconds", amount=args.wall_seconds),),
                    ),
                    (),
                    (),
                )
            elif args.goal and args.goal != engine.state.research.goal.statement:
                raise ValueError("cannot overwrite the existing research goal")
            enable_frontier(engine, root, args.exploration_fraction)
        service = AgentService(engine, root)
        if args.command == "discover":
            apply_discovery(
                engine, root, DiscoveryBatch.model_validate_json(args.file.read_bytes())
            )
        elif args.command == "run":
            service.run_next()
        elif args.command == "start":
            service.start()
        elif args.command in {"verify", "resume"}:
            service.verify()
        elif args.command == "record":
            engine.observe(Observation.model_validate_json(args.file.read_bytes()))
        elif args.command == "cancel":
            engine.cancel(
                args.proposal_id,
                CostVector.model_validate_json(args.cost_file.read_bytes()).costs,
                args.reason,
            )
        elif args.command == "resolve":
            engine.append(
                EventType.RESEARCH_QUESTION_RESOLVED,
                QuestionResolved(
                    resolution=Resolution(
                        unknown_id=args.unknown_id,
                        evidence_id=args.evidence_id,
                        resolved=not args.reopen,
                        rationale=args.reason,
                    )
                ),
            )
        data = (
            report(engine)
            if args.command in {"status", "report"}
            else service.iterate().model_dump(mode="json")
        )
        if args.command in {"status", "report"}:
            data["agent"] = service.iterate().model_dump(mode="json")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except ValidationError as exc:
        issues = [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
        print(json.dumps({"error": "schema_validation", "issues": issues}), file=sys.stderr)
        return 2
    except subprocess.CalledProcessError:
        print("epi: Git source validation failed", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, EventStoreError) as exc:
        print(f"epi: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
