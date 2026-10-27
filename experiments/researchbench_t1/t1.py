"""EpiPilot ResearchBench-T1: cost-capped tabular research-policy qualification.

The reference and smoke stages are offline. Explicit Pilot mode can call the
user's Codex CLI through the guarded Mac runner; all external-agent phases are
opt-in and a sealed evaluator handles final holdout scoring.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.resources
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

STAGES: dict[str, dict[str, Any]] = {
    "smoke": {
        "datasets": ["wdbc"],
        "arms": ["offline_baseline"],
        "seeds": [42],
        "per_cell_trials": 2,
        "trial_seconds": 12,
        "total_seconds": 30,
        "final_eval_seconds": 15,
    },
    "pilot": {
        "datasets": ["wdbc"],
        "arms": ["agent_only", "memory_only", "epipilot_heuristic", "epipilot_conditional"],
        "seeds": [42, 137],
        "per_cell_trials": 2,
        "trial_seconds": 15,
        "total_seconds": 240,
        "max_model_decision_calls_per_cell": 4,
        "model": "gpt-6-luna",
        "model_reasoning_effort": "max",
        "final_eval_seconds": 20,
    },
    "full": {
        "datasets": ["adult", "bank", "magic"],
        "arms": ["agent_only", "memory_only", "epipilot_heuristic", "epipilot_conditional"],
        "seeds": [42, 137, 271],
        "per_cell_trials": 2,
        "trial_seconds": 20,
        "total_seconds": 900,
        "final_eval_seconds": 35,
    },
}
SPLIT_SEED = 20271025
EXECUTION_ORDER_SEED = 20271027
SCHEMA = 4


class BenchmarkError(ValueError):
    """A research run violated its frozen benchmark contract."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    return digest(path.read_bytes())


def _code_revision() -> str | None:
    repo = Path(__file__).resolve().parents[2]
    try:
        revision = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, stderr=subprocess.DEVNULL, timeout=5
            )
            .decode("ascii")
            .strip()
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    return (
        revision if len(revision) == 40 and all(c in "0123456789abcdef" for c in revision) else None
    )


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise BenchmarkError(f"missing or oversized metadata: {path}")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise BenchmarkError("metadata must be a JSON object")
    return result


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise BenchmarkError(f"refusing to overwrite existing data: {path}")
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, prefix=".partial-", delete=False
        ) as stream:
            tmp = Path(stream.name)
            stream.write(canonical(data))
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def exclusive(root: Path) -> Iterator[None]:
    lock = root / ".t1.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BenchmarkError(
            "T1 is already running or requires explicit crash reconciliation"
        ) from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def ledger(root: Path) -> list[dict[str, Any]]:
    path = root / "events.jsonl"
    if not path.exists():
        return []
    result = []
    for lineno, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            obj = json.loads(text)
        except ValueError as exc:
            raise BenchmarkError(f"invalid append-only event at line {lineno}") from exc
        if not isinstance(obj, dict) or not obj.get("kind"):
            raise BenchmarkError(f"invalid event at line {lineno}")
        result.append(obj)
    return result


def event(root: Path, obj: dict[str, Any]) -> None:
    with (root / "events.jsonl").open("ab") as output:
        output.write(canonical(obj) + b"\n")
        output.flush()
        os.fsync(output.fileno())


def _dependencies() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np
        import pandas as pd
        import sklearn
        from sklearn.model_selection import train_test_split
    except ImportError as exc:
        raise BenchmarkError("install optional scikit-learn, pandas, numpy for T1") from exc
    return np, pd, sklearn, train_test_split


def load_data(dataset: str, source: Path | None) -> tuple[Any, Any, str]:
    """Load real data offline. Raw UCI files never enter the public workspace."""
    np, pd, _, _ = _dependencies()
    if dataset == "wdbc":
        from sklearn.datasets import load_breast_cancer

        bunch = load_breast_cancer(as_frame=True)
        data = bunch.data.copy()
        labels = bunch.target.copy()
        return data, labels, file_digest(_source_path(dataset, source))
    if source is None or not source.is_file():
        raise BenchmarkError(
            f"{dataset}: supply an existing local raw UCI file; no download is attempted"
        )
    if source.stat().st_size > 40 * 1024 * 1024:
        raise BenchmarkError("refusing oversized external dataset")
    if dataset == "adult":
        cols = [
            "age",
            "workclass",
            "fnlwgt",
            "education",
            "education_num",
            "marital_status",
            "occupation",
            "relationship",
            "race",
            "sex",
            "capital_gain",
            "capital_loss",
            "hours_per_week",
            "native_country",
            "target",
        ]
        frame = pd.read_csv(source, header=None, names=cols, skipinitialspace=True)
        frame = frame.replace("?", np.nan)
        target = frame.pop("target").astype(str).str.strip().str.rstrip(".")
        if set(target.unique()) != {"<=50K", ">50K"}:
            raise BenchmarkError("Adult class vocabulary not recognized; use adult.data")
        labels = target.eq(">50K").astype(int)
    elif dataset == "bank":
        frame = pd.read_csv(source, sep=";")
        if "y" not in frame or "duration" not in frame:
            raise BenchmarkError(
                "Bank Marketing requires bank-full.csv with y and duration columns"
            )
        target = frame.pop("y").astype(str).str.strip()
        if set(target.unique()) != {"yes", "no"}:
            raise BenchmarkError("Bank label vocabulary not recognized")
        frame = frame.drop(columns=["duration"])  # unavailable before the call outcome
        labels = target.eq("yes").astype(int)
    elif dataset == "magic":
        frame = pd.read_csv(source, header=None)
        if frame.shape[1] != 11:
            raise BenchmarkError("MAGIC requires the original 11-column magic04.data")
        frame.columns = [f"feature_{i}" for i in range(10)] + ["target"]
        target = frame.pop("target").astype(str).str.strip()
        if set(target.unique()) != {"g", "h"}:
            raise BenchmarkError("MAGIC class vocabulary not recognized")
        labels = target.eq("g").astype(int)
    else:
        raise BenchmarkError("unknown dataset")
    if frame.empty or len(frame) < 100:
        raise BenchmarkError("dataset too small to build a stable study")
    return frame, labels, file_digest(source)


def _source_path(dataset: str, source: Path | None) -> Path:
    if dataset == "wdbc":
        return Path(
            str(importlib.resources.files("sklearn.datasets.data").joinpath("breast_cancer.csv"))
        )
    if source is None or not source.is_file():
        raise BenchmarkError(f"{dataset}: supply an existing local raw UCI file")
    return source


