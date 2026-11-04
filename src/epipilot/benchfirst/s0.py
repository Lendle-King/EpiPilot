"""Sequential zero-provider qualification orchestrator; never schedules S1."""

from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from epipilot.benchfirst.adapters import (
    AgentOnlyAdapter,
    AideAdapter,
    AiraGreedyAdapter,
    EpiPilotAdapter,
    Family,
    Source,
    SourceAdapter,
)
from epipilot.benchfirst.contracts import SHA256, AdapterSpec, read_strict_json
from epipilot.benchfirst.isolation import Limits, OfflineDocker
from epipilot.benchfirst.offline import MockBudget, PublicTask, Value, digest


class S0Config(Value):
    schema_version: Literal[1]
    model: Literal["mock-only"]
    inject_crash: bool = False
    images: dict[Family, str]
    limits: Limits
    budget: MockBudget
    evaluator_sha256: str = Field(pattern=SHA256)
    task: PublicTask
    adapters: tuple[AdapterSpec, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def core_arms(self) -> Self:
        if {a.id for a in self.adapters} != {
            "agent_only",
            "aide_ml",
            "aira_dojo",
            "epipilot_current",
        } or any(a.id != a.family for a in self.adapters):
            raise ValueError("S0 requires the four explicit core arms")
        if set(self.images) != {a.id for a in self.adapters}:
            raise ValueError("every arm requires an explicit image")
        for image in self.images.values():
            OfflineDocker(image, self.limits)
        return self


def run_s0(config_path: Path, workspace: Path, output: Path) -> Path:
    """Attempt every arm once, retain failures, and refuse to refill an existing run."""
    config = S0Config.model_validate(read_strict_json(config_path))
    if digest(Path(__file__).with_name("offline.py")) != config.evaluator_sha256:
        raise ValueError("unexpected dev evaluator SHA")
    workspace = workspace.resolve(strict=True)
    output = output.absolute()
    if output.is_symlink() or output.exists():
        raise ValueError("existing run cannot be overwritten/refilled")
    lock = workspace / ".s0-execution.lock"
    if lock.is_symlink():
        raise ValueError("unsafe global execution lock")
    harness = Path(__file__).resolve().parents[2]
    implementations: dict[str, type[SourceAdapter]] = {
        "agent_only": AgentOnlyAdapter,
        "aide_ml": AideAdapter,
        "aira_dojo": AiraGreedyAdapter,
        "epipilot_current": EpiPilotAdapter,
    }
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir(parents=True)
        (output / "config.json").write_bytes(config_path.read_bytes())
        files = sorted(Path(__file__).parent.glob("*.py"))
        source_digest = hashlib.sha256(
            b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in files)
        ).hexdigest()
        rows = []
        for spec in config.adapters:
            checkout = workspace / spec.checkout
            if not checkout.resolve().is_relative_to(workspace):
                raise ValueError("source escapes workspace")
            adapter = implementations[spec.id](
                Source(checkout=checkout, sha=spec.source_sha, url=spec.source_url),
                output / spec.id,
                harness,
                inject_crash=config.inject_crash,
            )
            error = None
            try:
                adapter.prepare(
                    config.task,
                    config.model,
                    config.budget,
                    OfflineDocker(config.images[adapter.family], config.limits),
                )
                adapter.run_dev_experiment()
            except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                # Never expose raw external response/traceback in public receipt.
                error = type(exc).__name__
            finally:
                receipt = (
                    adapter.finalize_receipt()
                    if (adapter.cell / "prepared.json").is_file()
                    else None
                )
                row = {
                    "adapter": spec.id,
                    "source_sha": spec.source_sha,
                    "adapter_bundle_sha256": source_digest,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "task_id": config.task.task_id,
                    "seed": config.task.seed,
                    "status": "offline_pass" if adapter.dev else "NOT_QUALIFIED",
                    "error_class": error,
                    "receipt": str(receipt) if receipt else None,
                    "real_model_calls": 0,
                    "provider_billed_usd": 0,
                    "host_billed_usd": None,
                    "matched_model_verified": False,
                    "scientific_ranking": None,
                }
                with (output / "receipts.jsonl").open("a") as ledger:
                    ledger.write(json.dumps(row) + "\n")
                    ledger.flush()
                rows.append(row)
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "arms": rows,
                    "s0_gate": "INCONCLUSIVE",
                    "reason": "Offline plumbing alone does not verify live model parity, "
                    "full native "
                    "dependencies or a private scoring deployment",
                    "s1_authorized": False,
                },
                indent=2,
            )
        )
    return output / "summary.json"


def reconcile_s0(workspace: Path, output: Path) -> Path:
    """Explicit crash settlement only. No container/model launch and no rerun/refill."""
    lock = workspace.resolve(strict=True) / ".s0-execution.lock"
    if lock.is_symlink() or output.is_symlink():
        raise ValueError("unsafe recovery location")
    config = S0Config.model_validate(read_strict_json(output / "config.json"))
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target = output / "reconciliation.json"
        if target.exists():
            read_strict_json(target)  # reject corruption; idempotent settlement
            return target
        cells = []
        for spec in config.adapters:
            receipt = output / spec.id / "receipt.json"
            if receipt.is_file():
                read_strict_json(receipt)
                status = "terminal_receipt_retained"
            elif (output / spec.id / "prepared.json").is_file():
                status = "interrupted_unknown_outcome_no_retry"
            else:
                status = "not_started_no_refill"
            cells.append({"adapter": spec.id, "status": status})
        with target.open("x") as handle:
            json.dump(
                {
                    "cells": cells,
                    "real_model_calls": 0,
                    "mock_usage_if_interrupted": None,
                    "action": "settlement_only_no_retry",
                },
                handle,
                indent=2,
            )
    return target
