"""Zero-provider CPU calibration and native CLI qualification; no hidden score is read."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import secrets
import shutil
from pathlib import Path

from epipilot.benchfirst.isolation import Limits, OfflineDocker
from epipilot.benchfirst.offline import digest
from epipilot.benchfirst.private_scoring import ClosedRun, LabelSet, SealedScorer
from epipilot.benchfirst.real_agents import RealTask, aide_command


def qualify(image: str, task_root: Path, source: Path, output: Path) -> None:
    task = RealTask.model_validate_json((task_root / "task.json").read_text())
    task.check_files(task_root / "public")
    output.mkdir(parents=True, exist_ok=False)
    harness = Path(__file__).resolve().parents[3] / "src"
    sandbox = OfflineDocker(image, Limits(memory_mb=4096, seconds=180))
    records: list[dict[str, object]] = []
    commands = {
        "codex-version": ("codex", "--version"),
        "codex-help": ("codex", "exec", "--help"),
        "aide-zero-step": (
            "env",
            "EPIPILOT_REAL_CONTAINER=1",
            "OPENAI_BASE_URL=http://gateway:8080/v1",
            "OPENAI_API_KEY=not-a-provider-key",
            *aide_command(task, "offline-unavailable"),
            "agent.steps=0",
        ),
        "boundary": (
            "python",
            "-c",
            "import os,socket; from pathlib import Path; "
            "assert not any(os.getenv(k) for k in ('OPENAI_API_KEY','ANTHROPIC_API_KEY')); "
            "assert not any(Path(p).exists() "
            "for p in ('/private','/control','/var/run/docker.sock')); "
            "s=socket.socket(); s.settimeout(1); "
            "assert s.connect_ex(('1.1.1.1',443)) != 0; print('boundary passed')",
        ),
        "ridge": ("python", "candidate.py"),
        "hist-gradient-boosting": ("env", "CALIBRATION_REFERENCE=1", "python", "candidate.py"),
    }
    for name, command in commands.items():
        cell = output / name
        work = cell / "work"
        shutil.copytree(task_root / "public", work)
        shutil.copytree(work, work / "input", ignore=shutil.ignore_patterns("input"))
        work.chmod(0o777)
        for p in work.rglob("*"):
            p.chmod(0o777 if p.is_dir() else 0o666)
        process = sandbox.run(harness, source, work, command, cell / "native.log")
        record: dict[str, object] = {
            "name": name,
            "process": process.model_dump(mode="json"),
            "provider_calls": 0,
            "hidden_score": None,
        }
        if name in {"ridge", "hist-gradient-boosting"} and process.exit_code == 0:
            task.check_files(work)
            with (work / "dev.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            labels = LabelSet(
                row_ids=tuple(r["row_id"] for r in rows),
                values=tuple(float(r["target"]) for r in rows),
            )
            closed = ClosedRun(
                cell_id=name,
                track="native",
                task_sha256=task.fingerprint,
                candidate_sha256=digest(work / "candidate.py"),
                predictions_sha256=digest(work / "dev_predictions.csv"),
                closure_sha256=hashlib.sha256(process.model_dump_json().encode()).hexdigest(),
            )
            scorer_path = harness / "epipilot/benchfirst/private_scoring.py"
            score = SealedScorer(labels, digest(scorer_path), secrets.token_bytes(32)).score(
                closed, work / "dev_predictions.csv"
            )
            record["independent_dev_rmse"] = score.rmse
        records.append(record)
        (output / "records.json").write_text(json.dumps(records, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    qualify(args.image, args.task_root, args.source, args.output)
