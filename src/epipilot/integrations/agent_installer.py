"""Safe project-local EpiPilot skill installer for Codex and Kimi Code.

This is an AGENTS.md/Skill integration, not Codex marketplace registration or
an attempt to modify global agent configuration. Never overwrite unowned files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

START = "<!-- BEGIN EPIPILOT RESEARCH WORKFLOW -->"
END = "<!-- END EPIPILOT RESEARCH WORKFLOW -->"
SECTION = """<!-- BEGIN EPIPILOT RESEARCH WORKFLOW -->
## EpiPilot research workflow

For scientific or algorithm optimization tasks, read
`.agents/skills/epipilot-research/SKILL.md` and follow its protocol.
Use the installed `epi` CLI for goals, hypotheses, cost reservations,
pre-registered experiments and verified evidence. Execute actions through
this agent's existing tools and sandbox. Never invent measurements or
convert executor self-report into canonical scientific conclusions.
<!-- END EPIPILOT RESEARCH WORKFLOW -->"""
SKILL_PATH = Path(".agents/skills/epipilot-research/SKILL.md")
AGENTS_PATH = Path("AGENTS.md")


def _skill_text() -> str:
    return (
        files("epipilot.integrations")
        .joinpath("templates", "epipilot-research", "SKILL.md")
        .read_text(encoding="utf-8")
    )


def _validate_target(root: Path, relative: Path) -> Path:
    """Reject any parent/path symlink and unexpected escape from project root."""
    destination = root / relative
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"destination escapes project root: {relative}")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"refusing symlink in installation path: {cursor}")
    return destination


def _atomic_write(destination: Path, content: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=".epipilot-install-",
            delete=False,
        ) as f:
            tmp_path = Path(f.name)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(destination)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _inject_marker(existing: str, marker: str) -> str:
    if existing.count(START) > 1 or existing.count(END) > 1:
        raise ValueError("duplicated EpiPilot section markers in AGENTS.md")
    if START in existing or END in existing:
        if existing.count(START) != 1 or existing.count(END) != 1:
            raise ValueError("unbalanced EpiPilot section markers in AGENTS.md")
        begin, end = existing.index(START), existing.index(END) + len(END)
        if begin >= end:
            raise ValueError("malformed EpiPilot section markers in AGENTS.md")
        return existing[:begin] + marker + existing[end:]
    suffix = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + suffix + marker + "\n"


def _plan(root: Path, agent: str, force: bool) -> list[tuple[Path, str]]:
    skill_file = _validate_target(root, SKILL_PATH)
    expected = _skill_text()
    if skill_file.exists():
        old = skill_file.read_text(encoding="utf-8")
        if old != expected and not force:
            raise ValueError(
                f"{SKILL_PATH} differs from this EpiPilot version; "
                "inspect it and use --force only if safe to replace"
            )
    operations = [(skill_file, expected)]
    if agent in {"kimi", "both"}:
        agents_file = _validate_target(root, AGENTS_PATH)
        current = agents_file.read_text(encoding="utf-8") if agents_file.exists() else ""
        operations.append((agents_file, _inject_marker(current, SECTION)))
    return operations


def install(
    root: Path, *, agent: str = "both", force: bool = False, dry_run: bool = False
) -> dict[str, Any]:
    """Plan and apply a conservative project-only Skill/AGENTS installation."""
    if agent not in {"codex", "kimi", "both"}:
        raise ValueError("agent must be codex, kimi, or both")
    if not root.is_dir():
        raise ValueError("project path must point to an existing directory")
    root = root.resolve()
    operations = _plan(root, agent, force)
    changed: list[str] = []
    unchanged: list[str] = []
    for destination, content in operations:
        relative = destination.relative_to(root).as_posix()
        existing = destination.read_text(encoding="utf-8") if destination.exists() else None
        if existing == content:
            unchanged.append(relative)
        elif not dry_run:
            _atomic_write(destination, content)
            changed.append(relative)
        else:
            changed.append(relative)
    return {
        "ok": True,
        "agent": agent,
        "project": str(root),
        "dry_run": dry_run,
        "changed_or_pending": changed,
        "unchanged": unchanged,
        "installation": "project_skill_and_instructions",
        "native_codex_marketplace_plugin": False,
        "next": (
            "Start a NEW agent session in the project; verify the installed CLI with epi --help."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--agent", choices=("codex", "kimi", "both"), default="both")
    parser.add_argument("--force", action="store_true", help="replace a modified project skill")
    parser.add_argument("--dry-run", action="store_true", help="inspect changes without writing")
    parser.add_argument("--json", action="store_true", help="print machine-readable result")
    args = parser.parse_args(argv)
    try:
        outcome = install(args.project, agent=args.agent, force=args.force, dry_run=args.dry_run)
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"epipilot-install-agent: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
    else:
        print(f"EpiPilot skill {'planned' if args.dry_run else 'installed'}: {outcome['project']}")
        for name in outcome["changed_or_pending"]:
            print(f"  updated: {name}")
        for name in outcome["unchanged"]:
            print(f"  unchanged: {name}")
        print(outcome["next"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
