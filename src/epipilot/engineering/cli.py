from __future__ import annotations

import argparse
import json
from pathlib import Path

from epipilot.engineering.models import ManagedPolicy
from epipilot.engineering.service import ManagedWork
from epipilot.runtime.sqlite_event_store import SqliteEventStore


def add_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    work = commands.add_parser("work")
    actions = work.add_subparsers(dest="work_action", required=True)
    configure = actions.add_parser("configure")
    configure.add_argument("--file", type=Path, required=True)
    acquire = actions.add_parser("acquire")
    acquire.add_argument("--operation", required=True)
    acquire.add_argument("--scope", action="append", required=True)
    acquire.add_argument("--forbidden", action="append", default=[])
    complete = actions.add_parser("complete")
    complete.add_argument("--operation", required=True)
    actions.add_parser("status")
    release = actions.add_parser("release")
    release.add_argument("--operation", required=True)
    publish = actions.add_parser("publish")
    publish.add_argument("--operation", required=True)
    publish.add_argument("--artifact", action="append", required=True)


def execute(args: argparse.Namespace) -> int:
    service = ManagedWork(SqliteEventStore(args.db), args.project, args.root)
    if args.work_action == "configure":
        service.configure(ManagedPolicy.model_validate_json(args.file.read_bytes()))
        data: object = service.policy.model_dump(mode="json")
    elif args.work_action == "acquire":
        data = service.acquire(args.operation, tuple(args.scope), tuple(args.forbidden)).model_dump(
            mode="json"
        )
    elif args.work_action == "complete":
        data = service.complete(args.operation).model_dump(mode="json")
    elif args.work_action == "release":
        data = service.release(args.operation).model_dump(mode="json")
    elif args.work_action == "publish":
        data = service.publish(args.operation, tuple(args.artifact)).model_dump(mode="json")
    else:
        data = [work.model_dump(mode="json") for work in service.status()]
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0
