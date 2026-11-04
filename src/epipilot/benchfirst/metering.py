"""Trusted request accounting. A reservation is spent until verified usage arrives.

Amounts are integer nano-dollars; estimated charges are never called provider billing.
The database belongs to the controller and must not be mounted in an agent container.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from epipilot.benchfirst.contracts import SHA256
from epipilot.benchfirst.offline import Value


class RequestBudget(Value):
    cell_id: str = Field(min_length=1)
    plan_sha256: str = Field(pattern=SHA256)
    model: str = Field(min_length=1)
    approval_ref: str = Field(min_length=1)
    max_attempts: int = Field(ge=0)
    max_http_requests: int = Field(ge=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    input_nusd_per_token: int = Field(gt=0)
    output_nusd_per_token: int = Field(gt=0)
    max_cost_nusd: int = Field(ge=0)
    wall_seconds: int = Field(gt=0, le=1800)

    @property
    def reservation(self) -> int:
        return (
            self.max_input_tokens * self.input_nusd_per_token
            + self.max_output_tokens * self.output_nusd_per_token
        )


class Usage(Value):
    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)


class MeterSummary(Value):
    attempts: int
    http_requests: int
    held_nusd: int
    estimated_nusd: int
    unknown_attempts: int
    rejected_requests: int = 0
    provider_billed_nusd: None = None


class Meter:
    def __init__(
        self, path: Path, budget: RequestBudget, *, now: Callable[[], float] = time.time
    ) -> None:
        if path.is_symlink():
            raise ValueError("symlinked meter")
        self.path, self.budget, self.now = path, budget, now
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS policy (body TEXT NOT NULL, started REAL NOT NULL)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY, attempt TEXT NOT NULL, kind TEXT NOT NULL,
                body TEXT NOT NULL, timestamp TEXT NOT NULL)""")
            for table in ("policy", "events"):
                for operation in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER IF NOT EXISTS no_{table}_{operation} "
                        f"BEFORE {operation} ON {table} BEGIN "
                        "SELECT RAISE(ABORT, 'append-only audit'); END"
                    )
            rows = db.execute("SELECT body FROM policy").fetchall()
            if not rows:
                db.execute("INSERT INTO policy VALUES (?, ?)", (budget.model_dump_json(), now()))
            elif len(rows) != 1 or rows[0][0] != budget.model_dump_json():
                raise ValueError("immutable meter policy mismatch")
        path.chmod(0o600)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db

    def _append(
        self, db: sqlite3.Connection, attempt: str, kind: str, body: dict[str, object]
    ) -> None:
        db.execute(
            "INSERT INTO events(attempt,kind,body,timestamp) VALUES(?,?,?,?)",
            (attempt, kind, json.dumps(body, sort_keys=True), datetime.now(UTC).isoformat()),
        )

    def _events(self, db: sqlite3.Connection) -> list[tuple[str, str, dict[str, object]]]:
        return [
            (a, k, json.loads(b))
            for a, k, b in db.execute("SELECT attempt, kind, body FROM events ORDER BY sequence")
        ]

    def _summary(self, db: sqlite3.Connection) -> MeterSummary:
        events = self._events(db)
        reserved = {a for a, k, _ in events if k == "reserved"}
        settled = {a: b for a, k, b in events if k == "usage"}
        estimated = sum(int(str(b["estimated_nusd"])) for b in settled.values())
        return MeterSummary(
            attempts=len(reserved),
            http_requests=sum(k == "wire" for _, k, _ in events),
            held_nusd=(len(reserved - settled.keys()) * self.budget.reservation + estimated),
            estimated_nusd=estimated,
            unknown_attempts=len(reserved - settled.keys()),
            rejected_requests=sum(k == "denied" for _, k, _ in events),
        )

    def summary(self) -> MeterSummary:
        with self._transaction() as db:
            return self._summary(db)

    def denied(self, request_sha256: str) -> None:
        with self._transaction() as db:
            self._append(db, "rejected", "denied", {"sha256": request_sha256})

    def _check_wall(self, db: sqlite3.Connection) -> None:
        started = float(db.execute("SELECT started FROM policy").fetchone()[0])
        if not 0 <= self.now() - started < self.budget.wall_seconds:
            raise ValueError("wall budget or clock rollback")

    def _pending(self, db: sqlite3.Connection, attempt: str) -> None:
        kinds = [k for a, k, _ in self._events(db) if a == attempt]
        if "reserved" not in kinds or any(k in kinds for k in ("usage", "interrupted")):
            raise ValueError("attempt is not pending")

    def reserve(self, request_sha256: str) -> str:
        import re

        if re.fullmatch(SHA256, request_sha256) is None:
            raise ValueError("invalid request digest")
        with self._transaction() as db:
            self._check_wall(db)
            events = self._events(db)
            if any(b.get("sha256") == request_sha256 for _, k, b in events if k == "reserved"):
                raise ValueError("duplicate request requires a changed strategy")
            pending = {a for a, k, _ in events if k == "reserved"} - {
                a for a, k, _ in events if k in {"usage", "interrupted"}
            }
            if pending:
                raise ValueError("unreconciled pending attempt")
            summary = self._summary(db)
            if (
                summary.attempts >= self.budget.max_attempts
                or summary.held_nusd + self.budget.reservation > self.budget.max_cost_nusd
            ):
                raise ValueError("model attempt or money budget exhausted")
            attempt = uuid4().hex
            self._append(
                db,
                attempt,
                "reserved",
                {"sha256": request_sha256, "held_nusd": self.budget.reservation},
            )
            return attempt

    def wire(self, attempt: str, endpoint: Literal["input_tokens", "responses"]) -> None:
        with self._transaction() as db:
            self._check_wall(db)
            self._pending(db, attempt)
            endpoints = [
                b.get("endpoint") for a, k, b in self._events(db) if a == attempt and k == "wire"
            ]
            if endpoints != ([] if endpoint == "input_tokens" else ["input_tokens"]):
                raise ValueError("duplicate or out-of-order HTTP request")
            if self._summary(db).http_requests >= self.budget.max_http_requests:
                raise ValueError("HTTP request budget exhausted")
            self._append(db, attempt, "wire", {"endpoint": endpoint})

    def finish(self, attempt: str, usage: Usage, response_id: str) -> None:
        with self._transaction() as db:
            self._pending(db, attempt)
            if (
                usage.input_tokens > self.budget.max_input_tokens
                or usage.output_tokens > self.budget.max_output_tokens
            ):
                raise ValueError("provider usage exceeded reservation; retain full hold")
            if any(
                b.get("response_id") == response_id for _, k, b in self._events(db) if k == "usage"
            ):
                raise ValueError("duplicate provider response")
            estimated = (
                usage.input_tokens * self.budget.input_nusd_per_token
                + usage.output_tokens * self.budget.output_nusd_per_token
            )
            self._append(
                db,
                attempt,
                "usage",
                {**usage.model_dump(), "response_id": response_id, "estimated_nusd": estimated},
            )

    def interrupt(self, attempt: str) -> None:
        with self._transaction() as db:
            self._pending(db, attempt)
            self._append(db, attempt, "interrupted", {"held_nusd": self.budget.reservation})