def _dataset_manifest(dataset: str, source: Path | None, split_seed: int) -> dict[str, Any]:
    """Freeze the preregistered stratified split; this trusted controller keeps holdout private."""
    np, pd, sklearn, splitter = _dependencies()
    features, target, source_hash = load_data(dataset, source)
    y = pd.Series(np.asarray(target, dtype=int), name="target")
    if len(y.unique()) != 2 or y.value_counts().min() < 20:
        raise BenchmarkError("binary dataset needs two classes with at least 20 members each")
    left, test = splitter(np.arange(len(y)), test_size=0.2, stratify=y, random_state=split_seed)
    train, dev = splitter(left, test_size=0.25, stratify=y.iloc[left], random_state=split_seed + 1)
    data = features.reset_index(drop=True).copy()
    data.columns = [str(value) for value in data.columns]
    if "target" in data.columns:
        raise BenchmarkError("features contain target column")
    data["target"] = y
    frames = {
        "train": data.iloc[train].reset_index(drop=True),
        "dev": data.iloc[dev].reset_index(drop=True),
        "test": data.iloc[test].reset_index(drop=True),
    }
    files: dict[str, Any] = {}
    for name, frame in frames.items():
        rendered = io.StringIO()
        frame.to_csv(rendered, index=False, float_format="%.15g")
        file_meta = (
            {"rows": len(frame)}
            if name == "test"
            else {"sha256": digest(rendered.getvalue().encode("utf-8")), "rows": len(frame)}
        )
        if name == "test":
            file_meta["materialized"] = False
            holdout_hash = digest(rendered.getvalue().encode("utf-8"))
        files[name] = file_meta
    indices = {
        "train": [int(value) for value in train],
        "dev": [int(value) for value in dev],
        "test": [int(value) for value in test],
    }
    return {
        "dataset": dataset,
        "source_hash": source_hash,
        "split_seed": split_seed,
        "split_algorithm": (
            "sklearn.train_test_split stratified 60/20/20, random_state=split_seed "
            "then split_seed+1"
        ),
        "rows": len(y),
        "features": len(frames["train"].columns) - 1,
        "versions": {
            "sklearn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "files": files,
        "_indices": indices,
        "_frames": frames,
        "_holdout_sha256": holdout_hash,
    }


def _write_dataset(
    work: Path, dataset: str, source: Path | None, split_seed: int
) -> tuple[dict[str, Any], dict[str, list[int]], str]:
    meta = _dataset_manifest(dataset, source, split_seed)
    frames = meta.pop("_frames")
    indices = meta.pop("_indices")
    holdout_hash = meta.pop("_holdout_sha256")
    public_dir = work / "public" / dataset
    public_dir.mkdir(parents=True, exist_ok=False)
    for name in ("train", "dev"):
        path = public_dir / f"{name}.csv"
        rendered = io.StringIO()
        frames[name].to_csv(rendered, index=False, float_format="%.15g")
        path.write_text(rendered.getvalue(), encoding="utf-8")
        if file_digest(path) != meta["files"][name]["sha256"]:
            raise BenchmarkError("public split serialization changed during setup")
    return meta, indices, holdout_hash


def setup(
    work: Path, vault: Path, stage: str, sources: dict[str, Path], opt_in: bool
) -> dict[str, Any]:
    if stage not in STAGES:
        raise BenchmarkError("invalid T1 stage")
    if stage != "smoke" and not opt_in:
        raise BenchmarkError("pilot/full must be explicitly enabled; smoke is the default")
    # Check raw inputs BEFORE optional ML imports or creating a workspace.
    # A missing UCI file must never trigger a network fetch or partial setup.
    for dataset in STAGES[stage]["datasets"]:
        if dataset == "wdbc":
            continue
        source = sources.get(dataset)
        if source is None or not source.is_file():
            raise BenchmarkError(
                f"{dataset}: supply an existing local raw UCI file; no download is attempted"
            )
    profile = STAGES[stage]
    code_revision = _code_revision()
    if stage == "pilot" and code_revision is None:
        raise BenchmarkError("Pilot requires a committed Git revision before any model call")
    cell_count, per_cell_seconds, required_seconds = _budget_allocation(profile)
    if profile["total_seconds"] < required_seconds:
        raise BenchmarkError(
            "global safety cap cannot fully fund equal per-cell training allowances: "
            f"{cell_count} cells x {per_cell_seconds}s requires {required_seconds}s, "
            f"but the frozen cap is {profile['total_seconds']}s"
        )
    work, vault = work.resolve(), vault.resolve()
    if work == vault or work in vault.parents or vault in work.parents:
        raise BenchmarkError("public workspace and private vault must be disjoint")
    if work.exists() and any(work.iterdir()):
        raise BenchmarkError("workspace exists and is nonempty")
    if vault.exists() and any(vault.iterdir()):
        raise BenchmarkError("private vault exists and is nonempty")
    work.mkdir(parents=True, exist_ok=True)
    vault.mkdir(parents=True, exist_ok=True)
    spec = json.loads(canonical(profile))
    dataset_meta: dict[str, Any] = {}
    private_sources: dict[str, str] = {}
    private_indices: dict[str, dict[str, list[int]]] = {}
    private_holdout_hashes: dict[str, str] = {}
    # Do not reuse the already-inspected smoke holdout in real policy comparisons.
    # Fix the split per stage BEFORE any research agent sees validation feedback.
    split_seed = SPLIT_SEED + {"smoke": 0, "pilot": 1000, "full": 2000}[stage]
    for dataset in spec["datasets"]:
        dataset_meta[dataset], private_indices[dataset], private_holdout_hashes[dataset] = (
            _write_dataset(work, dataset, sources.get(dataset), split_seed)
        )
        source_path = _source_path(dataset, sources.get(dataset))
        private_sources[dataset] = str(source_path.resolve())
    cells = [
        (arm, dataset, seed)
        for dataset in spec["datasets"]
        for seed in spec["seeds"]
        for arm in spec["arms"]
    ]
    random.Random(EXECUTION_ORDER_SEED).shuffle(cells)
    meta = {
        "schema": SCHEMA,
        "stage": stage,
        "code_revision": code_revision,
        "profile": spec,
        "cell_count": cell_count,
        "per_cell_trial_budget_seconds": per_cell_seconds,
        "required_development_seconds_for_all_cells": required_seconds,
        "datasets": dataset_meta,
        "execution_order_seed": EXECUTION_ORDER_SEED,
        "execution_order": [list(cell) for cell in cells],
        "target_auroc": 0.95 if stage == "pilot" else None,
        "total_external_api_calls": 0,
        "final_holdout_is_off_limits_during_search": True,
    }
    atomic_json(work / "meta.json", meta)
    atomic_json(
        vault / "meta.json",
        {
            "public_meta_sha256": file_digest(work / "meta.json"),
            "source_paths": private_sources,
            "test_indices": private_indices,
            "test_sha256": private_holdout_hashes,
        },
    )
    return {
        "stage": stage,
        "work": str(work),
        "vault": str(vault),
        "profile": spec,
        "dataset_rows": {k: v["rows"] for k, v in dataset_meta.items()},
        "api_calls": 0,
    }


def normalize_candidate(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or "model" not in raw or "feature_mode" not in raw:
        raise BenchmarkError("candidate needs model and feature_mode")
    allowed = {
        "logistic": {"model", "feature_mode", "C", "class_weight"},
        "forest": {
            "model",
            "feature_mode",
            "n_estimators",
            "max_depth",
            "min_samples_leaf",
            "class_weight",
        },
    }
    name = raw["model"]
    if name not in allowed or set(raw) != allowed[name]:
        raise BenchmarkError("unapproved estimator or inconsistent parameter schema")
    if raw["feature_mode"] not in ("all", "numeric_only") or raw["class_weight"] not in (
        "none",
        "balanced",
    ):
        raise BenchmarkError("invalid feature_mode or class_weight")
    if name == "logistic":
        c = raw["C"]
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0.01 <= c <= 10:
            raise BenchmarkError("logistic C outside approved range")
    else:
        n, depth, leaf = raw["n_estimators"], raw["max_depth"], raw["min_samples_leaf"]
        if any(type(x) is not int for x in (n, depth, leaf)) or not (
            20 <= n <= 120 and 2 <= depth <= 16 and 1 <= leaf <= 10
        ):
            raise BenchmarkError("forest parameters outside approved range")
    return dict(sorted(raw.items()))


def _fit(train: Path, evaluation: Path, raw: dict[str, Any], seed: int, fit_all: bool) -> float:
    _, pd, _, _ = _dependencies()
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    candidate = normalize_candidate(raw)
    train_frame = pd.read_csv(train)
    eval_frame = pd.read_csv(evaluation)
    y_train = train_frame.pop("target")
    y_eval = eval_frame.pop("target")
    if set(y_train.unique()) != {0, 1} or set(y_eval.unique()) != {0, 1}:
        raise BenchmarkError("invalid target distribution for AUROC")
    if candidate["feature_mode"] == "numeric_only":
        cols = list(train_frame.select_dtypes(include="number").columns)
        if not cols:
            raise BenchmarkError("no numeric features")
        train_frame = train_frame[cols]
        eval_frame = eval_frame[cols]
    numerics = list(train_frame.select_dtypes(include="number").columns)
    categories = [c for c in train_frame.columns if c not in numerics]
    transformers: list[tuple[str, Any, list[str]]] = []
    if numerics:
        transformers.append(
            ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), numerics)
        )
    if categories:
        transformers.append(
            (
                "cat",
                make_pipeline(
                    SimpleImputer(strategy="most_frequent"), OneHotEncoder(handle_unknown="ignore")
                ),
                categories,
            )
        )
    if not transformers:
        raise BenchmarkError("empty design matrix")
    preprocessor = ColumnTransformer(transformers)
    weight = None if candidate["class_weight"] == "none" else "balanced"
    if candidate["model"] == "logistic":
        model = LogisticRegression(
            C=candidate["C"],
            max_iter=250,
            class_weight=weight,
            random_state=seed,
            solver="liblinear",
        )
    else:
        model = RandomForestClassifier(
            n_estimators=candidate["n_estimators"],
            max_depth=candidate["max_depth"],
            min_samples_leaf=candidate["min_samples_leaf"],
            class_weight=weight,
            n_jobs=1,
            random_state=seed,
        )
    pipeline = make_pipeline(preprocessor, model)
    pipeline.fit(train_frame, y_train)
    probabilities = pipeline.predict_proba(eval_frame)[:, 1]
    return float(roc_auc_score(y_eval, probabilities))


