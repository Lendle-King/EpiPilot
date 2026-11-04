"""Operator-gated real execution in a private Docker network, with a separate gateway.

This runner does not authorize a model call. An externally approved, private, immutable
approval file is required. No approval, key or dataset labels are shipped with the repo.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from epipilot.benchfirst.adapters import Source, check_source, git
from epipilot.benchfirst.contracts import SHA256, read_strict_json
from epipilot.benchfirst.gateway import Gateway, OpenAIHTTPS, serve
from epipilot.benchfirst.isolation import Limits, OfflineDocker
from epipilot.benchfirst.metering import Meter, RequestBudget
from epipilot.benchfirst.offline import Value, digest, freeze_candidate
from epipilot.benchfirst.private_scoring import ClosedRun, LabelSet, SealedScorer
from epipilot.benchfirst.real_agents import RealTask, aide_command, codex_command


class LivePlan(Value):
    family: Literal["agent_only", "aide_ml"]
    source: Source
    task: RealTask
    image: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gateway_image: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    model: str = Field(min_length=1)
    # Neither model/tool parity nor development trial enforcement is qualified yet.
    track: Literal["calibration"]

    @property
    def sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class OperatorApproval(Value):
    operator_reference: str = Field(min_length=1)
    approved_at: datetime
    expires_at: datetime
    plan_sha256: str = Field(pattern=SHA256)
    budget: RequestBudget

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (
            self.approved_at.utcoffset() is None
            or self.expires_at.utcoffset() is None
            or self.plan_sha256 != self.budget.plan_sha256
            or self.operator_reference != self.budget.approval_ref
        ):
            raise ValueError("approval identity/timezone mismatch")
        return self

    def check(self, plan: LivePlan) -> None:
        if (
            not self.approved_at <= datetime.now(UTC) < self.expires_at
            or self.plan_sha256 != plan.sha256
            or self.budget.model != plan.model
            or self.budget.max_attempts == 0
            or self.budget.max_cost_nusd == 0
        ):
            raise ValueError("no current matching nonzero operator approval")


def read_approval(path: Path) -> OperatorApproval:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_mode & 0o077
        or path.stat().st_uid != os.getuid()
    ):
        raise ValueError("approval must be a private controller-owned file (0600)")
    return OperatorApproval.model_validate(read_strict_json(path))


def docker(*args: str) -> str:
    return subprocess.check_output(
        ["docker", *args], text=True, stderr=subprocess.STDOUT, timeout=30
    ).strip()


class GatewayDocker(OfflineDocker):
    def __init__(self, image: str, limits: Limits, network: str) -> None:
        super().__init__(image, limits)
        self.network = network

    def argv(
        self, name: str, harness: Path, source: Path, work: Path, command: tuple[str, ...]
    ) -> tuple[str, ...]:
        info = json.loads(docker("network", "inspect", self.network))[0]
        if not info["Internal"] or info["Labels"].get("epipilot.role") != "isolated-agent":
            raise ValueError("agent network must be controller-created and internal")
        original = super().argv(name, harness, source, work, command)
        index = original.index(self.image)
        network_args = tuple(
            f"--network={self.network}" if x == "--network=none" else x for x in original[:index]
        )
        return (
            *network_args,
            "--env",
            "EPIPILOT_REAL_CONTAINER=1",
            "--env",
            "EPIPILOT_GATEWAY_TOKEN",
            "--env",
            "OPENAI_API_KEY",
            "--env",
            "OPENAI_BASE_URL=http://gateway:8080/v1",
            "--env",
            "CODEX_HOME=/tmp/codex",
            "--env",
            "OMP_NUM_THREADS=2",
            "--env",
            "OPENBLAS_NUM_THREADS=2",
            *original[index:],
        )


def run_live(
    plan: LivePlan,
    public: Path,
    output: Path,
    approval_path: Path,
    provider_key: Path,
    harness: Path,
) -> Path:
    approval = read_approval(approval_path)
    approval.check(plan)
    if provider_key.is_symlink() or provider_key.stat().st_mode & 0o077:
        raise ValueError("provider key file must be private (0600), outside agent work")
    if any(p.resolve().is_relative_to(public.resolve()) for p in (approval_path, provider_key)):
        raise ValueError("control files overlap public task")
    check_source(plan.source)
    plan.task.check_files(public)
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    control, audit, work = (output / n for n in ("control", "audit", "work"))
    for directory in (control, audit, work):
        directory.mkdir()
    # Trusted gateway UID needs its own writable audit directory, never agent-mounted.
    if os.getuid() == 0:
        raise ValueError("real controller must use an unprivileged identity")
    audit.chmod(0o700)
    shutil.copyfile(approval_path, control / "approval.json")
    shutil.copyfile(provider_key, control / "provider.key")
    for path in control.iterdir():
        path.chmod(0o444)
    (output / "plan.json").write_text(plan.model_dump_json(indent=2))
    for name in plan.task.public_files:
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(public / name, target)
        target.chmod(0o666 if name == "candidate.py" else 0o444)
    (work / "input").mkdir()
    for name in ("train.csv", "dev.csv", "test.csv"):
        shutil.copyfile(work / name, work / "input" / name)
        (work / "input" / name).chmod(0o444)
    git(work, "init", "-q")
    git(work, "add", "--", *plan.task.public_files, "input")
    tree = git(work, "write-tree")
    commit_environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Benchmark baseline",
        "GIT_COMMITTER_NAME": "Benchmark baseline",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }
    baseline_sha = subprocess.check_output(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "commit.gpgsign=false",
            "commit-tree",
            tree,
            "-m",
            "Frozen public benchmark task",
        ],
        env=commit_environment,
        text=True,
        timeout=30,
    ).strip()
    git(work, "update-ref", "HEAD", baseline_sha)
    work.chmod(0o777)
    token = uuid4().hex + uuid4().hex
    network, gateway_name = "epi-live-" + uuid4().hex, "epi-gateway-" + uuid4().hex
    network_created = gateway_created = False
    receipt: dict[str, object] = {
        "plan_sha256": plan.sha256,
        "family": plan.family,
        "track": plan.track,
        "status": "failed",
        "hidden_score": None,
        "provider_billed_nusd": None,
        "scientific_qualification": False,
        "baseline_git_sha": baseline_sha,
    }
    try:
        docker(
            "network", "create", "--internal", "--label", "epipilot.role=isolated-agent", network
        )
        network_created = True
        docker(
            "create",
            "--name",
            gateway_name,
            "--network",
            network,
            "--network-alias",
            "gateway",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--user={os.getuid()}:{os.getgid()}",
            "--cpus=0.5",
            "--memory=256m",
            "--memory-swap=256m",
            "--pids-limit=32",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=32m",
            "--mount",
            f"type=bind,src={harness.resolve()},dst=/harness,readonly",
            "--mount",
            f"type=bind,src={control.resolve()},dst=/control,readonly",
            "--mount",
            f"type=bind,src={audit.resolve()},dst=/audit",
            "--env",
            "PYTHONPATH=/harness",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "EPIPILOT_GATEWAY_TOKEN",
            plan.gateway_image,
            "python",
            "-m",
            "epipilot.benchfirst.live",
            "gateway",
        )
        gateway_created = True
        docker("network", "connect", "bridge", gateway_name)
        # Capability is controller-only on disk; the agent gets only its scoped capability.
        (control / "capability").write_text(token)
        (control / "capability").chmod(0o444)
        docker("start", gateway_name)
        for _ in range(30):
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    gateway_name,
                    "python",
                    "-c",
                    "import socket; socket.create_connection(('127.0.0.1',8080),1).close()",
                ],
                capture_output=True,
                timeout=3,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise ValueError("gateway startup failed; no model calls authorized by startup")
        # Conservative compute ceiling: 2 CPUs * wall <= training CPU allowance.
        # Split the compute allowance between the agent and independent final rerun.
        seconds = min(approval.budget.wall_seconds // 2, plan.task.training_seconds // 4)
        sandbox = GatewayDocker(plan.image, Limits(memory_mb=4096, seconds=seconds), network)
        command = (
            codex_command(plan.task, plan.model)
            if plan.family == "agent_only"
            else aide_command(plan.task, plan.model)
        )
        process = sandbox.run(
            harness,
            plan.source.checkout,
            work,
            command,
            output / "agent.log",
            environment={
                "PATH": os.environ["PATH"],
                "HOME": str(Path.home()),
                "EPIPILOT_GATEWAY_TOKEN": token,
                "OPENAI_API_KEY": token,
            },
        )
        receipt["process"] = process.model_dump(mode="json")
        plan.task.check_files(work, after_run=True)
        for name in ("train.csv", "dev.csv", "test.csv"):
            if digest(work / "input" / name) != plan.task.public_files[name]:
                raise ValueError("native input data changed")
        candidate = work / "candidate.py"
        if plan.family == "aide_ml":
            solutions = list((work / "aide-logs").glob("*/best_solution.py"))
            if len(solutions) != 1:
                raise ValueError("native AIDE did not export one best solution")
            candidate = solutions[0]
        if not candidate.resolve().is_relative_to(work.resolve()):
            raise ValueError("candidate export escaped agent workspace")
        frozen = freeze_candidate(candidate)
        if process.exit_code != 0:
            raise ValueError("real agent failed; retain all attempts and partial artifacts")
        (output / "frozen").mkdir()
        shutil.copyfile(frozen.path, output / "frozen" / "candidate.py")
        if digest(output / "frozen" / "candidate.py") != frozen.sha256:
            raise ValueError("candidate changed during export")
        receipt.update(status="candidate_exported_unscored", candidate_sha256=frozen.sha256)
        verify_work = output / "verification"
        verify_work.mkdir()
        for name in plan.task.public_files:
            target = verify_work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(public / name, target)
        shutil.copyfile(output / "frozen" / "candidate.py", verify_work / "candidate.py")
        (verify_work / "input").mkdir()
        for name in ("train.csv", "dev.csv", "test.csv"):
            shutil.copyfile(verify_work / name, verify_work / "input" / name)
        verify_work.chmod(0o777)
        verification = OfflineDocker(plan.image, Limits(memory_mb=4096, seconds=seconds)).run(
            harness,
            plan.source.checkout,
            verify_work,
            ("python", "candidate.py"),
            output / "verification.log",
        )
        receipt["verification"] = verification.model_dump(mode="json")
        if verification.exit_code != 0:
            raise ValueError("independent candidate rerun failed")
        plan.task.check_files(verify_work, after_run=True)
        for name in ("dev_predictions.csv", "predictions.csv"):
            path = verify_work / name
            if not path.resolve().is_relative_to(verify_work.resolve()):
                raise ValueError("prediction escaped verifier workspace")
            snapshot = freeze_candidate(path)
            shutil.copyfile(snapshot.path, output / "frozen" / name)
            if digest(output / "frozen" / name) != snapshot.sha256:
                raise ValueError("prediction changed during export")
        closure = hashlib.sha256(verification.model_dump_json().encode()).hexdigest()
        closed = ClosedRun(
            cell_id=approval.budget.cell_id,
            track=plan.track,
            task_sha256=plan.task.fingerprint,
            candidate_sha256=frozen.sha256,
            predictions_sha256=digest(output / "frozen" / "predictions.csv"),
            closure_sha256=closure,
        )
        (output / "closed.json").write_text(closed.model_dump_json(indent=2))
        with (public / "dev.csv").open() as handle:
            rows = list(csv.DictReader(handle))
        labels = LabelSet(
            row_ids=tuple(r["row_id"] for r in rows), values=tuple(float(r["target"]) for r in rows)
        )
        dev_closed = closed.model_copy(
            update={"predictions_sha256": digest(output / "frozen" / "dev_predictions.csv")}
        )
        score = SealedScorer(
            labels, digest(Path(__file__).with_name("private_scoring.py")), secrets.token_bytes(32)
        ).score(dev_closed, output / "frozen" / "dev_predictions.csv")
        receipt.update(status="candidate_verified_dev", independent_dev_rmse=score.rmse)
    except Exception as error:
        receipt["failure_type"] = type(error).__name__
        # No exception text: subprocess output/provider errors may include sensitive data.
    finally:
        if gateway_created:
            subprocess.run(
                ["docker", "rm", "-f", gateway_name], capture_output=True, timeout=30, check=False
            )
        if network_created:
            subprocess.run(
                ["docker", "network", "rm", network], capture_output=True, timeout=30, check=False
            )
        for ephemeral in (control / "provider.key", control / "capability"):
            ephemeral.unlink(missing_ok=True)
        meter_path = audit / "requests.sqlite3"
        if meter_path.exists():
            receipt["requests"] = (
                Meter(meter_path, approval.budget).summary().model_dump(mode="json")
            )
        path = output / "receipt.json"
        path.write_text(json.dumps(receipt, indent=2, allow_nan=False))
        # No labels or scoring keys are ever mounted, and scoring is a separate operation.
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["gateway"])
    parser.parse_args()
    approval = OperatorApproval.model_validate(read_strict_json(Path("/control/approval.json")))
    meter = Meter(Path("/audit/requests.sqlite3"), approval.budget)
    key = Path("/control/provider.key").read_text().strip()
    capability = Path("/control/capability").read_text().strip()
    serve(
        Gateway(
            meter,
            OpenAIHTTPS(key),
            authorized=lambda: approval.approved_at <= datetime.now(UTC) < approval.expires_at,
        ),
        capability,
        "0.0.0.0",
    )


if __name__ == "__main__":
    main()
