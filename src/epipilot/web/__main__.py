"""Run with python -m epipilot.web --repo /path/to/repository."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="EpiPilot local read-only project workbench")
    parser.add_argument(
        "--repo", type=Path, required=True, help="Local Git repository with a commit"
    )
    parser.add_argument(
        "--events-db", type=Path, help="Existing EpiPilot SQLite database (read-only)"
    )
    parser.add_argument("--project-id", help="Exact aggregate ID; required with --events-db")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.events_db is not None and not args.project_id:
        parser.error("--project-id is required with --events-db")
    try:
        import uvicorn

        from epipilot.web.app import create_app
        from epipilot.web.service import WorkbenchService
    except ImportError:
        parser.exit(2, "Install web dependencies first: pip install -e '.[web]'\n")
    service = WorkbenchService(args.repo, events_db=args.events_db, project_id=args.project_id)
    try:
        service.snapshot()
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.error(f"Unable to load project data: {type(error).__name__}")
    print(f"EpiPilot read-only workbench: http://127.0.0.1:{args.port}")
    uvicorn.run(create_app(service), host="127.0.0.1", port=args.port, access_log=False, ws="none")


if __name__ == "__main__":
    main()