def _run_worker(args: argparse.Namespace) -> int:
    try:
        candidate = read_json(Path(args.candidate))
        score = _fit(Path(args.train), Path(args.eval), candidate, args.seed, False)
        atomic_json(Path(args.output), {"auroc": score})
        return 0
    except Exception as exc:
        print(f"T1 worker error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _load_meta(work: Path) -> dict[str, Any]:
    meta = read_json(work / "meta.json")
    if meta.get("schema") != SCHEMA or meta.get("profile") != STAGES.get(meta.get("stage")):
        raise BenchmarkError("unsupported or modified experiment budget profile")
    return meta


def _unit(meta: dict[str, Any], arm: str, dataset: str, seed: int) -> None:
    profile = meta["profile"]
    if (
        arm not in profile["arms"]
        or dataset not in profile["datasets"]
        or seed not in profile["seeds"]
    ):
        raise BenchmarkError("arm/dataset/seed outside preregistered task matrix")


def _budget_allocation(profile: dict[str, Any]) -> tuple[int, int, int]:
    """Return cell count, independent per-cell cap, and full equal-allocation total."""
    cell_count = len(profile["arms"]) * len(profile["datasets"]) * len(profile["seeds"])
    per_cell_seconds = profile["per_cell_trials"] * profile["trial_seconds"]
    return cell_count, per_cell_seconds, cell_count * per_cell_seconds


def _spent(events: list[dict[str, Any]], timeout: int) -> float:
    ended = {e["id"] for e in events if e["kind"] in ("finished", "aborted")}
    return sum(float(e["seconds"]) for e in events if e["kind"] in ("finished", "aborted")) + sum(
        timeout for e in events if e["kind"] == "started" and e["id"] not in ended
    )


def _spent_by_cell(
    events: list[dict[str, Any]], timeout: int, arm: str, dataset: str, seed: int
) -> float:
    """Charge only this cell's finished work and reserve full timeout for an open run."""
    started = {e["id"]: e for e in events if e["kind"] == "started"}
    ended = {e["id"]: e for e in events if e["kind"] in ("finished", "aborted")}
    ids = {
        run_id
        for run_id, event in started.items()
        if (event["arm"], event["dataset"], event["seed"]) == (arm, dataset, seed)
    }
    return sum(float(ended[run_id]["seconds"]) for run_id in ids if run_id in ended) + sum(
        timeout for run_id in ids if run_id not in ended
    )


def record_agent_call_started(
    work: Path,
    arm: str,
    dataset: str,
    seed: int,
    call_id: str,
    purpose: str,
    model: str = "gpt-6-luna",
    reasoning_effort: str = "max",
) -> int:
    """Reserve one CLI model-call attempt before launch; failed calls still consume it."""
    work = work.resolve()
    meta = _load_meta(work)
    _unit(meta, arm, dataset, seed)
    if (
        meta["stage"] != "pilot"
        or not call_id
        or not purpose
        or model != meta["profile"]["model"]
        or reasoning_effort != meta["profile"]["model_reasoning_effort"]
    ):
        raise BenchmarkError("model calls are permitted only for frozen Pilot cells")
    with exclusive(work):
        history = ledger(work)
        starts = [e for e in history if e["kind"] == "agent_call_started"]
        ends = {e["call_id"] for e in history if e["kind"] == "agent_call_finished"}
        if any(e["call_id"] not in ends for e in starts):
            raise BenchmarkError("previous model call is unreconciled; do not retry")
        cell_starts = [
            e for e in starts if (e["arm"], e["dataset"], e["seed"]) == (arm, dataset, seed)
        ]
        cap = meta["profile"]["max_model_decision_calls_per_cell"]
        recovery = meta.get("recovery", {})
        prior_counts = recovery.get("prior_call_attempts_by_cell", {})
        prior_cell_calls = int(prior_counts.get(f"{arm}__{dataset}__{seed}", 0))
        prior_total_calls = sum(int(value) for value in prior_counts.values())
        prior_call_ids = set(recovery.get("prior_call_ids", []))
        if prior_cell_calls + len(cell_starts) >= cap:
            raise BenchmarkError("per-cell model decision-call budget exhausted")
        if prior_total_calls + len(starts) >= meta["cell_count"] * cap:
            raise BenchmarkError("global model decision-call safety ceiling exhausted")
        if call_id in prior_call_ids or any(e["call_id"] == call_id for e in starts):
            raise BenchmarkError("duplicate model call identifier")
        ordinal = prior_cell_calls + len(cell_starts) + 1
        event(
            work,
            {
                "kind": "agent_call_started",
                "call_id": call_id,
                "call_ordinal": ordinal,
                "arm": arm,
                "dataset": dataset,
                "seed": seed,
                "purpose": purpose,
                "model": model,
                "model_reasoning_effort": reasoning_effort,
            },
        )
        return ordinal


def record_agent_call_finished(
    work: Path, call_id: str, outcome: str, telemetry: dict[str, Any] | None = None
) -> None:
    """Close exactly one reserved call without storing raw CLI output or transcripts."""
    if outcome not in {"success", "invalid_output", "failed", "timeout", "tool_use_rejected"}:
        raise BenchmarkError("invalid model-call outcome")
    safe = telemetry or {}
    allowed = {
        "elapsed_seconds",
        "turn_count",
        "tool_event_count",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "usage_available",
        "retry_controls",
        "process_started",
        "exit_code",
        "failure_classification",
        "stderr_excerpt_redacted",
        "stdout_event_types",
        "stdout_error_events",
    }
    if set(safe) - allowed:
        raise BenchmarkError("unexpected model-call telemetry field")
    work = work.resolve()
    with exclusive(work):
        history = ledger(work)
        starts = [
            e for e in history if e["kind"] == "agent_call_started" and e["call_id"] == call_id
        ]
        ends = [
            e for e in history if e["kind"] == "agent_call_finished" and e["call_id"] == call_id
        ]
        if len(starts) != 1 or ends:
            raise BenchmarkError("model call is missing or already reconciled")
        event(
            work,
            {"kind": "agent_call_finished", "call_id": call_id, "outcome": outcome, **safe},
        )


def record_cell_terminal(
    work: Path,
    arm: str,
    dataset: str,
    seed: int,
    outcome: str,
    reason: str = "",
) -> None:
    """Seal a research cell after all its Codex subprocesses and trials are finished."""
    allowed = {"submitted", "stopped", "failed", "invalid"}
    if outcome not in allowed:
        raise BenchmarkError("invalid terminal cell outcome")
    work = work.resolve()
    meta = _load_meta(work)
    _unit(meta, arm, dataset, seed)
    with exclusive(work):
        history = ledger(work)
        cell = (arm, dataset, seed)
        terminal = [
            e
            for e in history
            if e["kind"] == "cell_terminal" and (e["arm"], e["dataset"], e["seed"]) == cell
        ]
        cell_starts = [
            e
            for e in history
            if e["kind"] == "agent_call_started" and (e["arm"], e["dataset"], e["seed"]) == cell
        ]
        call_ends = {e["call_id"] for e in history if e["kind"] == "agent_call_finished"}
        trial_starts = [
            e
            for e in history
            if e["kind"] == "started" and (e["arm"], e["dataset"], e["seed"]) == cell
        ]
        trial_ends = {e["id"] for e in history if e["kind"] in ("finished", "aborted")}
        if (
            terminal
            or any(e["call_id"] not in call_ends for e in cell_starts)
            or any(e["id"] not in trial_ends for e in trial_starts)
        ):
            raise BenchmarkError(
                "cell is already terminal or has an unreconciled call/training attempt"
            )
        event(
            work,
            {
                "kind": "cell_terminal",
                "arm": arm,
                "dataset": dataset,
                "seed": seed,
                "outcome": outcome,
                "reason": reason[:300],
            },
        )


def perform_trial(
    work: Path, arm: str, dataset: str, seed: int, candidate_path: Path
) -> dict[str, Any]:
    work = work.resolve()
    meta = _load_meta(work)
    _unit(meta, arm, dataset, seed)
    profile = meta["profile"]
    proposed = normalize_candidate(read_json(candidate_path))
    c_hash = digest(canonical(proposed))
    with exclusive(work):
        history = ledger(work)
        started = [e for e in history if e["kind"] == "started"]
        finished = {e["id"] for e in history if e["kind"] in ("finished", "aborted")}
        if any(e["id"] not in finished for e in started):
            raise BenchmarkError("unreconciled interrupted trial; do not restart automatically")
        matches = [
            e for e in started if (e["arm"], e["dataset"], e["seed"]) == (arm, dataset, seed)
        ]
        if len(matches) >= profile["per_cell_trials"]:
            raise BenchmarkError("per-cell experiment count budget exhausted")
        if any(e["candidate_sha256"] == c_hash for e in matches):
            raise BenchmarkError("identical candidate already attempted for this research cell")
        # Enforce the same independent training allowance for every cell first.
        per_cell_seconds = profile["per_cell_trials"] * profile["trial_seconds"]
        if (
            _spent_by_cell(history, profile["trial_seconds"], arm, dataset, seed)
            + profile["trial_seconds"]
            > per_cell_seconds + 1e-9
        ):
            raise BenchmarkError("per-cell CPU wall-budget exhausted, cannot reserve trial")
        # The global cap is only a final safety ceiling. setup() refuses any stage
        # whose cap cannot cover every cell's full equal allocation.
        if (
            _spent(history, profile["trial_seconds"]) + profile["trial_seconds"]
            > profile["total_seconds"] + 1e-9
        ):
            raise BenchmarkError("global CPU wall safety cap exhausted, cannot reserve trial")
        run_id = f"trial-{len(started) + 1:04d}"
        run_dir = work / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        atomic_json(run_dir / "candidate.json", proposed)
        event(
            work,
            {
                "kind": "started",
                "id": run_id,
                "arm": arm,
                "dataset": dataset,
                "seed": seed,
                "candidate_sha256": c_hash,
                "time_limit": profile["trial_seconds"],
            },
        )
        train = work / "public" / dataset / "train.csv"
        dev = work / "public" / dataset / "dev.csv"
        dataset_meta = meta["datasets"][dataset]["files"]
        if (
            file_digest(train) != dataset_meta["train"]["sha256"]
            or file_digest(dev) != dataset_meta["dev"]["sha256"]
        ):
            raise BenchmarkError("public split was modified; run remains reserved until reviewed")
        argv = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_worker",
            "--train",
            str(train),
            "--eval",
            str(dev),
            "--candidate",
            str(run_dir / "candidate.json"),
            "--seed",
            str(seed),
            "--output",
            str(run_dir / "metric.json"),
        ]
        environment = dict(os.environ)
        environment.update(
            {
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            }
        )
        begin = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                timeout=profile["trial_seconds"],
                capture_output=True,
                text=True,
                cwd=work,
                env=environment,
                check=False,
            )
            exit_code = result.returncode
            stderr = result.stderr[:400]
        except subprocess.TimeoutExpired:
            exit_code, stderr = 124, "time budget exceeded"
        seconds = time.monotonic() - begin
        metric_path = run_dir / "metric.json"
        measured = read_json(metric_path) if exit_code == 0 and metric_path.exists() else None
        score = None if measured is None else measured["auroc"]
        if score is not None and (type(score) is not float or not 0 <= score <= 1):
            exit_code, score, stderr = 2, None, "invalid AUROC result"
        receipt = {
            "kind": "finished",
            "id": run_id,
            "seconds": round(seconds, 6),
            "exit_code": exit_code,
            "dev_auroc": score,
            "metric_sha256": (file_digest(metric_path) if metric_path.exists() else None),
            "error": stderr,
        }
        event(work, receipt)
        return {
            **receipt,
            "dataset": dataset,
            "seed": seed,
            "arm": arm,
            "remaining_wall_seconds": max(
                0, profile["total_seconds"] - _spent(ledger(work), profile["trial_seconds"])
            ),
            "remaining_cell_seconds": max(
                0,
                per_cell_seconds
                - _spent_by_cell(ledger(work), profile["trial_seconds"], arm, dataset, seed),
            ),
        }


