"""EpiPilot ResearchBench-T1: offline, cost-capped tabular research qualification.

The benchmark *does not call any coding agent or paid API*. It provides a
frozen experiment surface for independently launched research agents and a
held-out evaluator. All expensive or external-agent phases are opt-in.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

STAGES: dict[str, dict[str, Any]] = {
    "smoke": {"datasets": ["wdbc"], "arms": ["offline_baseline"],
              "seeds": [42], "per_cell_trials": 2, "trial_seconds": 12,
              "total_seconds": 30, "final_eval_seconds": 15},
    "pilot": {"datasets": ["wdbc"], "arms": ["agent_only", "memory_only",
              "epipilot_heuristic", "epipilot_conditional"], "seeds": [42, 137],
              "per_cell_trials": 2, "trial_seconds": 15, "total_seconds": 120, "final_eval_seconds": 20},
    "full": {"datasets": ["adult", "bank", "magic"],
             "arms": ["agent_only", "memory_only", "epipilot_heuristic",
                      "epipilot_conditional"], "seeds": [42, 137, 271],
             "per_cell_trials": 2, "trial_seconds": 20, "total_seconds": 900, "final_eval_seconds": 35},
}
SPLIT_SEED = 20271025
SCHEMA = 1


class BenchmarkError(ValueError):
    """A research run violated its frozen benchmark contract."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    return digest(path.read_bytes())


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
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".partial-", delete=False) as stream:
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
        raise BenchmarkError("T1 is already running or requires explicit crash reconciliation") from exc
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
        return data, labels, "sklearn.datasets.load_breast_cancer"
    if source is None or not source.is_file():
        raise BenchmarkError(f"{dataset}: supply an existing local raw UCI file; no download is attempted")
    if source.stat().st_size > 40 * 1024 * 1024:
        raise BenchmarkError("refusing oversized external dataset")
    if dataset == "adult":
        cols = ["age", "workclass", "fnlwgt", "education", "education_num", "marital_status",
                "occupation", "relationship", "race", "sex", "capital_gain", "capital_loss",
                "hours_per_week", "native_country", "target"]
        frame = pd.read_csv(source, header=None, names=cols, skipinitialspace=True)
        frame = frame.replace("?", np.nan)
        target = frame.pop("target").astype(str).str.strip().str.rstrip(".")
        if set(target.unique()) != {"<=50K", ">50K"}:
            raise BenchmarkError("Adult class vocabulary not recognized; use adult.data")
        labels = target.eq(">50K").astype(int)
    elif dataset == "bank":
        frame = pd.read_csv(source, sep=";")
        if "y" not in frame or "duration" not in frame:
            raise BenchmarkError("Bank Marketing requires bank-full.csv with y and duration columns")
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


def _write_dataset(work: Path, vault: Path, dataset: str, source: Path | None) -> dict[str, Any]:
    np, pd, sklearn, splitter = _dependencies()
    features, target, source_hash = load_data(dataset, source)
    y = pd.Series(np.asarray(target, dtype=int), name="target")
    if len(y.unique()) != 2 or y.value_counts().min() < 20:
        raise BenchmarkError("binary dataset needs two classes with at least 20 members each")
    # Split once per dataset, independently of research agent seed.
    left, test = splitter(np.arange(len(y)), test_size=0.2, stratify=y, random_state=SPLIT_SEED)
    train, dev = splitter(left, test_size=0.25, stratify=y.iloc[left], random_state=SPLIT_SEED + 1)
    data = features.reset_index(drop=True).copy()
    data.columns = [str(x) for x in data.columns]
    if "target" in data.columns:
        raise BenchmarkError("features contain target column")
    data["target"] = y
    public_dir = work / "public" / dataset
    vault_dir = vault / dataset
    public_dir.mkdir(parents=True, exist_ok=False)
    vault_dir.mkdir(parents=True, exist_ok=False)
    meta = {"dataset": dataset, "source_hash": source_hash, "split_seed": SPLIT_SEED,
            "rows": len(y), "features": len(data.columns) - 1,
            "versions": {"sklearn": sklearn.__version__, "pandas": pd.__version__,
                         "numpy": np.__version__},
            "files": {}}
    for name, ids, folder in [("train", train, public_dir), ("dev", dev, public_dir),
                              ("test", test, vault_dir)]:
        path = folder / f"{name}.csv"
        data.iloc[ids].to_csv(path, index=False, float_format="%.15g")
        meta["files"][name] = {"sha256": file_digest(path), "rows": len(ids)}
    return meta


