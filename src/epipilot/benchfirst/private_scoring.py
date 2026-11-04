"""Numeric-only private RMSE scorer. It never imports or executes candidate code.

Deploy under a controller/scorer identity with no agent access. The signed payload binds
the scorer, private labels, closed session, candidate and predictions. HMAC proves origin
only to a verifier holding the separate key; it is not a public-key signature or an OS sandbox.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import math
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from epipilot.benchfirst.contracts import SHA256
from epipilot.benchfirst.offline import Value


class LabelSet(Value):
    row_ids: tuple[str, ...] = Field(min_length=1)
    values: tuple[Annotated[float, Field(allow_inf_nan=False)], ...]

    @model_validator(mode="after")
    def shape(self) -> Self:
        if len(self.row_ids) != len(self.values) or len(set(self.row_ids)) != len(self.row_ids):
            raise ValueError("invalid labels")
        return self


class ClosedRun(Value):
    cell_id: str = Field(min_length=1)
    track: Literal["native", "matched", "calibration"]
    task_sha256: str = Field(pattern=SHA256)
    candidate_sha256: str = Field(pattern=SHA256)
    predictions_sha256: str = Field(pattern=SHA256)
    closure_sha256: str = Field(pattern=SHA256)


class SignedScore(Value):
    run: ClosedRun
    evaluator_sha256: str = Field(pattern=SHA256)
    labels_sha256: str = Field(pattern=SHA256)
    rmse: float = Field(ge=0, allow_inf_nan=False)
    mac: str = Field(pattern=SHA256)


def canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class SealedScorer:
    def __init__(self, labels: LabelSet, evaluator_sha256: str, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("scorer key must be at least 32 bytes")
        self.labels, self.evaluator_sha256, self.key = labels, evaluator_sha256, key

    def score(self, run: ClosedRun, prediction: Path) -> SignedScore:
        if prediction.is_symlink() or not prediction.is_file():
            raise ValueError("unsafe prediction path")
        # Snapshot once: validate and score the same bytes, avoiding a hash/read race.
        with prediction.open("rb") as handle:
            raw = handle.read(8_388_609)
        if len(raw) > 8_388_608 or hashlib.sha256(raw).hexdigest() != run.predictions_sha256:
            raise ValueError("prediction digest or size mismatch")
        reader = csv.reader(io.StringIO(raw.decode("utf-8")), strict=True)
        if next(reader, []) != ["row_id", "prediction"]:
            raise ValueError("invalid prediction header")
        rows = list(reader)
        if any(len(row) != 2 for row in rows):
            raise ValueError("invalid prediction shape")
        if tuple(row[0] for row in rows) != self.labels.row_ids:
            raise ValueError("prediction row mismatch")
        values = [float(row[1]) for row in rows]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("nonfinite prediction")
        rmse = math.hypot(
            *(p - y for p, y in zip(values, self.labels.values, strict=True))
        ) / math.sqrt(len(values))
        payload: dict[str, object] = {
            "run": run.model_dump(mode="json"),
            "evaluator_sha256": self.evaluator_sha256,
            "labels_sha256": hashlib.sha256(self.labels.model_dump_json().encode()).hexdigest(),
            "rmse": rmse,
        }
        return SignedScore.model_validate(
            {**payload, "mac": hmac.new(self.key, canonical(payload), "sha256").hexdigest()}
        )


def verify_score(score: SignedScore, key: bytes) -> bool:
    payload = score.model_dump(mode="json", exclude={"mac"})
    return hmac.compare_digest(score.mac, hmac.new(key, canonical(payload), "sha256").hexdigest())
