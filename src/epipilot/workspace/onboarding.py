"""Local-first project onboarding. Historical extraction creates reviewable notes, never facts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

Signal = Literal["observation", "lesson"]
_RESEARCH = re.compile(
    r"\b(BLOCKED|FAILED|REJECTED|REFUTED|INCONCLUSIVE|SUCCESS|PASSED|"
    r"NEGATIVE|UNVERIFIED|CONCLUSION|RESULT|PROVENANCE|DO NOT|MUST|NEVER|STOP)\b"
    r"|失败|受阻|未通过|不显著|不能|不得|不要|结论|证据|验证|冻结",
    re.IGNORECASE,
)
_LESSON = re.compile(r"\b(DO NOT|MUST|NEVER|STOP|AVOID)\b|不能|不得|不要|必须", re.I)
_SECRET = re.compile(r"(TOKEN|PASSWORD|API_KEY|SECRET|PRIVATE_KEY)\s*[:=]", re.I)


def git(root: Path, *args: str) -> str:
    """Run a bounded read-only Git command without invoking a shell."""
    process = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        timeout=15, check=False,
    )
    if process.returncode:
        raise ValueError("Cannot inspect selected local Git repository")
    return process.stdout.strip()


def scan_history(root: Path, commit: str) -> list[dict[str, Any]]:
    """Bounded research-note candidates from the pinned commit, not the working tree."""
    paths = git(root, "ls-tree", "-r", "--name-only", commit).splitlines()
    selected = [
        p for p in paths if p == "README.md"
        or (p.startswith("docs/") and p.lower().endswith((".md", ".json", ".yaml")))
    ][:80]
    candidates: list[dict[str, Any]] = []
    for path in selected:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{path}"],
            capture_output=True, timeout=15, check=False,
        )
        if result.returncode or len(result.stdout) > 160_000:
            continue
        try:
            source = result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            continue
        digest = hashlib.sha256(result.stdout).hexdigest()
        for line_no, line in enumerate(source.splitlines(), 1):
            text = line.strip()
            if (
                not 22 <= len(text) <= 450 or text.startswith(("#", "|", ">", "http", "["))
                or _SECRET.search(text) or not _RESEARCH.search(text)
            ):
                continue
            key = hashlib.sha256(
                f"{commit}\0{path}\0{line_no}\0{text}".encode("utf-8")
            ).hexdigest()[:24]
            candidates.append({
                "id": key, "kind": "lesson" if _LESSON.search(text) else "observation",
                "text": text, "source_path": path, "line": line_no,
                "source_sha256": digest, "git_commit": commit,
            })
            if len(candidates) == 220:
                return candidates
    return candidates


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class WorkspaceProject:
    id: str
    name: str
    goal: str
    mode: str
    repository_path: str | None
    git_commit: str | None
    git_branch: str | None
    created_at: str


class WorkspaceStore:
    """Staging database with append-only review events; not the canonical EventStore."""

    def __init__(self, path: Path, roots: tuple[Path, ...] | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.roots = tuple(r.expanduser().resolve() for r in (
            roots if roots is not None else (Path.home(),)
        ))
        with self._db() as conn:
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS projects ("
                "id TEXT PRIMARY KEY, name TEXT, goal TEXT, mode TEXT, "
                "repository_path TEXT, git_commit TEXT, git_branch TEXT, created_at TEXT);"
                "CREATE TABLE IF NOT EXISTS candidates ("
                "id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, text TEXT, "
                "source_path TEXT, line INTEGER, source_sha256 TEXT, git_commit TEXT);"
                "CREATE TABLE IF NOT EXISTS workspace_events ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, kind TEXT, "
                "payload TEXT, occurred_at TEXT);"
            )

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def projects(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def _resolve_repo(self, value: str) -> tuple[Path, str, str]:
        root = Path(value).expanduser().resolve(strict=True)
        if not root.is_dir() or not any(root == p or p in root.parents for p in self.roots):
            raise ValueError("Git repository path is outside configured allowed roots")
        top = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
        if top != root:
            raise ValueError("Choose the Git repository root")
        return root, git(root, "rev-parse", "HEAD"), git(root, "branch", "--show-current")

    @staticmethod
    def _event(
        conn: sqlite3.Connection, project_id: str, kind: str, payload: dict[str, Any]
    ) -> None:
        conn.execute(
            "INSERT INTO workspace_events(project_id, kind, payload, occurred_at) "
            "VALUES (?, ?, ?, ?)",
            (project_id, kind, json.dumps(payload, ensure_ascii=False), _now()),
        )

    def create(
        self, name: str, goal: str, mode: Literal["create", "adopt"],
        repository_path: str | None = None,
    ) -> dict[str, Any]:
        if not name.strip() or not goal.strip() or len(name) > 120 or len(goal) > 3000:
            raise ValueError("Name and goal are required (max 120 and 3000 chars)")
        if mode not in ("create", "adopt"):
            raise ValueError("Unknown onboarding mode")
        if mode == "adopt" and not repository_path:
            raise ValueError("Adoption requires a local Git repository path")
        if mode == "create" and repository_path:
            raise ValueError("Use adoption to link an existing repository")
        root: Path | None = None
        sha: str | None = None
        branch: str | None = None
        notes: list[dict[str, Any]] = []
        if repository_path:
            root, sha, branch = self._resolve_repo(repository_path)
            notes = scan_history(root, sha)
        item = WorkspaceProject(
            str(uuid4()), name.strip(), goal.strip(), mode,
            str(root) if root else None, sha, branch, _now(),
        )
        with self._db() as conn:
            conn.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(asdict(item).values()),
            )
            conn.executemany(
                "INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(n["id"], item.id, n["kind"], n["text"], n["source_path"],
                  n["line"], n["source_sha256"], n["git_commit"]) for n in notes],
            )
            self._event(conn, item.id, "project_created", {
                "mode": mode, "git_commit": sha, "candidate_count": len(notes),
            })
        return self.get(item.id)

    def get(self, project_id: str) -> dict[str, Any]:
        with self._db() as conn:
            project = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project is None:
                raise KeyError("Unknown project")
            candidates = conn.execute(
                "SELECT * FROM candidates WHERE project_id=? ORDER BY source_path, line",
                (project_id,),
            ).fetchall()
            events = conn.execute(
                "SELECT * FROM workspace_events WHERE project_id=? ORDER BY seq DESC LIMIT 100",
                (project_id,),
            ).fetchall()
        data = dict(project)
        latest_reviews: dict[str, str] = {}
        for e in events:
            payload = json.loads(str(e["payload"]))
            if e["kind"] == "candidate_review":
                latest_reviews.setdefault(payload["candidate_id"], payload["decision"])
        data["candidates"] = [
            {**dict(c), "review": latest_reviews.get(c["id"], "pending")}
            for c in candidates
        ]
        data["events"] = [
            {**dict(e), "payload": json.loads(str(e["payload"]))} for e in events
        ]
        data["git"] = self.git_status(project_id)
        return data

    def git_status(self, project_id: str) -> dict[str, Any]:
        with self._db() as conn:
            item = conn.execute(
                "SELECT repository_path FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        if item is None:
            raise KeyError("Unknown project")
        if item["repository_path"] is None:
            return {"attached": False}
        root, _sha, _branch = self._resolve_repo(str(item["repository_path"]))
        return {
            "attached": True,
            "branch": git(root, "branch", "--show-current") or "(detached)",
            "commit": git(root, "rev-parse", "HEAD"),
            "changed_paths": len(git(root, "status", "--porcelain", "-uno").splitlines()),
            "worktrees": git(root, "worktree", "list", "--porcelain").count("worktree "),
        }

    def review(
        self, project_id: str, candidate_id: str,
        decision: Literal["accepted_note", "rejected"],
    ) -> dict[str, Any]:
        if decision not in ("accepted_note", "rejected"):
            raise ValueError("Review must be accepted_note or rejected")
        with self._db() as conn:
            present = conn.execute(
                "SELECT 1 FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            if not present:
                raise KeyError("Unknown candidate")
            self._event(conn, project_id, "candidate_review", {
                "candidate_id": candidate_id, "decision": decision,
            })
        return self.get(project_id)

    def intent(self, project_id: str, message: str) -> dict[str, Any]:
        if not message.strip() or len(message) > 3000:
            raise ValueError("Intent must contain 1-3000 characters")
        with self._db() as conn:
            if not conn.execute(
                "SELECT 1 FROM projects WHERE id=?", (project_id,)
            ).fetchone():
                raise KeyError("Unknown project")
            self._event(conn, project_id, "user_intent_proposed", {
                "message": message.strip(),
            })
        return self.get(project_id)

    def chat_prompt(self, project_id: str) -> str:
        item = self.get(project_id)
        accepted = [n for n in item["candidates"] if n["review"] == "accepted_note"][:24]
        notes = "\n".join(
            f"- {n['kind']}: {n['text']} ({n['source_path']}:{n['line']}, "
            f"commit {n['git_commit'][:12]})"
            for n in accepted
        )
        return (
            "EpiPilot research delegation — ordinary ChatGPT Chat, not Work.\n"
            f"Project: {item['name']}\nGoal: {item['goal']}\n"
            f"Git snapshot: {item['git_commit'] or 'none'}\n"
            "Reviewed historical notes (not verified scientific facts):\n"
            f"{notes or '(no reviewed notes)'}\n\n"
            "Use authorized ChatGPT GitHub and Notion tools, if available. "
            "Separate code/CI success from scientific evidence. Recommend the smallest "
            "high-information experiment, competing hypotheses, stopping conditions "
            "and necessary proof. Report actual action limitations and links. "
            "Do not trade real money or claim historical research as fresh OOS."
        )