def recover_failed_trial(work: Path, run_id: str, reason: str) -> dict[str, Any]:
    if len(reason.strip()) < 10:
        raise BenchmarkError("recovery requires a meaningful reason")
    work = work.resolve()
    meta = _load_meta(work)
    with exclusive(work):
        history = ledger(work)
        ids = [e["id"] for e in history if e["kind"] == "started"]
        ended = {e["id"] for e in history if e["kind"] in ("finished", "aborted")}
        if run_id not in ids or run_id in ended:
            raise BenchmarkError("only an uncompleted started trial may be reconciled")
        # Conservatively charge full timeout rather than inventing actual wall time.
        event(
            work,
            {
                "kind": "aborted",
                "id": run_id,
                "seconds": meta["profile"]["trial_seconds"],
                "reason": reason,
                "cost_is_upper_bound": True,
            },
        )
        return {"aborted": run_id, "charged_seconds": meta["profile"]["trial_seconds"]}


def add_memory(
    work: Path, arm: str, dataset: str, seed: int, run_id: str, note: str
) -> dict[str, Any]:
    """A memory-only baseline may reuse scoped lessons grounded in real dev runs."""
    if arm != "memory_only" or not 20 <= len(note.strip()) <= 500:
        raise BenchmarkError("only memory_only may save a bounded, meaningful lesson")
    work = work.resolve()
    meta = _load_meta(work)
    _unit(meta, arm, dataset, seed)
    with exclusive(work):
        history = ledger(work)
        begins = {e["id"]: e for e in history if e["kind"] == "started"}
        ends = {e["id"]: e for e in history if e["kind"] == "finished"}
        if (
            run_id not in ends
            or ends[run_id]["exit_code"] != 0
            or (begins[run_id]["arm"], begins[run_id]["dataset"], begins[run_id]["seed"])
            != (arm, dataset, seed)
        ):
            raise BenchmarkError("memory cannot be based on an unrelated or failed trial")
        notes = [e for e in history if e["kind"] == "memory_note" and e["id"] == run_id]
        if notes:
            raise BenchmarkError("only one memory note is permitted per validated run")
        event(
            work,
            {
                "kind": "memory_note",
                "id": run_id,
                "arm": arm,
                "dataset": dataset,
                "seed": seed,
                "note": note.strip(),
                "dev_auroc": ends[run_id]["dev_auroc"],
            },
        )
    return {"memory_added": run_id, "status": "unverified_agent_note_with_verified_dev_metric"}


