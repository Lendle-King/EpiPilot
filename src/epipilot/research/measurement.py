"""Registered input-bound measurements; raw artifacts and reports cannot self-certify."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol

from pydantic import Field

from epipilot.research.discovery import safe_input, validate_contract
from epipilot.research.engine import ResearchEngine
from epipilot.research.frontier import FrontierValue, MeasurementContract, Quality
from epipilot.research.models import (
    Assessment,
    Cost,
    ExecutionStatus,
    ExperimentProposal,
    Metric,
    Observation,
    Outcome,
    Scope,
)


class Measured(FrontierValue):
    metrics: tuple[Metric, ...]
    samples: int = Field(ge=0)
    details: tuple[str, ...] = ()


class Computer(Protocol):
    @property
    def quality(self) -> Quality: ...

    def measure(self, root: Path, contract: MeasurementContract) -> Measured: ...


@dataclass(frozen=True)
class MissingnessComputer:
    quality: Quality = Quality.MEASUREMENT

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        total = missing = before = after = files_with_missing = 0
        details = []
        boundary = date.fromisoformat(contract.boundary_date)
        cutoff = date.fromisoformat(contract.cutoff_date) if contract.cutoff_date else None
        for source in contract.inputs:
            count = old = new = 0
            with safe_input(root, source.path).open(newline="", encoding="utf8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    when = date.fromisoformat(row[contract.date_column])
                    if cutoff and when > cutoff:
                        raise ValueError("input observation exceeds preregistered cutoff")
                    flag = row[contract.value_column].strip().lower()
                    if flag not in {"true", "false", "1", "0", "1.0", "0.0", ""}:
                        raise ValueError("missingness adapter requires explicit observed flags")
                    total += 1
                    if flag in {"false", "0", "0.0", ""}:
                        count += 1
                        if when < boundary:
                            old += 1
                        else:
                            new += 1
            missing += count
            before += old
            after += new
            files_with_missing += int(count > 0)
            details.append(f"{source.path}: missing={count},before={old},after={new}")
        values = {
            "rows": float(total),
            "missing_rows": float(missing),
            "missing_before_boundary": float(before),
            "missing_after_boundary": float(after),
            "files_with_missing": float(files_with_missing),
            "before_share_of_missing": before / missing if missing else 0.0,
        }
        return Measured(
            metrics=tuple(Metric(name=k, value=v) for k, v in values.items()),
            samples=total,
            details=tuple(details),
        )


@dataclass(frozen=True)
class SeriesComputer:
    quality: Quality = Quality.MEASUREMENT

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if len(contract.inputs) != 1:
            raise ValueError("series measurement requires one chronological return series")
        values = []
        previous_date = None
        for source in contract.inputs:
            with safe_input(root, source.path).open(newline="", encoding="utf8") as handle:
                for row in csv.DictReader(handle):
                    when = date.fromisoformat(row[contract.date_column])
                    if previous_date is not None and when <= previous_date:
                        raise ValueError("series dates must be unique and increasing")
                    previous_date = when
                    if contract.cutoff_date and when > date.fromisoformat(contract.cutoff_date):
                        raise ValueError("series exceeds preregistered cutoff")
                    value = float(row[contract.value_column])
                    if not math.isfinite(value) or value <= -1:
                        raise ValueError("invalid numeric return observation")
                    values.append(value)
        if not values:
            raise ValueError("empty measurement sample")
        equity = peak = 1.0
        mdd = 0.0
        for value in values:
            equity *= 1 + value
            peak = max(peak, equity)
            mdd = max(mdd, 1 - equity / peak)
        metrics = {
            "rows": float(len(values)),
            "mean": statistics.mean(values),
            "compound_return": equity - 1,
            "mdd_abs": mdd,
        }
        if len(values) >= 252:
            metrics["cagr"] = equity ** (252 / len(values)) - 1
        return Measured(
            metrics=tuple(Metric(name=k, value=v) for k, v in metrics.items()), samples=len(values)
        )


@dataclass(frozen=True)
class IntegrityComputer:
    quality: Quality = Quality.INTEGRITY

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        return Measured(
            metrics=(Metric(name="files", value=float(len(contract.inputs))),),
            samples=len(contract.inputs),
        )


def file_sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def artifact_payload(
    proposal: ExperimentProposal,
    contract: MeasurementContract,
    measured: Measured,
    error: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "metadata": {
            "proposal_id": proposal.id,
            "input_fingerprint": proposal.input_revision,
            "contract_fingerprint": contract.fingerprint(),
            "contract": contract.model_dump(mode="json"),
            "input_manifest": [item.model_dump(mode="json") for item in contract.inputs],
            "code_revision": contract.git_revision,
            "data_source": contract.data_source,
            "prospective": contract.prospective,
        },
        "measurement": measured.model_dump(mode="json"),
    }
    if error is not None:
        payload["error"] = error
    return payload


class MeasurementAdapter:
    """Inject reviewed domain Computers without changing the search algorithm."""

    def __init__(
        self, engine: ResearchEngine, root: Path, computers: Mapping[str, Computer] | None = None
    ):
        self.engine, self.root = engine, root.resolve()
        self.computers: Mapping[str, Computer] = computers or {
            "missingness": MissingnessComputer(),
            "series": SeriesComputer(),
            "integrity": IntegrityComputer(),
        }

    def contract(self, p: ExperimentProposal) -> MeasurementContract:
        r = self.engine.state.research
        if r is None or r.frontier is None:
            raise ValueError("agent frontier not initialized")
        c = r.frontier.contract(p.id)
        if c.fingerprint() != p.input_revision:
            raise ValueError("input/measurement contract mismatch")
        return c

    def measure(self, p: ExperimentProposal) -> tuple[Measured, Quality]:
        c = self.contract(p)
        validate_contract(self.root, c)
        if c.adapter not in self.computers:
            raise ValueError("no registered domain measurement computer")
        computer = self.computers[c.adapter]
        return computer.measure(self.root, c), computer.quality

    def run(self, p: ExperimentProposal, output: Path) -> Observation:
        if {c.unit for c in p.estimated_cost} != {"wall_seconds"}:
            raise ValueError(
                "built-in runner meters wall_seconds; use a domain runner for other units"
            )
        start = perf_counter()
        contract = self.contract(p)
        try:
            validate_contract(self.root, contract)
            if contract.adapter not in self.computers:
                raise ValueError("no registered domain measurement computer")
            computer = self.computers[contract.adapter]
            measured = computer.measure(self.root, contract)
        except (ValueError, OSError, KeyError) as exc:
            measured = Measured(metrics=(), samples=0)
            payload = artifact_payload(p, contract, measured, type(exc).__name__)
            execution = ExecutionStatus.INVALID
            summary = "measurement failed closed; inputs or adapter invalid"
        else:
            payload = artifact_payload(p, contract, measured)
            execution = ExecutionStatus.SUCCEEDED
            summary = "registered input-bound measurement"
        output.write_text(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2), encoding="utf8"
        )
        return Observation(
            proposal_id=p.id,
            execution=execution,
            metrics=measured.metrics,
            samples=measured.samples,
            actual_cost=(Cost(unit="wall_seconds", amount=perf_counter() - start),),
            artifact=str(output),
            artifact_sha256=file_sha256(output),
            source_revision=contract.git_revision,
            data_cutoff=contract.cutoff_date,
            summary=summary,
            recorded_at=datetime.now(UTC),
        )

    def verify(self, p: ExperimentProposal, o: Observation) -> Assessment:
        verified = False
        quality = Quality.INTEGRITY
        outcome = Outcome.INVALID
        try:
            c = self.contract(p)
            measured, quality = self.measure(p)  # fresh independently executed input recomputation
            artifact = Path(o.artifact)
            payload = json.loads(artifact.read_text(encoding="utf8"))
            expected = artifact_payload(p, c, measured)
            verified = (
                o.artifact_sha256 == file_sha256(artifact)
                and payload == expected
                and o.source_revision == c.git_revision
                and o.data_cutoff == c.cutoff_date
                and (
                    p.scope not in {Scope.INDEPENDENT, Scope.PROSPECTIVE}
                    or quality >= Quality.INDEPENDENT
                )
                and o.execution is ExecutionStatus.SUCCEEDED
                and o.metrics == measured.metrics
                and o.samples == measured.samples
            )
            metrics = {m.name: m.value for m in measured.metrics}
            if verified:
                if o.samples < p.minimum_samples or any(
                    rule.metric not in metrics for rule in c.rules
                ):
                    outcome = Outcome.INCONCLUSIVE
                else:
                    passed = all(
                        (rule.minimum is None or metrics[rule.metric] >= rule.minimum)
                        and (rule.maximum is None or metrics[rule.metric] <= rule.maximum)
                        for rule in c.rules
                    )
                    outcome = (
                        Outcome.SUPPORT
                        if passed
                        else Outcome.REFUTE
                        if p.definitive_negative
                        else Outcome.INCONCLUSIVE
                    )
        except (ValueError, OSError, KeyError):
            pass
        return Assessment(
            proposal_id=p.id,
            verified=verified,
            outcome=outcome,
            reliability=1.0 if verified else 0.0,
            quality=quality,
            scope=p.scope,
            qualified_for_promotion=False,
            reasons=(
                "independent recomputation matches preregistered input-bound measurement"
                if verified
                else "missing/changed inputs, artifact or measured values",
                "measured data property only; no causal or independent-forward promotion",
            ),
        )
