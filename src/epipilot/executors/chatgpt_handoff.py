"""Human-supervised ChatGPT Web handoff for EpiPilot research tasks.

GitHub and Notion are *only* ChatGPT-side destinations: this module never uses
GitHub/Notion APIs or extracts content from ChatGPT's web page.

The handoff is operational state, not canonical EpiPilot fact/evidence/event state.
No record in this module can mark an EpiPilot task PASSED.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from epipilot.executors.agent_browser import AgentBrowserLauncher, session_for_task

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_CONTEXT_BYTES = 64 * 1024
_MAX_RECEIPT_BYTES = 64 * 1024


class HandoffState(StrEnum):
    PREPARED = "prepared"
    OPENED = "opened"
    RESULT_REPORTED = "result_reported"


def _validate_url(value: str, service: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("destination must be an HTTPS URL without credentials")
    if service == "github" and host != "github.com":
        raise ValueError("GitHub reference must use github.com")
    if service == "notion" and not (
        host in {"notion.so", "www.notion.so"} or host.endswith(".notion.site")
    ):
        raise ValueError("Notion reference must use notion.so or a notion.site subdomain")
    if not parsed.path.strip("/"):
        raise ValueError("reference must identify a specific page or repository")
    return value


@dataclass(frozen=True, slots=True)
class ResearchHandoff:
    task_id: str
    objective: str
    context: str
    github_repos: tuple[str, ...]
    notion_pages: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.task_id):
            raise ValueError("task id must be 1-128 safe characters")
        if not self.objective.strip():
            raise ValueError("objective must not be empty")
        if not self.context.strip():
            raise ValueError("context must not be empty")
        if len(self.context.encode("utf-8")) > _MAX_CONTEXT_BYTES:
            raise ValueError("context exceeds 64 KiB")
        if not self.github_repos:
            raise ValueError("at least one GitHub repository is required")
        if not self.notion_pages:
            raise ValueError("at least one Notion destination is required")
        for link in self.github_repos:
            _validate_url(link, "github")
        for link in self.notion_pages:
            _validate_url(link, "notion")

    def prompt(self) -> str:
        repos = "\n".join(f"- {url}" for url in self.github_repos)
        notes = "\n".join(f"- {url}" for url in self.notion_pages)
        return (
            "# EpiPilot supervised research delegation\n\n"
            f"Task ID: {self.task_id}\n\n"
            f"## Objective\n{self.objective}\n\n"
            f"## Authoritative context and constraints\n{self.context}\n\n"
            f"## GitHub repositories (use ChatGPT's authorized capabilities only)\n{repos}\n\n"
            f"## Notion destinations (use ChatGPT's authorized capabilities only)\n{notes}\n\n"
            "## Execution and evidence requirements\n"
            "Use ChatGPT Work or Codex where available. First confirm actual read/write and "
            "experiment-execution permissions. Do not claim code was modified, experiments "
            "ran, or Notion was updated without verifiable evidence. Propose and run the "
            "smallest informative experiment within the stated constraints; request approval "
            "for expensive, destructive or irreversible actions. Record commit/PR and "
            "workflow-run links with configs, seeds, artifacts and measured outcomes; "
            "write evidence-backed progress and observations to Notion if authorized. "
            "If a connector lacks an action, report that limitation instead of pretending.\n\n"
            "Finally, provide an explicit summary, GitHub evidence links, Notion page links, "
            "open uncertainties, and what was not completed. This response remains an "
            "UNVERIFIED report until independently checked by EpiPilot or the user.\n"
        )


@dataclass(frozen=True, slots=True)
class DelegationReceipt:
    """Untrusted manual transcription of ChatGPT's report (never a proof)."""

    summary: str
    github_evidence: tuple[str, ...] = ()
    notion_evidence: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: bytes) -> DelegationReceipt:
        if len(raw) > _MAX_RECEIPT_BYTES:
            raise ValueError("receipt exceeds 64 KiB")
        try:
            body: Any = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("receipt must be UTF-8 JSON") from exc
        if not isinstance(body, dict) or set(body) - {
            "summary", "github_evidence", "notion_evidence", "limitations"
        }:
            raise ValueError("unexpected receipt fields")
        summary = body.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("receipt summary is required")
        fields: dict[str, tuple[str, ...]] = {}
        for name in ("github_evidence", "notion_evidence", "limitations"):
            items = body.get(name, [])
            if not isinstance(items, list) or len(items) > 50 or any(
                not isinstance(item, str) or not item.strip() for item in items
            ):
                raise ValueError(f"receipt {name} must be a list of nonempty strings")
            fields[name] = tuple(items)
        for item in fields["github_evidence"]:
            _validate_url(item, "github")
        for item in fields["notion_evidence"]:
            _validate_url(item, "notion")
        return cls(summary=summary, **fields)


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("state directory must be a real directory")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise PermissionError("state directory must not be accessible by group/others")