def task_prompt(work: Path, arm: str, dataset: str, seed: int) -> str:
    """Human-launchable task instructions; never contacts or launches a paid model."""
    work = work.resolve()
    meta = _load_meta(work)
    _unit(meta, arm, dataset, seed)
    profile = meta["profile"]
    per_cell_seconds = profile["per_cell_trials"] * profile["trial_seconds"]
    max_model_calls = profile.get("max_model_decision_calls_per_cell")
    path = Path(__file__).resolve()
    src = work / "public" / dataset
    instructions = {
        "agent_only": (
            "No EpiPilot CLI or past research memory. "
            "Choose at most two candidates using shared development feedback."
        ),
        "memory_only": (
            "No EpiPilot planner. Reuse only policy-scoped, sourced memory notes, "
            "and record a note after real trials."
        ),
        "epipilot_heuristic": (
            "Use an isolated project-local EpiPilot event store. Register uncertainty, "
            "claims, proposals and evidence; select via epi iterate."
        ),
        "epipilot_conditional": (
            "Use an isolated EpiPilot event store and freeze a four-outcome table. "
            "Select via epi iterate --policy conditional; never silently fall back."
        ),
        "offline_baseline": "Offline baseline smoke only, no agent permitted or required.",
    }
    history = [e for e in ledger(work) if e["kind"] == "memory_note" and e["arm"] == arm]
    notes = (
        "\n".join(
            f"- {e['dataset']}/{e['seed']}/{e['id']}: {e['note']} (dev_AUROC={e['dev_auroc']})"
            for e in history[-6:]
        )
        if arm == "memory_only"
        else "(not exposed to this policy)"
    )
    trial_cmd = (
        f"python {path} trial --work {work} --arm {arm} "
        f"--dataset {dataset} --seed {seed} --candidate YOUR_CANDIDATE.json"
    )
    submit_cmd = f"python {path} submit --work {work} --arm {arm} --dataset {dataset} --seed {seed}"
    call_budget_line = (
        f"At most {max_model_calls} Codex decision calls for this cell, "
        "including failed calls and retries."
        if max_model_calls is not None
        else "No model calls are used by this offline stage."
    )
    return f"""ResearchBench-T1 {meta["stage"]} / {dataset} / seed {seed} / policy {arm}
Goal: maximize held-out AUROC under a hard development experiment budget.
Only train/dev are visible: {src}/train.csv and {src}/dev.csv.
Never read the raw dataset, private holdout, vault path, or final metrics during research.
Do not use web, external APIs, GPU or paid providers without separate authorization.
The harness makes ZERO external model calls; Agent billing is NOT metered here.
At most {profile["per_cell_trials"]} trials per cell.
Each trial has a {profile["trial_seconds"]}-second hard wall-time cap.
Independent per-cell development budget: {per_cell_seconds} worker wall seconds.
The global {profile["total_seconds"]}-second ceiling is a safety cap shared by the stage,
and the frozen profile guarantees it covers every cell's full allocation.
{call_budget_line}
Allowed interventions: logistic/forest parameter JSONs in candidates/.
Instructions for this arm: {instructions[arm]}
Memory notes available: {notes}
To run an experiment:
  {trial_cmd}
Then choose and freeze the strongest development candidate:
  {submit_cmd}
NEVER call finalize from an agent session. Final held-out evaluation is operator-only,
after every arm and seed is frozen. Inspect genuine failures and stop when budget is exhausted.
"""


