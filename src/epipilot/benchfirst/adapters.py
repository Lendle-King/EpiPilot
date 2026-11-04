"""Real source-bound S0 adapters. No generic prompt or paid-run escape hatch."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from epipilot.benchfirst.contracts import SHA40
from epipilot.benchfirst.isolation import OfflineDocker, ProcessResult
from epipilot.benchfirst.offline import (
    FrozenCandidate,
    MockBudget,
    PublicTask,
    Value,
    digest,
    evaluate_dev,
    freeze_candidate,
    strict_predictions,
)

Family = Literal["agent_only", "aide_ml", "aira_dojo", "epipilot_current"]


class Source(Value):
    checkout: Path
    sha: str = Field(pattern=SHA40)
    url: str = Field(pattern=r"^https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()


def check_source(source: Source) -> None:
    path = source.checkout
    if path.is_symlink() or not path.is_dir():
        raise ValueError("missing or symlinked source")
    if Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve():
        raise ValueError("source is not checkout root")
    remote = git(path, "remote", "get-url", "origin").removesuffix(".git")
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote.split(":", 1)[1]
    if remote != source.url or git(path, "rev-parse", "HEAD") != source.sha:
        raise ValueError("source identity/revision mismatch")
    # Include ignored inputs: .env or stale bytecode must not enter candidate mounts.
    if git(path, "status", "--porcelain", "--untracked-files=all", "--ignored"):
        raise ValueError("dirty or ignored source inputs")


class DevResult(Value):
    mse: float = Field(ge=0, allow_inf_nan=False)
    candidate_sha256: str
    prediction_sha256: str
    evaluator_sha256: str


class AgentAdapter(Protocol):
    def prepare(
        self,
        public_task: PublicTask,
        model: Literal["mock-only"],
        budget: MockBudget,
        sandbox: OfflineDocker,
    ) -> None: ...
    def run_dev_experiment(self) -> DevResult: ...
    def export_frozen_candidate(self) -> FrozenCandidate: ...
    def finalize_receipt(self) -> Path: ...


class SourceAdapter:
    family: Family
    native_cli: tuple[str, ...]

    def __init__(
        self, source: Source, cell: Path, harness: Path, *, inject_crash: bool = False
    ) -> None:
        self.source, self.cell, self.harness = source, cell, harness
        self.inject_crash = inject_crash
        self.processes: list[ProcessResult] = []
        self.dev: DevResult | None = None
        self.frozen: FrozenCandidate | None = None

    def prepare(
        self,
        public_task: PublicTask,
        model: Literal["mock-only"],
        budget: MockBudget,
        sandbox: OfflineDocker,
    ) -> None:
        if model != "mock-only":
            raise ValueError("S0 transport is mock-only; paid execution is unavailable")
        if budget != MockBudget():
            raise ValueError("native S0 smoke requires the frozen 4/3/1800 mock budget")
        self.budget = budget
        check_source(self.source)
        self.cell.mkdir(parents=True, exist_ok=False)  # no silent refill or reuse
        self.task, self.sandbox = public_task, sandbox
        self.native = self.cell / "source"
        git(self.source.checkout, "worktree", "add", "--detach", str(self.native), self.source.sha)
        baseline = self.cell / "baseline"
        baseline.mkdir()
        git(baseline, "init", "-q")
        (baseline / "task.json").write_text(public_task.model_dump_json())
        (baseline / "candidate.py").write_text(
            "import json\nfrom pathlib import Path\n"
            "task = json.loads(Path('task.json').read_text())\n"
            "print(json.dumps([0.0 for x in task['x']]))\n"
        )
        git(baseline, "add", "--", "task.json", "candidate.py")
        tree = git(baseline, "write-tree")
        environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "S0 Synthetic",
            "GIT_COMMITTER_NAME": "S0 Synthetic",
            "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
            "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        }
        self.baseline_sha = subprocess.check_output(
            [
                "git",
                "-C",
                str(baseline),
                "-c",
                "commit.gpgsign=false",
                "commit-tree",
                tree,
                "-m",
                "S0 frozen synthetic baseline",
            ],
            env=environment,
            text=True,
            timeout=30,
        ).strip()
        self.work = self.cell / "work"
        git(baseline, "worktree", "add", "--detach", str(self.work), self.baseline_sha)
        self.work.chmod(0o777)  # ephemeral container UID; synthetic public data only
        (self.work / "candidate.py").chmod(0o666)
        (self.work / "input").mkdir()
        self.task_digest = digest(self.work / "task.json")
        (self.cell / "prepared.json").write_text(
            json.dumps(
                {
                    "family": self.family,
                    "source": self.source.model_dump(mode="json"),
                    "task_sha256": self.task_digest,
                    "baseline_sha": self.baseline_sha,
                    "image": sandbox.image,
                    "limits": sandbox.limits.model_dump(mode="json"),
                    "mock_budget": budget.model_dump(mode="json"),
                    "model": model,
                    "inject_crash": self.inject_crash,
                },
                indent=2,
            )
        )

    def run_dev_experiment(self) -> DevResult:
        if self.processes:
            raise ValueError("cell already attempted; reconcile receipt instead of retry")
        failures = []
        for label, command in (
            ("cli", self.native_cli),
            (
                "native",
                (
                    "python",
                    "/harness/epipilot/benchfirst/native_smoke.py",
                    "--family",
                    self.family,
                    *(("--inject-crash",) if self.inject_crash else ()),
                ),
            ),
        ):
            result = self.sandbox.run(
                self.harness, self.native, self.work, command, self.cell / f"{label}.log"
            )
            self.processes.append(result)
            if result.exit_code:
                failures.append(label)
        if failures:
            raise ValueError("native capabilities failed: " + ",".join(failures))
        if digest(self.work / "task.json") != self.task_digest:
            raise ValueError("public task source digest changed")
        candidate = self.export_frozen_candidate()
        prediction_path = self.work / "prediction.json"
        self.dev = DevResult(
            mse=evaluate_dev(self.task, candidate, strict_predictions(prediction_path)),
            candidate_sha256=candidate.sha256,
            prediction_sha256=digest(prediction_path),
            evaluator_sha256=digest(Path(__file__).with_name("offline.py")),
        )
        return self.dev

    def export_frozen_candidate(self) -> FrozenCandidate:
        check_source(self.source)
        # Native tree must remain exactly pinned, including ignored/untracked inputs.
        if git(self.native, "rev-parse", "HEAD") != self.source.sha or git(
            self.native, "status", "--porcelain", "--ignored", "--untracked-files=all"
        ):
            raise ValueError("native source digest changed")
        current = freeze_candidate(self.work / "candidate.py")
        if self.frozen is None:
            frozen_dir = self.cell / "frozen"
            frozen_dir.mkdir()
            target = frozen_dir / "candidate.py"
            with target.open("xb") as handle:
                handle.write(current.path.read_bytes())
            target.chmod(0o444)
            self.frozen = freeze_candidate(target)
        if self.frozen.sha256 != current.sha256:
            raise ValueError("candidate changed after freezing")
        return self.frozen

    def finalize_receipt(self) -> Path:
        path = self.cell / "receipt.json"
        receipt = {
            "family": self.family,
            "source_sha": self.source.sha,
            "task_id": self.task.task_id,
            "seed": self.task.seed,
            "task_sha256": self.task_digest,
            "synthetic_only": True,
            "baseline_sha": self.baseline_sha,
            "real_model_attempts": 0,
            "real_training_experiments": 0,
            "provider_billed_usd": 0,
            "host_cost_usd": None,
            "hidden_score": None,
            "scientific_qualification": False,
            "dev": self.dev.model_dump(mode="json") if self.dev else None,
            "processes": [p.model_dump(mode="json") for p in self.processes],
            "offline_status": "passed" if self.dev else "failed_or_incomplete",
        }
        # Persist only once. Recovery reads previous receipts; never overwrite history.
        with path.open("x") as handle:
            json.dump(receipt, handle, indent=2)
        return path


class AgentOnlyAdapter(SourceAdapter):
    family = "agent_only"
    # This checks the offline executor seam, not access to a paid coding-agent session.
    native_cli = ("python", "-m", "epipilot.benchfirst.native_smoke", "--help")


class AideAdapter(SourceAdapter):
    family = "aide_ml"
    # Zero steps and fixed metric direction suppress both metric inference and reports.
    native_cli = (
        "python",
        "-m",
        "aide.run",
        "data_dir=/work/input",
        "goal=S0-offline",
        "agent.steps=0",
        "agent.metric_maximize=false",
        "generate_report=false",
        "preprocess_data=false",
        "log_dir=/work/cli-logs",
        "workspace_dir=/work/cli-work",
    )


class AiraGreedyAdapter(SourceAdapter):
    family = "aira_dojo"
    native_cli = ("python", "-m", "dojo.main_run", "--help")


class EpiPilotAdapter(SourceAdapter):
    family = "epipilot_current"
    native_cli = ("python", "-m", "epipilot.research", "--help")