def _atomic_private_write(path: Path, data: bytes) -> None:
    _private_dir(path.parent)
    descriptor, temp_name = tempfile.mkstemp(prefix=".handoff-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


@dataclass(slots=True)
class ChatGPTWebHandoffStore:
    """Private operational handoff files; never writes canonical task completion."""

    root: Path

    def _folder(self, task_id: str) -> Path:
        if not _ID_RE.fullmatch(task_id):
            raise ValueError("invalid task id")
        return self.root / hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:24]

    def prepare(self, spec: ResearchHandoff) -> Path:
        folder = self._folder(spec.task_id)
        _private_dir(folder)
        prompt = spec.prompt().encode("utf-8")
        manifest_file = folder / "manifest.json"
        if manifest_file.exists():
            old = self.status(spec.task_id)
            if old["prompt_sha256"] != hashlib.sha256(prompt).hexdigest():
                raise ValueError("existing handoff differs; use a new task id")
            return folder / "prompt.md"
        _atomic_private_write(folder / "prompt.md", prompt)
        self._save_manifest(spec.task_id, {
            "schema_version": 1,
            "task_id": spec.task_id,
            "session": session_for_task(spec.task_id),
            "state": HandoffState.PREPARED.value,
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return folder / "prompt.md"

    def status(self, task_id: str) -> dict[str, Any]:
        path = self._folder(task_id) / "manifest.json"
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("task_id") != task_id:
            raise ValueError("corrupt handoff manifest")
        if data.get("schema_version") != 1 or data.get("state") not in set(HandoffState):
            raise ValueError("unsupported handoff schema/state")
        if data.get("session") != session_for_task(task_id):
            raise ValueError("invalid session binding")
        return data

    def opened(self, task_id: str) -> None:
        manifest = self.status(task_id)
        if manifest["state"] == HandoffState.RESULT_REPORTED:
            raise ValueError("cannot reopen a reported handoff")
        manifest["state"] = HandoffState.OPENED.value
        self._save_manifest(task_id, manifest)

    def report(self, task_id: str, raw_receipt: bytes) -> None:
        manifest = self.status(task_id)
        if manifest["state"] != HandoffState.OPENED:
            raise ValueError("handoff must be opened before accepting a report")
        receipt = DelegationReceipt.parse(raw_receipt)
        # Serialize a validated, normalized payload (not arbitrary extra input).
        data = json.dumps({
            "summary": receipt.summary,
            "github_evidence": receipt.github_evidence,
            "notion_evidence": receipt.notion_evidence,
            "limitations": receipt.limitations,
        }, ensure_ascii=False, indent=2).encode("utf-8")
        _atomic_private_write(self._folder(task_id) / "receipt.json", data)
        manifest["state"] = HandoffState.RESULT_REPORTED.value
        manifest["receipt_sha256"] = hashlib.sha256(data).hexdigest()
        # Deliberately no 'verified' or 'passed': a link is still a claim.
        self._save_manifest(task_id, manifest)

    def _save_manifest(self, task_id: str, manifest: dict[str, Any]) -> None:
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        _atomic_private_write(self._folder(task_id) / "manifest.json", encoded)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervised ChatGPT Web research delegation")
    parser.add_argument(
        "--state-dir", type=Path,
        default=Path.home() / ".local" / "share" / "epipilot" / "chatgpt-handoffs",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--task-id", required=True)
    p.add_argument("--objective", required=True)
    p.add_argument("--context-file", type=Path, required=True)
    p.add_argument("--github", action="append", required=True)
    p.add_argument("--notion", action="append", required=True)
    for name in ("open", "close", "status"):
        x = sub.add_parser(name)
        x.add_argument("--task-id", required=True)
    x = sub.add_parser("report")
    x.add_argument("--task-id", required=True)
    x.add_argument("--receipt-file", required=True, type=Path)
    return parser


async def _main() -> None:
    args = _parser().parse_args()
    store = ChatGPTWebHandoffStore(args.state_dir)
    if args.command == "prepare":
        path = store.prepare(ResearchHandoff(
            task_id=args.task_id,
            objective=args.objective,
            context=args.context_file.read_text(encoding="utf-8"),
            github_repos=tuple(args.github),
            notion_pages=tuple(args.notion),
        ))
        print(f"Handoff prepared. Manually paste the local file into ChatGPT Work: {path}")
    elif args.command == "status":
        print(json.dumps(store.status(args.task_id), indent=2))
    elif args.command == "report":
        store.report(args.task_id, args.receipt_file.read_bytes())
        print("Unverified report saved. Independent verification still required.")
    elif args.command == "open":
        session = store.status(args.task_id)["session"]
        await AgentBrowserLauncher().open(session)
        store.opened(args.task_id)
        print("ChatGPT opened; paste prompt.md manually and approve actions in ChatGPT.")
    elif args.command == "close":
        session = store.status(args.task_id)["session"]
        await AgentBrowserLauncher().close(session)
        print("Browser session closed (persistent restore remains under agent-browser control).")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