def select(work: Path, arm: str, dataset: str, seed: int) -> dict[str, Any]:
    work = work.resolve()
    meta = _load_meta(work)
    _unit(meta, arm, dataset, seed)
    with exclusive(work):
        history = ledger(work)
        started = {e["id"]: e for e in history if e["kind"] == "started"}
        outcomes = [
            e
            for e in history
            if e["kind"] == "finished"
            and e["exit_code"] == 0
            and e["dev_auroc"] is not None
            and (started[e["id"]]["arm"], started[e["id"]]["dataset"], started[e["id"]]["seed"])
            == (arm, dataset, seed)
        ]
        if not outcomes:
            raise BenchmarkError("cannot submit without a successful real validation run")
        for observed in outcomes:
            metric_path = work / "runs" / observed["id"] / "metric.json"
            if file_digest(metric_path) != observed["metric_sha256"]:
                raise BenchmarkError("validation artifact changed since the controlled run")
        chosen = max(outcomes, key=lambda e: (e["dev_auroc"], -int(e["id"].split("-")[1])))
        candidate = work / "runs" / chosen["id"] / "candidate.json"
        if file_digest(candidate) != digest(canonical(read_json(candidate))):
            raise BenchmarkError("candidate bytes mismatch")
        result = {
            "arm": arm,
            "dataset": dataset,
            "seed": seed,
            "run_id": chosen["id"],
            "candidate_sha256": file_digest(candidate),
            "dev_auroc": chosen["dev_auroc"],
        }
        path = work / "submissions" / f"{arm}__{dataset}__{seed}.json"
        atomic_json(path, result)
        return result


def status(work: Path) -> dict[str, Any]:
    work = work.resolve()
    meta = _load_meta(work)
    history = ledger(work)
    started = [e for e in history if e["kind"] == "started"]
    finished = [e for e in history if e["kind"] == "finished"]
    model_calls = [e for e in history if e["kind"] == "agent_call_started"]
    call_finishes = [e for e in history if e["kind"] == "agent_call_finished"]
    completed_call_ids = {e["call_id"] for e in call_finishes}
    usage_rows = [e for e in call_finishes if e.get("usage_available") is True]
    process_rows = [e for e in call_finishes if e.get("process_started") is True]
    terminals = [e for e in history if e["kind"] == "cell_terminal"]
    incidents = [e for e in history if e["kind"] == "cell_incident"]
    target = meta.get("target_auroc")
    recovery = meta.get("recovery", {})
    prior_call_counts = recovery.get("prior_call_attempts_by_cell", {})
    prior_process_counts = recovery.get("prior_process_starts_by_cell", {})
    prior_calls = sum(int(value) for value in prior_call_counts.values())
    prior_processes = sum(int(value) for value in prior_process_counts.values())
    return {
        "stage": meta["stage"],
        "total_started": len(started),
        "completed": len(finished),
        "reserved_or_spent_seconds": _spent(history, meta["profile"]["trial_seconds"]),
        "max_total_seconds": meta["profile"]["total_seconds"],
        "per_cell_trial_budget_seconds": meta["profile"]["per_cell_trials"]
        * meta["profile"]["trial_seconds"],
        "required_development_seconds_for_all_cells": meta["cell_count"]
        * meta["per_cell_trial_budget_seconds"],
        "submitted": len(list((work / "submissions").glob("*.json")))
        if (work / "submissions").exists()
        else 0,
        "required_submissions": len(meta["profile"]["datasets"])
        * len(meta["profile"]["arms"])
        * len(meta["profile"]["seeds"]),
        "model_call_attempts": prior_calls + len(model_calls),
        "model_call_attempts_current_run": len(model_calls),
        "model_calls_completed": prior_calls + len(call_finishes),
        "model_calls_open": len(model_calls) - len(completed_call_ids),
        "codex_process_starts": prior_processes + len(process_rows),
        "model_usage": {
            "available_call_count": len(usage_rows),
            "missing_call_count": prior_calls + len(call_finishes) - len(usage_rows),
            "input_tokens": sum(e.get("input_tokens") or 0 for e in usage_rows),
            "output_tokens": sum(e.get("output_tokens") or 0 for e in usage_rows),
            "cached_input_tokens": sum(e.get("cached_input_tokens") or 0 for e in usage_rows),
        },
        "terminal_cells": len(terminals),
        "invalid_events": sum(e["category"] == "invalid" for e in incidents),
        "repeated_experiments": sum(e["category"] == "repeat" for e in incidents),
        "unsupported_claim_events": sum(e["category"] == "unsupported_claim" for e in incidents),
        "target_auroc": target,
        "target_attained_trials": (
            sum(
                e.get("exit_code") == 0
                and e.get("dev_auroc") is not None
                and e["dev_auroc"] >= target
                for e in finished
            )
            if target is not None
            else None
        ),
        "holdout_evaluated": (work / "final.started").exists(),
    }


def record_cell_incident(
    work: Path, arm: str, dataset: str, seed: int, category: str, reason: str
) -> None:
    if category not in {"invalid", "repeat", "unsupported_claim"}:
        raise BenchmarkError("unknown cell incident category")
    work = work.resolve()
    meta = _load_meta(work)
    _unit(meta, arm, dataset, seed)
    with exclusive(work):
        event(
            work,
            {
                "kind": "cell_incident",
                "arm": arm,
                "dataset": dataset,
                "seed": seed,
                "category": category,
                "reason": reason[:300],
            },
        )


