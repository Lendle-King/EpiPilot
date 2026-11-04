"""Typed S0-only task seam and durable mock quota accounting.

This module has no provider transport. Mock reservations never authorize a real call.
The journal is an engineering log, not canonical scientific evidence.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from epipilot.benchfirst.contracts import (
    SHA256,
    Record,
    _no_duplicate_properties,
    _reject_constant,
    read_strict_json,
)


class Value(Record):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicTask(Value):
    task_id: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    seed: int = Field(ge=0)
    objective: str = "Synthetic plumbing only: predict 2*x and minimize development MSE"
    allowed_files: tuple[str, ...] = ("candidate.py",)
    expected_outputs: tuple[str, ...] = ("candidate.py", "prediction.json")
    row_ids: tuple[str, ...] = Field(min_length=1)
    x: tuple[float, ...]
    dev_y: tuple[float, ...]
    synthetic_only: bool = True

    @model_validator(mode="after")
    def shape(self) -> Self:
        import math

        if self.allowed_files != ("candidate.py",):
            raise ValueError("S0 editable scope is candidate.py only")
        if not self.synthetic_only:
            raise ValueError("S0 accepts synthetic tasks only")
        if len(self.row_ids) != len(set(self.row_ids)) or not (
            len(self.row_ids) == len(self.x) == len(self.dev_y)
        ):
            raise ValueError("invalid dev rows")
        if any(not math.isfinite(x) for x in (*self.x, *self.dev_y)):
            raise ValueError("nonfinite dev data")
        return self


class FrozenCandidate(Value):
    path: Path
    sha256: str = Field(pattern=SHA256)


class Prediction(Value):
    candidate_sha256: str = Field(pattern=SHA256)
    row_ids: tuple[str, ...] = Field(min_length=1)
    values: tuple[Annotated[float, Field(allow_inf_nan=False)], ...] = Field(min_length=1)


class MockBudget(Value):
    max_attempts: int = Field(default=4, ge=0, le=4)
    max_trials: int = Field(default=3, ge=0, le=3)
    wall_seconds: float = Field(default=1800, gt=0, le=1800, allow_inf_nan=False)


class EventKind(StrEnum):
    CREATED = "created"
    MODEL_STARTED = "mock_model_started"
    TRIAL_STARTED = "mock_trial_started"
    FINISHED = "finished"
    FAILED = "failed"
    INTERRUPTED = "interrupted_unknown_outcome"
    REJECTED = "rejected"


class OfflineEvent(Value):
    sequence: int = Field(ge=1)
    cell_id: str
    kind: EventKind
    operation: str
    epoch_seconds: float = Field(allow_inf_nan=False)
    timestamp: datetime
    fingerprint: str = ""
    reason: str = ""
    budget: MockBudget | None = None


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def freeze_candidate(path: Path) -> FrozenCandidate:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise ValueError("missing, symlinked or oversized candidate")
    return FrozenCandidate(path=path.absolute(), sha256=digest(path))


def strict_predictions(path: Path) -> Prediction:
    return Prediction.model_validate(read_strict_json(path))


def evaluate_dev(task: PublicTask, candidate: FrozenCandidate, result: Prediction) -> float:
    """Trusted MSE recomputation from numeric predictions; never imports candidate code."""
    if freeze_candidate(candidate.path).sha256 != candidate.sha256:
        raise ValueError("candidate digest changed")
    if result.candidate_sha256 != candidate.sha256:
        raise ValueError("prediction candidate mismatch")
    if result.row_ids != task.row_ids or len(result.values) != len(task.dev_y):
        raise ValueError("prediction row mismatch")
    return sum(
        (pred - true) ** 2 for pred, true in zip(result.values, task.dev_y, strict=True)
    ) / len(task.dev_y)


class Journal:
    """Single-writer locked, fsynced append-only reservations. Interrupted work stays spent."""

    def __init__(
        self, path: Path, budget: MockBudget, cell_id: str, *, now: Callable[[], float] = time.time
    ) -> None:
        self.path, self.budget, self.cell_id, self.now = path, budget, cell_id, now
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            if not path.exists():
                self._append(EventKind.CREATED, "cell", budget=budget)
            events = self.events()
            if events[0].budget != budget or any(e.cell_id != cell_id for e in events):
                raise ValueError("journal identity or immutable budget mismatch")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock = self.path.with_suffix(".lock")
        if self.path.is_symlink() or lock.is_symlink():
            raise ValueError("symlinked journal")
        with lock.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def events(self) -> list[OfflineEvent]:
        if not self.path.exists():
            return []
        if self.path.is_symlink() or self.path.stat().st_size > 4_194_304:
            raise ValueError("unsafe journal")
        events = [
            OfflineEvent.model_validate(
                json.loads(
                    line,
                    object_pairs_hook=_no_duplicate_properties,
                    parse_constant=_reject_constant,
                )
            )
            for line in self.path.read_text().splitlines()
        ]
        if any(e.sequence != i for i, e in enumerate(events, 1)):
            raise ValueError("journal sequence mismatch")
        return events

    def _append(
        self,
        kind: EventKind,
        operation: str,
        *,
        fingerprint: str = "",
        reason: str = "",
        budget: MockBudget | None = None,
    ) -> None:
        event = OfflineEvent(
            sequence=len(self.events()) + 1,
            cell_id=self.cell_id,
            kind=kind,
            operation=operation,
            epoch_seconds=self.now(),
            timestamp=datetime.now(UTC),
            fingerprint=fingerprint,
            reason=reason,
            budget=budget,
        )
        with self.path.open("a") as handle:
            handle.write(event.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def counts(self) -> tuple[int, int]:
        events = self.events()
        return (
            sum(e.kind == EventKind.MODEL_STARTED for e in events),
            sum(e.kind == EventKind.TRIAL_STARTED for e in events),
        )

    def _pending(self) -> set[str]:
        events = self.events()
        started = {
            e.operation
            for e in events
            if e.kind in {EventKind.MODEL_STARTED, EventKind.TRIAL_STARTED}
        }
        finished = {
            e.operation
            for e in events
            if e.kind in {EventKind.FINISHED, EventKind.FAILED, EventKind.INTERRUPTED}
        }
        return started - finished

    def start(self, operation: str, kind: EventKind, *, fingerprint: str = "") -> None:
        with self._locked():
            events = self.events()
            attempts, trials = self.counts()
            reason = ""
            if kind not in {EventKind.MODEL_STARTED, EventKind.TRIAL_STARTED}:
                reason = "invalid reservation kind"
            elif any(e.operation == operation and e.kind != EventKind.REJECTED for e in events):
                reason = "operation already used"
            elif self._pending():
                reason = "unreconciled operation"
            elif not 0 <= self.now() - events[0].epoch_seconds <= self.budget.wall_seconds:
                reason = "wall budget or clock rollback"
            elif kind == EventKind.TRIAL_STARTED and (
                not fingerprint
                or any(e.fingerprint == fingerprint and e.kind == kind for e in events)
            ):
                reason = "duplicate experiment or missing fingerprint"
            elif kind == EventKind.MODEL_STARTED and attempts >= self.budget.max_attempts:
                reason = "attempt budget exhausted"
            elif kind == EventKind.TRIAL_STARTED and trials >= self.budget.max_trials:
                reason = "trial budget exhausted"
            if reason:
                self._append(EventKind.REJECTED, operation, reason=reason)
                raise ValueError(reason)
            self._append(kind, operation, fingerprint=fingerprint)

    def finish(self, operation: str, *, failed: bool = False) -> None:
        with self._locked():
            if operation not in self._pending():
                raise ValueError("no pending operation")
            self._append(EventKind.FAILED if failed else EventKind.FINISHED, operation)

    def reconcile(self, operation: str) -> None:
        with self._locked():
            if operation not in self._pending():
                raise ValueError("no pending operation to reconcile")
            self._append(EventKind.INTERRUPTED, operation, reason="explicit recovery; no retry")
