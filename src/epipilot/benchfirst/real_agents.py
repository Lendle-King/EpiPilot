"""Real Codex/AIDE command adapters. Offline synthetic drivers are never used here."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path, PurePosixPath
from typing import Self

from pydantic import Field, model_validator

from epipilot.benchfirst.contracts import SHA256
from epipilot.benchfirst.gateway import object_json
from epipilot.benchfirst.offline import Value, digest


class RealTask(Value):
    task_id: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    objective: str = Field(min_length=1)
    dataset_citation: str = Field(min_length=1)
    data_license: str = Field(min_length=1)
    public_files: dict[str, str]
    training_seconds: int = Field(gt=0, le=600)
    steps: int = Field(gt=0, le=3)

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        import re

        if not {"train.csv", "dev.csv", "test.csv", "candidate.py"} <= self.public_files.keys():
            raise ValueError("required real task files missing")
        for name, sha in self.public_files.items():
            path = PurePosixPath(name)
            if (
                name not in {"train.csv", "dev.csv", "test.csv", "candidate.py", "task.md"}
                or path.is_absolute()
                or ".." in path.parts
                or str(path) != name
                or re.fullmatch(SHA256, sha) is None
            ):
                raise ValueError("unsafe task path/digest")
        return self

    def check_files(self, root: Path, *, after_run: bool = False) -> None:
        for name, sha in self.public_files.items():
            if after_run and name == "candidate.py":
                continue
            path = root / name
            if (
                path.is_symlink()
                or not path.is_file()
                or not path.resolve().is_relative_to(root.resolve())
                or digest(path) != sha
            ):
                raise ValueError("public task file changed: " + name)

    @property
    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def codex_command(task: RealTask, model: str) -> tuple[str, ...]:
    prompt = (
        f"Task {task.task_id}: {task.objective}. Read task.md. Modify candidate.py and run "
        "development experiments on train.csv and dev.csv. test.csv has features only. "
        "Write dev_predictions.csv and predictions.csv with header row_id,prediction in input "
        "row order. Do not modify the data or task files. Do not access external data or services. "
        f"At most {task.steps} development trials, {task.training_seconds} total training seconds. "
        "Export the best executable candidate.py; report commands and failures."
    )
    return (
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "workspace-write",
        "--model",
        model,
        "-C",
        "/work",
        "-c",
        'approval_policy="never"',
        "-c",
        'model_provider="epipilot"',
        "-c",
        'model_providers.epipilot.name="Budget gateway"',
        "-c",
        'model_providers.epipilot.base_url="http://gateway:8080/v1"',
        "-c",
        'model_providers.epipilot.env_key="EPIPILOT_GATEWAY_TOKEN"',
        "-c",
        'model_providers.epipilot.wire_api="responses"',
        "-c",
        "model_providers.epipilot.request_max_retries=0",
        "-c",
        "model_providers.epipilot.stream_max_retries=0",
        "-c",
        "model_providers.epipilot.supports_websockets=false",
        "-c",
        'web_search="disabled"',
        prompt,
    )


def aide_command(task: RealTask, model: str) -> tuple[str, ...]:
    return (
        "python",
        "-m",
        "epipilot.benchfirst.real_agents",
        "aide",
        "data_dir=/work/input",
        "desc_file=/work/task.md",
        "agent.metric_maximize=false",
        f"agent.steps={task.steps}",
        f"exec.timeout={task.training_seconds // task.steps}",
        f"agent.code.model={model}",
        f"agent.feedback.model={model}",
        "agent.code.provider=openai",
        "agent.feedback.provider=openai",
        "agent.code.max_tokens=2048",
        "agent.feedback.max_tokens=2048",
        "agent.code.temp=null",
        "agent.feedback.temp=null",
        "generate_report=false",
        "preprocess_data=false",
        "copy_data=true",
        "exp_name=real-task",
        "log_dir=/work/aide-logs",
        "workspace_dir=/work/aide-work",
    )


class CodexDiagnostics(Value):
    file_change_events: int
    command_events: int
    turn_failures: int
    input_tokens: int | None
    output_tokens: int | None
    # A CLI turn is not a provider request. The separate trusted meter supplies this.
    api_request_count: None = None


def summarize_codex(log: Path) -> CodexDiagnostics:
    edits = commands = failures = 0
    inputs: int | None = None
    outputs: int | None = None
    if log.stat().st_size > 4_194_304:
        raise ValueError("Codex event log too large")
    for line in log.read_bytes().splitlines():
        event = object_json(line)
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict):
                edits += item.get("type") == "file_change"
                commands += item.get("type") == "command_execution"
        failures += event.get("type") in {"turn.failed", "error"}
        usage = event.get("usage")
        if event.get("type") == "turn.completed" and isinstance(usage, dict):
            for key in ("input_tokens", "output_tokens"):
                if type(usage.get(key)) is not int or usage[key] < 0:
                    raise ValueError("invalid native usage")
            inputs = (inputs or 0) + usage["input_tokens"]
            outputs = (outputs or 0) + usage["output_tokens"]
    return CodexDiagnostics(
        file_change_events=edits,
        command_events=commands,
        turn_failures=failures,
        input_tokens=inputs,
        output_tokens=outputs,
    )


def main() -> None:
    if sys.argv[1:2] != ["aide"] or os.environ.get("EPIPILOT_REAL_CONTAINER") != "1":
        raise ValueError("AIDE bridge requires an isolated real-task container")
    # Transport-only override. Original Agent.step/search/debug/journal/Interpreter and
    # native backend query/response parsing remain unchanged. No mock model is installed.
    from aide.backend import backend_openai  # type: ignore[import-not-found]

    if os.environ.get("OPENAI_BASE_URL") != "http://gateway:8080/v1":
        raise ValueError("AIDE must use the metered gateway")
    backend_openai.OPENAI_BASE_URL = "http://gateway:8080/v1"
    sys.argv = ["aide.run", *sys.argv[2:]]
    runpy.run_module("aide.run", run_name="__main__")


if __name__ == "__main__":
    main()