def finalize(work: Path, vault: Path, audit: Path | None = None) -> dict[str, Any]:
    work, vault = work.resolve(), vault.resolve()
    meta = _load_meta(work)
    priv = read_json(vault / "meta.json")
    if priv["public_meta_sha256"] != file_digest(work / "meta.json"):
        raise BenchmarkError("evaluation vault is not paired to this frozen public task")
    profile = meta["profile"]
    wanted = [
        (a, d, s) for d in profile["datasets"] for s in profile["seeds"] for a in profile["arms"]
    ]
    audit_data: dict[str, Any] = {}
    if meta["stage"] != "smoke":
        if audit is None:
            raise BenchmarkError(
                "comparison requires explicit non-synthetic intervention/audit records"
            )
        audit_data = read_json(audit)
        if set(audit_data) != {f"{a}__{d}__{s}" for a, d, s in wanted}:
            raise BenchmarkError("audit must cover exactly all benchmark cells")
        for row in audit_data.values():
            if (
                not isinstance(row, dict)
                or set(row) != {"human_interventions", "repeated_experiments", "unsupported_claims"}
                or any(type(x) is not int or x < 0 for x in row.values())
            ):
                raise BenchmarkError("audit counts must be explicit nonnegative integers")
    with exclusive(work):
        if (work / "final.started").exists():
            raise BenchmarkError(
                "sealed holdout has already been evaluated; prevent test-set reuse"
            )
        submissions = {}
        hist = ledger(work)
        if meta["stage"] == "pilot":
            terminals = [e for e in hist if e["kind"] == "cell_terminal"]
            terminal_by_cell = {
                (e["arm"], e["dataset"], e["seed"]): e["outcome"] for e in terminals
            }
            expected_cells = set(wanted)
            if set(terminal_by_cell) != expected_cells or set(terminal_by_cell.values()) != {
                "submitted"
            }:
                raise BenchmarkError(
                    "all Pilot cells must be terminal and submitted before holdout"
                )
        agent_calls = [e for e in hist if e["kind"] == "agent_call_started"]
        finished_calls = {e["call_id"] for e in hist if e["kind"] == "agent_call_finished"}
        if any(e["call_id"] not in finished_calls for e in agent_calls):
            raise BenchmarkError("an agent process is not reconciled; keep the holdout sealed")
        started = {e["id"]: e for e in hist if e["kind"] == "started"}
        finished = {e["id"]: e for e in hist if e["kind"] == "finished"}
        trial_ends = {e["id"] for e in hist if e["kind"] in ("finished", "aborted")}
        if any(run_id not in trial_ends for run_id in started):
            raise BenchmarkError("a training process is unreconciled; keep the holdout sealed")
        for arm, dataset, seed in wanted:
            key = f"{arm}__{dataset}__{seed}"
            selection = read_json(work / "submissions" / f"{key}.json")
            rid = selection["run_id"]
            if (selection["arm"], selection["dataset"], selection["seed"]) != (arm, dataset, seed):
                raise BenchmarkError("selection does not belong to frozen arm and task")
            if rid not in started or rid not in finished or finished[rid]["exit_code"] != 0:
                raise BenchmarkError("selection refers to a non-validated trial")
            if (started[rid]["arm"], started[rid]["dataset"], started[rid]["seed"]) != (
                arm,
                dataset,
                seed,
            ):
                raise BenchmarkError("selection references a different policy or task")
            if selection["dev_auroc"] != finished[rid]["dev_auroc"]:
                raise BenchmarkError("submitted validation score differs from frozen receipt")
            metric_path = work / "runs" / rid / "metric.json"
            if file_digest(metric_path) != finished[rid]["metric_sha256"]:
                raise BenchmarkError("validation artifact changed after submission")
            candidate = work / "runs" / rid / "candidate.json"
            if file_digest(candidate) != selection["candidate_sha256"]:
                raise BenchmarkError("submitted candidate changed after development")
            submissions[key] = (selection, candidate)
        pending_holdouts: dict[str, Any] = {}
        for dataset in profile["datasets"]:
            private_test = vault / dataset / "test.csv"
            if private_test.exists():
                raise BenchmarkError(
                    "private holdout was materialized before final submission freeze"
                )
            source = Path(priv["source_paths"][dataset])
            public_dataset = meta["datasets"][dataset]
            rebuilt = _dataset_manifest(dataset, source, public_dataset["split_seed"])
            frames = rebuilt.pop("_frames")
            indices = rebuilt.pop("_indices")
            rebuilt_holdout_hash = rebuilt.pop("_holdout_sha256")
            if rebuilt != public_dataset:
                raise BenchmarkError(
                    "dataset source or stratified split differs from the frozen manifest"
                )
            if indices != priv["test_indices"][dataset]:
                raise BenchmarkError(
                    "private held-out row membership differs from its vault record"
                )
            for name in ("train", "dev"):
                public_file = work / "public" / dataset / f"{name}.csv"
                if file_digest(public_file) != public_dataset["files"][name]["sha256"]:
                    raise BenchmarkError("public split differs from the frozen source manifest")
            rendered_test = io.StringIO()
            frames["test"].to_csv(rendered_test, index=False, float_format="%.15g")
            if (
                digest(rendered_test.getvalue().encode("utf-8")) != priv["test_sha256"][dataset]
                or rebuilt_holdout_hash != priv["test_sha256"][dataset]
            ):
                raise BenchmarkError("trusted holdout split differs from the private frozen digest")
            pending_holdouts[dataset] = frames["test"]
        atomic_json(
            work / "final.started",
            {
                "submission_hashes": {k: file_digest(v[1]) for k, v in submissions.items()},
                "status": (
                    "all_submissions_frozen_all_agent_calls_reconciled_final_evaluation_started"
                ),
            },
        )
        materialized: dict[str, str] = {}
        for dataset, frame in pending_holdouts.items():
            if set(frame["target"].unique()) != {0, 1}:
                raise BenchmarkError("sealed holdout must contain both classes for AUROC")
            private_test = vault / dataset / "test.csv"
            private_test.parent.mkdir(parents=True, exist_ok=True)
            rendered = io.StringIO()
            frame.to_csv(rendered, index=False, float_format="%.15g")
            private_test.write_text(rendered.getvalue(), encoding="utf-8")
            materialized[dataset] = file_digest(private_test)
        atomic_json(
            vault / "holdout_manifest.json",
            {
                "release_started": file_digest(work / "final.started"),
                "holdout_sha256": materialized,
                "qualification": (
                    "holdout materialized for evaluator only after all cell agents "
                    "terminated and submissions froze"
                ),
            },
        )
        evaluated = {}
        for arm, dataset, seed in wanted:
            key = f"{arm}__{dataset}__{seed}"
            selection, candidate = submissions[key]
            public = work / "public" / dataset
            temp_train = work / ".eval_tmp.csv"
            output = work / "final" / f"{key}.worker.json"
            try:
                _, pd, _, _ = _dependencies()
                train = pd.concat(
                    [pd.read_csv(public / "train.csv"), pd.read_csv(public / "dev.csv")],
                    ignore_index=True,
                )
                train.to_csv(temp_train, index=False)
                cmd = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "_worker",
                    "--train",
                    str(temp_train),
                    "--eval",
                    str(vault / dataset / "test.csv"),
                    "--candidate",
                    str(candidate),
                    "--seed",
                    str(seed),
                    "--output",
                    str(output),
                ]
                environment = dict(os.environ)
                environment.update(
                    {
                        "OPENBLAS_NUM_THREADS": "1",
                        "OMP_NUM_THREADS": "1",
                        "MKL_NUM_THREADS": "1",
                        "NUMEXPR_NUM_THREADS": "1",
                    }
                )
                evaluation = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=work,
                    env=environment,
                    timeout=profile["final_eval_seconds"],
                )
                if evaluation.returncode != 0:
                    raise BenchmarkError("sealed reference evaluator failed; no automatic retry")
                score = read_json(output)["auroc"]
                if type(score) is not float or not 0 <= score <= 1:
                    raise BenchmarkError("invalid sealed AUROC")
            except subprocess.TimeoutExpired as exc:
                raise BenchmarkError(
                    "sealed reference evaluator exceeded its hard time cap"
                ) from exc
            finally:
                temp_train.unlink(missing_ok=True)
            outcome = {
                "case_id": f"{dataset}:{seed}",
                "seed": seed,
                "dataset": f"researchbench-t1-{meta['stage']}",
                "evaluator": "t1-offline-reference-v1",
                "metric": "holdout_auroc",
                "value": score,
            }
            evaluated[key] = outcome
            atomic_json(work / "final" / f"{key}.json", outcome)
        result = {
            "stage": meta["stage"],
            "holdout_auroc": {k: v["value"] for k, v in evaluated.items()},
            "qualification": "Offline reference evaluator; not verified Coding Agent performance",
        }
        if meta["stage"] != "smoke":
            runs = []
            for arm, dataset, seed in wanted:
                key = f"{arm}__{dataset}__{seed}"
                eval_file = work / "final" / f"{key}.json"
                attempts = [
                    e
                    for e in started.values()
                    if (e["arm"], e["dataset"], e["seed"]) == (arm, dataset, seed)
                ]
                finish_map = {e["id"]: e for e in hist if e["kind"] in ("finished", "aborted")}
                cost = sum(finish_map[e["id"]]["seconds"] for e in attempts)
                runs.append(
                    {
                        "policy": arm,
                        "case_id": f"{dataset}:{seed}",
                        "seed": seed,
                        "artifact_ref": f"final/{key}.json",
                        "artifact_sha256": file_digest(eval_file),
                        "actual_cost": cost,
                        **audit_data[key],
                    }
                )
            suite = {
                "schema_version": 1,
                "name": f"ResearchBench-T1-{meta['stage']}",
                "dataset": f"researchbench-t1-{meta['stage']}",
                "evaluator": "t1-offline-reference-v1",
                "metric": "holdout_auroc",
                "direction": "maximize",
                "budget_unit": "measured_worker_wall_seconds",
                "per_case_budget": profile["per_cell_trials"] * profile["trial_seconds"],
                "model_decision_call_cap_per_cell": profile.get(
                    "max_model_decision_calls_per_cell", 0
                ),
                "model_decision_call_cap_total": profile.get("max_model_decision_calls_per_cell", 0)
                * len(profile["datasets"])
                * len(profile["arms"])
                * len(profile["seeds"]),
                "baseline": "agent_only",
                "policies": profile["arms"],
                "cases": [
                    {"case_id": f"{d}:{s}", "seed": s}
                    for d in profile["datasets"]
                    for s in profile["seeds"]
                ],
                "runs": runs,
            }
            atomic_json(work / "manifest.json", suite)
            result["comparison_manifest"] = str(work / "manifest.json")
        atomic_json(
            work / "final.completed",
            {"evaluation_count": len(wanted), "qualification": result["qualification"]},
        )
        return result