def setup(work: Path, vault: Path, stage: str, sources: dict[str, Path], opt_in: bool) -> dict[str, Any]:
    if stage not in STAGES:
        raise BenchmarkError("invalid T1 stage")
    if stage != "smoke" and not opt_in:
        raise BenchmarkError("pilot/full must be explicitly enabled; smoke is the default")
    work, vault = work.resolve(), vault.resolve()
    if work == vault or work in vault.parents or vault in work.parents:
        raise BenchmarkError("public workspace and private vault must be disjoint")
    if work.exists() and any(work.iterdir()):
        raise BenchmarkError("workspace exists and is nonempty")
    if vault.exists() and any(vault.iterdir()):
        raise BenchmarkError("private vault exists and is nonempty")
    work.mkdir(parents=True, exist_ok=True)
    vault.mkdir(parents=True, exist_ok=True)
    spec = STAGES[stage]
    dataset_meta: dict[str, Any] = {}
    for dataset in spec["datasets"]:
        dataset_meta[dataset] = _write_dataset(work, vault, dataset, sources.get(dataset))
    meta = {"schema": SCHEMA, "stage": stage, "profile": spec,
            "datasets": dataset_meta, "total_external_api_calls": 0,
            "final_holdout_is_off_limits_during_search": True}
    atomic_json(work / "meta.json", meta)
    atomic_json(vault / "meta.json", {"public_meta_sha256": file_digest(work / "meta.json"),
                                      "dataset_holdouts": {k: v["files"]["test"]["sha256"]
                                                           for k, v in dataset_meta.items()}})
    return {"stage": stage, "work": str(work), "vault": str(vault),
            "profile": spec, "dataset_rows": {k: v["rows"] for k, v in dataset_meta.items()},
            "api_calls": 0}


