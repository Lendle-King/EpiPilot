"""Blind-scoring protocol for a separate, trusted PRIVATE evaluator job.

The agent may author a JSON prediction/placement artifact on an isolated runner.
This module does not import or execute candidate code: the scorer consumes only
that artifact and private rows, which must NEVER be mounted in the agent job.
Filesystem isolation and artifact authenticity are the responsibility of the
separate private workflow/security boundary, not of this Python module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from experiments.researchbench_r1.p0_tasks import (
    Job,
    Observation,
    Placement,
    classifier_score,
    score_queue,
)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant: {value}")


def _load(path: Path, limit: int) -> dict[str, Any]:
    """Parse bounded strict JSON; do not execute or evaluate any untrusted code."""
    if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
        raise ValueError("missing, symlinked or oversized JSON")
    data = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_pairs,
        parse_constant=_reject_constant,
    )
    if not isinstance(data, dict):
        raise ValueError("JSON input must be an object")
    return data


def _strict_keys(row: dict[str, Any], keys: set[str]) -> None:
    if set(row) != keys:
        raise ValueError("unexpected or missing submission fields")


def _identity(submission: dict[str, Any], *, task_id: str, kind: str) -> None:
    _strict_keys(submission, {"schema_version", "task_id", "kind", "candidate_sha256", "rows"})
    if submission["schema_version"] != 1 or type(submission["schema_version"]) is not int:
        raise ValueError("unsupported scorer schema")
    if submission["task_id"] != task_id or submission["kind"] != kind:
        raise ValueError("submission targets a different frozen task")
    fingerprint = submission["candidate_sha256"]
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(
        char not in "0123456789abcdef" for char in fingerprint
    ):
        raise ValueError("missing candidate SHA-256 provenance")
    rows = submission["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError("invalid submission row count")


def score_private_classification(
    submission: dict[str, Any],
    *,
    task_id: str,
    private_rows: tuple[Observation, ...],
) -> dict[str, Any]:
    """Compute only aggregate private scores, never expose labels or examples."""
    _identity(submission, task_id=task_id, kind="classification")
    predictions: dict[str, int] = {}
    for row in submission["rows"]:
        if not isinstance(row, dict):
            raise ValueError("prediction row is not an object")
        _strict_keys(row, {"sample_id", "prediction"})
        sample_id, prediction = row["sample_id"], row["prediction"]
        if not isinstance(sample_id, str) or not sample_id or sample_id in predictions:
            raise ValueError("duplicate or invalid prediction ID")
        if type(prediction) is not int or prediction not in (0, 1):
            raise ValueError("prediction must be exactly 0 or 1")
        predictions[sample_id] = prediction
    scores = classifier_score(private_rows, predictions)
    return {"task_id": task_id, "kind": "classification", "scores": scores}


def score_private_queue(
    submission: dict[str, Any], *, task_id: str, private_jobs: tuple[Job, ...]
) -> dict[str, Any]:
    _identity(submission, task_id=task_id, kind="scheduling")
    placements: list[Placement] = []
    for row in submission["rows"]:
        if not isinstance(row, dict):
            raise ValueError("placement row is not an object")
        _strict_keys(row, {"job_id", "server", "start"})
        job_id, server, start = row["job_id"], row["server"], row["start"]
        if not isinstance(job_id, str) or type(server) is not int or (
            isinstance(start, bool) or not isinstance(start, (int, float))
        ):
            raise ValueError("invalid placement values")
        if not math.isfinite(start):
            raise ValueError("nonfinite placement time")
        placements.append(Placement(job_id, server, float(start)))
    value = score_queue(private_jobs, tuple(placements))
    return {"task_id": task_id, "kind": "scheduling", "scores": {
        "mean_weighted_tardiness": value
    }}


def private_score_file(
    submission_path: Path,
    rows_path: Path,
    *,
    task_id: str,
    kind: str,
) -> dict[str, Any]:
    """Use only the reviewed scorer and private data; no arbitrary code execution."""
    submission = _load(submission_path, 2 * 1024 * 1024)
    source_bytes = submission_path.read_bytes()
    private = _load(rows_path, 8 * 1024 * 1024)
    _strict_keys(private, {"schema_version", "task_id", "kind", "rows"})
    if private["schema_version"] != 1 or type(private["schema_version"]) is not int:
        raise ValueError("unsupported private data schema")
    if private["task_id"] != task_id or private["kind"] != kind:
        raise ValueError("private rows do not match the scored task")
    rows = private["rows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError("invalid private row count")
    if kind == "classification":
        samples = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid private row")
            _strict_keys(row, {"id", "stable_a", "stable_b", "nuisance", "group", "label"})
            if type(row["group"]) is not int or row["group"] not in (0, 1):
                raise ValueError("invalid private group")
            if type(row["label"]) is not int or row["label"] not in (0, 1):
                raise ValueError("invalid private label")
            for key in ("stable_a", "stable_b", "nuisance"):
                if isinstance(row[key], bool) or not isinstance(row[key], (int, float)):
                    raise ValueError("invalid private numerical feature")
                if not math.isfinite(row[key]):
                    raise ValueError("nonfinite private feature")
            samples.append(Observation(**row))
        result = score_private_classification(
            submission, task_id=task_id, private_rows=tuple(samples)
        )
    elif kind == "scheduling":
        jobs = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid private job row")
            _strict_keys(row, {"id", "release", "duration", "deadline", "weight", "accelerator"})
            if type(row["accelerator"]) is not bool:
                raise ValueError("invalid private accelerator")
            for key in ("release", "duration", "deadline", "weight"):
                if isinstance(row[key], bool) or not isinstance(row[key], (int, float)):
                    raise ValueError("invalid job field")
                if not math.isfinite(row[key]):
                    raise ValueError("nonfinite job field")
            jobs.append(Job(**row))
        result = score_private_queue(
            submission, task_id=task_id, private_jobs=tuple(jobs)
        )
    else:
        raise ValueError("unsupported task type")
    return {
        **result,
        "submission_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "candidate_sha256": submission["candidate_sha256"],
        "qualification": (
            "Aggregated blind score of frozen prediction artifact; scorer did not "
            "execute candidate code. Authentic isolation must be audited in private CI."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Private, prediction-only R1 scoring")
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--private-rows", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--kind", choices=["classification", "scheduling"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Invoke this only in the separate PRIVATE scorer job, never in an agent sandbox.
    report = private_score_file(
        args.submission, args.private_rows, task_id=args.task_id, kind=args.kind
    )
    if args.output.exists():
        raise ValueError("private score output already exists")
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
