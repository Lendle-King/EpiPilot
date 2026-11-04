"""Run the pinned AIRA official entry point without a model or network."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from epipilot.benchfirst.isolation import Limits, OfflineDocker


def qualify(image: str, source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    harness = Path(__file__).resolve().parents[3] / "src"
    sandbox = OfflineDocker(image, Limits(memory_mb=4096, seconds=180))
    common = (
        "env",
        "PWD=/work",
        "OMP_NUM_THREADS=2",
        "OPENBLAS_NUM_THREADS=2",
        "TF_NUM_INTRAOP_THREADS=2",
        "TF_NUM_INTEROP_THREADS=2",
        "GIT_DIR=/framework/.git",
        "GIT_CONFIG_COUNT=1",
        "GIT_CONFIG_KEY_0=safe.directory",
        "GIT_CONFIG_VALUE_0=/framework/.git",
        "python",
        "-m",
        "dojo.main_run",
        "+_exp=run_example",
        "interpreter=python",
        "solver.step_limit=0",
        "logger.use_wandb=False",
        "logger.write_env_vars=False",
    )
    commands = {
        "pip-check": ("pip", "check"),
        "official-help": (*common, "--help"),
        "official-config": (*common, "--cfg", "job"),
        "official-entry-zero": (*common, "task.name=random-acts-of-pizza"),
    }
    records: list[dict[str, object]] = []
    for name, command in commands.items():
        cell = output / name
        work = cell / "work"
        work.mkdir(parents=True)
        work.chmod(0o777)
        process = sandbox.run(harness, source, work, command, cell / "native.log")
        records.append(
            {"name": name, "process": process.model_dump(mode="json"), "provider_calls": 0}
        )
        (output / "records.json").write_text(json.dumps(records, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    qualify(args.image, args.source, args.output)