def normalize_candidate(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or "model" not in raw or "feature_mode" not in raw:
        raise BenchmarkError("candidate needs model and feature_mode")
    allowed = {
        "logistic": {"model", "feature_mode", "C", "class_weight"},
        "forest": {"model", "feature_mode", "n_estimators", "max_depth", "min_samples_leaf", "class_weight"},
    }
    name = raw["model"]
    if name not in allowed or set(raw) != allowed[name]:
        raise BenchmarkError("unapproved estimator or inconsistent parameter schema")
    if raw["feature_mode"] not in ("all", "numeric_only") or raw["class_weight"] not in ("none", "balanced"):
        raise BenchmarkError("invalid feature_mode or class_weight")
    if name == "logistic":
        c = raw["C"]
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0.01 <= c <= 10:
            raise BenchmarkError("logistic C outside approved range")
    else:
        n, depth, leaf = raw["n_estimators"], raw["max_depth"], raw["min_samples_leaf"]
        if any(type(x) is not int for x in (n, depth, leaf)) or not (20 <= n <= 120 and 2 <= depth <= 16 and 1 <= leaf <= 10):
            raise BenchmarkError("forest parameters outside approved range")
    return dict(sorted(raw.items()))


def _fit(train: Path, evaluation: Path, raw: dict[str, Any], seed: int, fit_all: bool) -> float:
    np, pd, sklearn, _ = _dependencies()
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
        transformers.append(("num", make_pipeline(SimpleImputer(strategy="median"),
                                                  StandardScaler()), numerics))
    if categories:
        transformers.append(("cat", make_pipeline(SimpleImputer(strategy="most_frequent"),
                                                  OneHotEncoder(handle_unknown="ignore")), categories))
    if not transformers:
        raise BenchmarkError("empty design matrix")
    preprocessor = ColumnTransformer(transformers)
    weight = None if candidate["class_weight"] == "none" else "balanced"
    if candidate["model"] == "logistic":
        model = LogisticRegression(C=candidate["C"], max_iter=250, class_weight=weight,
                                   random_state=seed, solver="liblinear")
    else:
        model = RandomForestClassifier(n_estimators=candidate["n_estimators"],
                                      max_depth=candidate["max_depth"],
                                      min_samples_leaf=candidate["min_samples_leaf"],
                                      class_weight=weight, n_jobs=1, random_state=seed)
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
    if arm not in profile["arms"] or dataset not in profile["datasets"] or seed not in profile["seeds"]:
        raise BenchmarkError("arm/dataset/seed outside preregistered task matrix")


def _spent(events: list[dict[str, Any]], timeout: int) -> float:
    ended = {e["id"] for e in events if e["kind"] in ("finished", "aborted")}
    return sum(float(e["seconds"]) for e in events if e["kind"] in ("finished", "aborted")) + sum(
        timeout for e in events if e["kind"] == "started" and e["id"] not in ended)


def perform_trial(work: Path, arm: str, dataset: str, seed: int, candidate_path: Path) -> dict[str, Any]:
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
        matches = [e for e in started if (e["arm"], e["dataset"], e["seed"]) == (arm, dataset, seed)]
        if len(matches) >= profile["per_cell_trials"]:
            raise BenchmarkError("per-cell experiment count budget exhausted")
        if any(e["candidate_sha256"] == c_hash for e in matches):
            raise BenchmarkError("identical candidate already attempted for this research cell")
        # Reserve one maximum-duration trial; crash counts conservatively.
        if _spent(history, profile["trial_seconds"]) + profile["trial_seconds"] > profile["total_seconds"] + 1e-9:
            raise BenchmarkError("global CPU wall-budget exhausted, cannot reserve trial")
        run_id = f"trial-{len(started) + 1:04d}"
        run_dir = work / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        atomic_json(run_dir / "candidate.json", proposed)
        event(work, {"kind": "started", "id": run_id, "arm": arm, "dataset": dataset,
                     "seed": seed, "candidate_sha256": c_hash, "time_limit": profile["trial_seconds"]})
        train = work / "public" / dataset / "train.csv"
        dev = work / "public" / dataset / "dev.csv"
        dataset_meta = meta["datasets"][dataset]["files"]
        if file_digest(train) != dataset_meta["train"]["sha256"] or file_digest(dev) != dataset_meta["dev"]["sha256"]:
            raise BenchmarkError("public split was modified; run remains reserved until reviewed")
        argv = [sys.executable, str(Path(__file__).resolve()), "_worker", "--train", str(train),
                "--eval", str(dev), "--candidate", str(run_dir / "candidate.json"),
                "--seed", str(seed), "--output", str(run_dir / "metric.json")]
        environment = dict(os.environ)
        environment.update({"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                            "NUMEXPR_NUM_THREADS": "1"})
        begin = time.monotonic()
        try:
            result = subprocess.run(argv, timeout=profile["trial_seconds"], capture_output=True,
                                    text=True, cwd=work, env=environment, check=False)
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
        receipt = {"kind": "finished", "id": run_id, "seconds": round(seconds, 6),
                   "exit_code": exit_code, "dev_auroc": score, "metric_sha256": (
                       file_digest(metric_path) if metric_path.exists() else None),
                   "error": stderr}
        event(work, receipt)
        return {**receipt, "dataset": dataset, "seed": seed, "arm": arm,
                "remaining_wall_seconds": max(0, profile["total_seconds"] - _spent(ledger(work), profile["trial_seconds"]))}


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
        event(work, {"kind": "aborted", "id": run_id, "seconds": meta["profile"]["trial_seconds"],
                     "reason": reason, "cost_is_upper_bound": True})
        return {"aborted": run_id, "charged_seconds": meta["profile"]["trial_seconds"]}


def add_memory(work: Path, arm: str, dataset: str, seed: int, run_id: str, note: str) -> dict[str, Any]:
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
        if run_id not in ends or ends[run_id]["exit_code"] != 0 or (
            begins[run_id]["arm"], begins[run_id]["dataset"], begins[run_id]["seed"]
        ) != (arm, dataset, seed):
            raise BenchmarkError("memory cannot be based on an unrelated or failed trial")
        notes = [e for e in history if e["kind"] == "memory_note" and e["id"] == run_id]
        if notes:
            raise BenchmarkError("only one memory note is permitted per validated run")
        event(work, {"kind": "memory_note", "id": run_id, "arm": arm, "dataset": dataset,
                     "seed": seed, "note": note.strip(), "dev_auroc": ends[run_id]["dev_auroc"]})
    return {"memory_added": run_id, "status": "unverified_agent_note_with_verified_dev_metric"}


def task_prompt(work: Path, arm: str, dataset: str, seed: int) -> str:
    """Human-launchable task instructions; never contacts or launches a paid model."""
    work = work.resolve()
    meta = _load_meta(work)
    _unit(meta, arm, dataset, seed)
    profile = meta["profile"]
    path = Path(__file__).resolve()
    src = work / "public" / dataset
    instructions = {
        "agent_only": "No EpiPilot CLI or past-research memory. Choose up to two distinct candidate experiments using the shared dev feedback.",
        "memory_only": "No EpiPilot planner. Read previously recorded memory_only notes; add a sourced note after each real trial with the memory command.",
        "epipilot_heuristic": "Use a separate project-local EpiPilot event store. Register uncertainty, claim, proposals and evidence, then follow `epi iterate` (default heuristic).",
        "epipilot_conditional": "Use a separate project-local EpiPilot event store and preregister a four-outcome decision table before selecting with `epi iterate --policy conditional`. Never silently fall back to heuristic.",
        "offline_baseline": "Offline baseline smoke only, no agent permitted or required.",
    }
    history = [e for e in ledger(work) if e["kind"] == "memory_note" and e["arm"] == arm]
    notes = "\n".join(
        f"- {e['dataset']}/{e['seed']}/{e['id']}: {e['note']} (dev_AUROC={e['dev_auroc']})"
        for e in history[-6:]
    ) if arm == "memory_only" else "(not exposed to this policy)"
    return f"""ResearchBench-T1 {meta['stage']} / {dataset} / seed {seed} / policy {arm}
Goal: maximize held-out AUROC under a hard development experiment budget.
Only train/dev are visible: {src}/train.csv and {src}/dev.csv.
Never read the raw dataset, private holdout, vault path, or final metrics during research.
Do not use web access, external model APIs, GPU jobs, or paid provider calls without separate explicit authorization.
The harness makes ZERO external model calls. Coding Agent billing outside it is NOT metered.
At most {profile['per_cell_trials']} trials per cell, each <= {profile['trial_seconds']} wall-seconds.
Total stage experimental wall budget = {profile['total_seconds']} seconds; violations fail closed.
Candidate interventions are restricted to logistic/forest parameter JSONs (see candidates/).
Instructions for this arm: {instructions[arm]}
Memory notes available: {notes}
To run an experiment:
  python {path} trial --work {work} --arm {arm} --dataset {dataset} --seed {seed} --candidate YOUR_CANDIDATE.json
Then choose and freeze the strongest development candidate:
  python {path} submit --work {work} --arm {arm} --dataset {dataset} --seed {seed}
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
        outcomes = [e for e in history if e["kind"] == "finished" and e["exit_code"] == 0
                    and e["dev_auroc"] is not None and
                    (started[e["id"]]["arm"], started[e["id"]]["dataset"], started[e["id"]]["seed"]) ==
                    (arm, dataset, seed)]
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
        result = {"arm": arm, "dataset": dataset, "seed": seed,
                  "run_id": chosen["id"], "candidate_sha256": file_digest(candidate),
                  "dev_auroc": chosen["dev_auroc"]}
        path = work / "submissions" / f"{arm}__{dataset}__{seed}.json"
        atomic_json(path, result)
        return result


def status(work: Path) -> dict[str, Any]:
    work = work.resolve()
    meta = _load_meta(work)
    history = ledger(work)
    started = [e for e in history if e["kind"] == "started"]
    finished = [e for e in history if e["kind"] == "finished"]
    return {"stage": meta["stage"], "total_started": len(started),
            "completed": len(finished), "reserved_or_spent_seconds": _spent(history, meta["profile"]["trial_seconds"]),
            "max_total_seconds": meta["profile"]["total_seconds"],
            "submitted": len(list((work / "submissions").glob("*.json"))) if (work / "submissions").exists() else 0,
            "required_submissions": len(meta["profile"]["datasets"]) * len(meta["profile"]["arms"]) * len(meta["profile"]["seeds"]),
            "external_model_calls": "not metered; none invoked by this harness",
            "holdout_evaluated": (work / "final.started").exists()}


def finalize(work: Path, vault: Path, audit: Path | None = None) -> dict[str, Any]:
    work, vault = work.resolve(), vault.resolve()
    meta = _load_meta(work)
    priv = read_json(vault / "meta.json")
    if priv["public_meta_sha256"] != file_digest(work / "meta.json"):
        raise BenchmarkError("evaluation vault is not paired to this frozen public task")
    profile = meta["profile"]
    wanted = [(a, d, s) for d in profile["datasets"] for s in profile["seeds"] for a in profile["arms"]]
    audit_data: dict[str, Any] = {}
    if meta["stage"] != "smoke":
        if audit is None:
            raise BenchmarkError("comparison requires explicit non-synthetic intervention/audit records")
        audit_data = read_json(audit)
        if set(audit_data) != {f"{a}__{d}__{s}" for a, d, s in wanted}:
            raise BenchmarkError("audit must cover exactly all benchmark cells")
        for row in audit_data.values():
            if not isinstance(row, dict) or set(row) != {"human_interventions", "repeated_experiments", "unsupported_claims"} or any(type(x) is not int or x < 0 for x in row.values()):
                raise BenchmarkError("audit counts must be explicit nonnegative integers")
    with exclusive(work):
        if (work / "final.started").exists():
            raise BenchmarkError("sealed holdout has already been evaluated; prevent test-set reuse")
        submissions = {}
        hist = ledger(work)
        started = {e["id"]: e for e in hist if e["kind"] == "started"}
        finished = {e["id"]: e for e in hist if e["kind"] == "finished"}
        for arm, dataset, seed in wanted:
            key = f"{arm}__{dataset}__{seed}"
            selection = read_json(work / "submissions" / f"{key}.json")
            rid = selection["run_id"]
            if (selection["arm"], selection["dataset"], selection["seed"]) != (arm, dataset, seed):
                raise BenchmarkError("selection does not belong to frozen arm and task")
            if rid not in started or rid not in finished or finished[rid]["exit_code"] != 0:
                raise BenchmarkError("selection refers to a non-validated trial")
            if (started[rid]["arm"], started[rid]["dataset"], started[rid]["seed"]) != (arm, dataset, seed):
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
        for dataset in profile["datasets"]:
            private_test = vault / dataset / "test.csv"
            if file_digest(private_test) != priv["dataset_holdouts"][dataset]:
                raise BenchmarkError("private test file does not match frozen holdout")
        atomic_json(work / "final.started", {"submission_hashes": {
            k: file_digest(v[1]) for k, v in submissions.items()}, "status": "sealed_for_one_time_evaluation"})
        evaluated = {}
        for arm, dataset, seed in wanted:
            key = f"{arm}__{dataset}__{seed}"
            selection, candidate = submissions[key]
            public = work / "public" / dataset
            temp_train = work / ".eval_tmp.csv"
            output = work / "final" / f"{key}.worker.json"
            try:
                _, pd, _, _ = _dependencies()
                train = pd.concat([pd.read_csv(public / "train.csv"), pd.read_csv(public / "dev.csv")], ignore_index=True)
                train.to_csv(temp_train, index=False)
                cmd = [sys.executable, str(Path(__file__).resolve()), "_worker", "--train", str(temp_train),
                       "--eval", str(vault / dataset / "test.csv"), "--candidate", str(candidate),
                       "--seed", str(seed), "--output", str(output)]
                environment = dict(os.environ)
                environment.update({"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                                    "NUMEXPR_NUM_THREADS": "1"})
                evaluation = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=work,
                                            env=environment, timeout=profile["final_eval_seconds"])
                if evaluation.returncode != 0:
                    raise BenchmarkError("sealed reference evaluator failed; no automatic retry")
                score = read_json(output)["auroc"]
                if type(score) is not float or not 0 <= score <= 1:
                    raise BenchmarkError("invalid sealed AUROC")
            except subprocess.TimeoutExpired as exc:
                raise BenchmarkError("sealed reference evaluator exceeded its hard time cap") from exc
            finally:
                temp_train.unlink(missing_ok=True)
            outcome = {"case_id": f"{dataset}:{seed}", "seed": seed, "dataset": f"researchbench-t1-{meta['stage']}",
                       "evaluator": "t1-offline-reference-v1", "metric": "holdout_auroc", "value": score}
            evaluated[key] = outcome
            atomic_json(work / "final" / f"{key}.json", outcome)
        result = {"stage": meta["stage"], "holdout_auroc": {k: v["value"] for k, v in evaluated.items()},
                  "qualification": "Offline reference evaluator only; not verified Coding Agent performance"}
        if meta["stage"] != "smoke":
            runs = []
            for arm, dataset, seed in wanted:
                key = f"{arm}__{dataset}__{seed}"
                eval_file = work / "final" / f"{key}.json"
                attempts = [e for e in started.values() if (e["arm"], e["dataset"], e["seed"]) == (arm, dataset, seed)]
                finish_map = {e["id"]: e for e in hist if e["kind"] in ("finished", "aborted")}
                cost = sum(finish_map[e["id"]]["seconds"] for e in attempts)
                runs.append({"policy": arm, "case_id": f"{dataset}:{seed}", "seed": seed,
                             "artifact_ref": f"final/{key}.json", "artifact_sha256": file_digest(eval_file),
                             "actual_cost": cost, **audit_data[key]})
            suite = {"schema_version": 1, "name": f"ResearchBench-T1-{meta['stage']}",
                     "dataset": f"researchbench-t1-{meta['stage']}", "evaluator": "t1-offline-reference-v1",
                     "metric": "holdout_auroc", "direction": "maximize", "budget_unit": "measured_worker_wall_seconds",
                     "per_case_budget": profile["per_cell_trials"] * profile["trial_seconds"] * 1.2,
                     "baseline": "agent_only", "policies": profile["arms"],
                     "cases": [{"case_id": f"{d}:{s}", "seed": s} for d in profile["datasets"] for s in profile["seeds"]],
                     "runs": runs}
            atomic_json(work / "manifest.json", suite)
            result["comparison_manifest"] = str(work / "manifest.json")
        atomic_json(work / "final.completed", {"evaluation_count": len(wanted), "qualification": result["qualification"]})
        return result


def smoke(work: Path, vault: Path) -> dict[str, Any]:
    setup(work, vault, "smoke", {}, False)
    candidate = Path(__file__).parent / "candidates" / "logistic.json"
    first = perform_trial(work, "offline_baseline", "wdbc", 42, candidate)
    if first["exit_code"] != 0:
        raise BenchmarkError(f"smoke baseline fit failed: {first['error']}")
    alternate = perform_trial(work, "offline_baseline", "wdbc", 42,
                              Path(__file__).parent / "candidates" / "forest.json")
    if alternate["exit_code"] != 0:
        raise BenchmarkError(f"smoke forest fit failed: {alternate['error']}")
    selected = select(work, "offline_baseline", "wdbc", 42)
    final = finalize(work, vault)
    return {"trials": [first, alternate], "submission": selected,
            "final": final, "status": status(work)}


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
            val = setup(args.work, args.vault, args.stage,
                        {n: getattr(args, f"source_{n}") for n in ("adult", "bank", "magic")
                         if getattr(args, f"source_{n}") is not None}, args.allow_agent_stage)
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