def smoke(work: Path, vault: Path) -> dict[str, Any]:
    setup(work, vault, "smoke", {}, False)
    candidate = Path(__file__).parent / "candidates" / "logistic.json"
    first = perform_trial(work, "offline_baseline", "wdbc", 42, candidate)
    if first["exit_code"] != 0:
        raise BenchmarkError(f"smoke baseline fit failed: {first['error']}")
    alternate = perform_trial(
        work, "offline_baseline", "wdbc", 42, Path(__file__).parent / "candidates" / "forest.json"
    )
    if alternate["exit_code"] != 0:
        raise BenchmarkError(f"smoke forest fit failed: {alternate['error']}")
    selected = select(work, "offline_baseline", "wdbc", 42)
    final = finalize(work, vault)
    return {
        "trials": [first, alternate],
        "submission": selected,
        "final": final,
        "status": status(work),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--work", type=Path, required=True)
    init.add_argument("--vault", type=Path, required=True)
    init.add_argument("--stage", choices=sorted(STAGES), default="smoke")
    init.add_argument("--allow-agent-stage", action="store_true")
    for name in ("adult", "bank", "magic"):
        init.add_argument(f"--source-{name}", type=Path)
    trial = sub.add_parser("trial")
    trial.add_argument("--work", type=Path, required=True)
    trial.add_argument("--arm", required=True)
    trial.add_argument("--dataset", required=True)
    trial.add_argument("--seed", type=int, required=True)
    trial.add_argument("--candidate", type=Path, required=True)
    submit = sub.add_parser("submit")
    submit.add_argument("--work", type=Path, required=True)
    submit.add_argument("--arm", required=True)
    submit.add_argument("--dataset", required=True)
    submit.add_argument("--seed", type=int, required=True)
    task = sub.add_parser("task")
    task.add_argument("--work", type=Path, required=True)
    task.add_argument("--arm", required=True)
    task.add_argument("--dataset", required=True)
    task.add_argument("--seed", type=int, required=True)
    memory = sub.add_parser("memory")
    memory.add_argument("--work", type=Path, required=True)
    memory.add_argument("--arm", required=True)
    memory.add_argument("--dataset", required=True)
    memory.add_argument("--seed", type=int, required=True)
    memory.add_argument("--id", required=True)
    memory.add_argument("--note", required=True)
    finish = sub.add_parser("finalize")
    finish.add_argument("--work", type=Path, required=True)
    finish.add_argument("--vault", type=Path, required=True)
    finish.add_argument("--audit", type=Path)
    abort = sub.add_parser("abort-interrupted")
    abort.add_argument("--work", type=Path, required=True)
    abort.add_argument("--id", required=True)
    abort.add_argument("--reason", required=True)
    stats = sub.add_parser("status")
    stats.add_argument("--work", type=Path, required=True)
    sp = sub.add_parser("smoke")
    sp.add_argument("--work", type=Path, required=True)
    sp.add_argument("--vault", type=Path, required=True)
    hidden = sub.add_parser("_worker")
    hidden.add_argument("--train", required=True)
    hidden.add_argument("--eval", required=True)
    hidden.add_argument("--candidate", required=True)
    hidden.add_argument("--seed", type=int, required=True)
    hidden.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "_worker":
            return _run_worker(args)
        if args.command == "task":
            print(task_prompt(args.work, args.arm, args.dataset, args.seed))
            return 0
        if args.command == "init":
            val = setup(
                args.work,
                args.vault,
                args.stage,
                {
                    n: getattr(args, f"source_{n}")
                    for n in ("adult", "bank", "magic")
                    if getattr(args, f"source_{n}") is not None
                },
                args.allow_agent_stage,
            )
        elif args.command == "trial":
            val = perform_trial(args.work, args.arm, args.dataset, args.seed, args.candidate)
        elif args.command == "submit":
            val = select(args.work, args.arm, args.dataset, args.seed)
        elif args.command == "memory":
            val = add_memory(args.work, args.arm, args.dataset, args.seed, args.id, args.note)
        elif args.command == "finalize":
            val = finalize(args.work, args.vault, args.audit)
        elif args.command == "abort-interrupted":
            val = recover_failed_trial(args.work, args.id, args.reason)
        elif args.command == "status":
            val = status(args.work)
        else:
            val = smoke(args.work, args.vault)
        print(json.dumps(val, ensure_ascii=False, indent=2, sort_keys=True))
    except (BenchmarkError, ValueError, OSError, KeyError, RuntimeError, TypeError) as exc:
        print(f"ResearchBench-T1: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
