"""Local algorithm execution boundary and independent artifact validation.

Only a configured, committed repository adapter can execute. Model text never
becomes a shell command. Receipts are observations; daily returns, equity and
metrics must agree before the loop may archive a completed experiment.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from epipilot.research.engine import ResearchEngine
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric
from epipilot.workspace.onboarding import git

AlgorithmId = Literal[
    "dual_momentum",
    "dual_momentum_12vol",
    "dual_momentum_126trend",
    "dual_momentum_126trend_15vol",
    "dual_momentum_126trend_14vol",
    "dual_momentum_126trend_135vol",
    "dual_momentum_252trend",
    "dual_momentum_252trend_15vol",
    "trend_risk_parity",
    "defensive_momentum",
]

ETF_ALGORITHM_ADAPTERS: dict[str, AlgorithmId] = {
    "quant_etf_dual_momentum_v1": "dual_momentum",
    "quant_etf_dual_momentum_12vol_v1": "dual_momentum_12vol",
    "quant_etf_dual_momentum_126trend_v1": "dual_momentum_126trend",
    "quant_etf_dual_momentum_126trend_15vol_v1": "dual_momentum_126trend_15vol",
    "quant_etf_dual_momentum_126trend_15vol_v2": "dual_momentum_126trend_15vol",
    "quant_etf_dual_momentum_126trend_14vol_v1": "dual_momentum_126trend_14vol",
    "quant_etf_dual_momentum_126trend_135vol_v1": "dual_momentum_126trend_135vol",
    "quant_etf_dual_momentum_252trend_v1": "dual_momentum_252trend",
    "quant_etf_dual_momentum_252trend_15vol_v1": "dual_momentum_252trend_15vol",
    "quant_etf_trend_risk_parity_v1": "trend_risk_parity",
    "quant_etf_defensive_momentum_v1": "defensive_momentum",
}


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    route_run_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,60}$")
    round_index: int = Field(ge=1)
    algorithm_id: AlgorithmId


class FileDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExecutionMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    variant: str
    cost_bps: float = Field(ge=0)
    daily_path: str
    cagr: float
    max_drawdown: float = Field(ge=-1, le=0)
    annualized_turnover: float = Field(ge=0)


class ExecutionRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    python_executable: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    numpy_version: str = Field(min_length=1)
    pandas_version: str = Field(min_length=1)


class ExecutionReceipt(ExecutionRequest):
    started_at_utc: datetime
    finished_at_utc: datetime
    scope: Literal["exploratory_public_history"]
    runtime: ExecutionRuntime | None = None
    initial_cash: float = Field(gt=0, allow_inf_nan=False)
    input_files: list[FileDigest] = Field(min_length=1)
    artifacts: list[FileDigest] = Field(min_length=1)
    metrics: list[ExecutionMetric] = Field(min_length=1)
    protocol: str = Field(min_length=20)


class AlgorithmExecutorPort(Protocol):
    def execute(self, request: ExecutionRequest) -> Path:
        """Return the path of a newly executed, route-bound receipt."""
        ...


def repository_file(repo: Path, relative: str) -> Path:
    path = (repo / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(repo.resolve()):
        raise ValueError("Evidence path escapes the repository")
    if path.is_symlink() or not path.is_file():
        raise ValueError("Evidence file is unavailable")
    return path


def verify_execution(
    repo: Path, manifest: Path, route_run_id: str, round_index: int
) -> ExecutionReceipt:
    receipt = ExecutionReceipt.model_validate_json(manifest.read_text(encoding="utf-8"))
    if receipt.route_run_id != route_run_id or receipt.round_index != round_index:
        raise ValueError("Execution receipt does not match the current route and round")
    start, end = receipt.started_at_utc, receipt.finished_at_utc
    if start.tzinfo is None or end.tzinfo is None or not start <= end <= datetime.now(UTC):
        raise ValueError("Execution timestamps must be ordered, aware and not in the future")
    for ref in (*receipt.input_files, *receipt.artifacts):
        path = repository_file(repo, ref.path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != ref.sha256:
            raise ValueError(f"Execution evidence hash mismatch: {ref.path}")
    artifact_paths = {ref.path for ref in receipt.artifacts}
    if not {("baseline", 5.0), ("candidate", 5.0)}.issubset(
        {(metric.variant, metric.cost_bps) for metric in receipt.metrics}
    ):
        raise ValueError("Execution requires measured baseline and candidate at primary 5bp cost")
    for metric in receipt.metrics:
        if metric.daily_path not in artifact_paths:
            raise ValueError("Daily metric file is missing from artifact inventory")
        with repository_file(repo, metric.daily_path).open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) < 2:
            raise ValueError("Daily results require at least two observations")
        wealth, peak, drawdown, turnover = 1.0, 1.0, 0.0, 0.0
        previous_date = ""
        for row in rows:
            date = row["date"]
            datetime.fromisoformat(date)
            ret, equity, traded = (float(row[key]) for key in ("return", "equity", "turnover"))
            if date <= previous_date or not all(math.isfinite(v) for v in (ret, equity, traded)):
                raise ValueError("Daily evidence must be finite with unique ascending dates")
            if ret <= -1 or equity <= 0 or traded < 0:
                raise ValueError("Invalid daily return, equity or turnover")
            previous_date = date
            wealth *= 1 + ret
            peak = max(peak, wealth)
            drawdown = min(drawdown, wealth / peak - 1)
            turnover += traded
            if not math.isclose(equity, wealth * receipt.initial_cash, rel_tol=1e-8, abs_tol=1e-5):
                raise ValueError("Daily equity does not reconcile with returns")
        measured = (wealth ** (252 / len(rows)) - 1, drawdown, turnover * 252 / len(rows))
        reported = (metric.cagr, metric.max_drawdown, metric.annualized_turnover)
        if not all(
            math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-8)
            for a, b in zip(measured, reported, strict=True)
        ):
            raise ValueError("Reported metrics disagree with daily evidence")
    return receipt


class LocalAlgorithmExecutor:
    """Invoke one explicitly configured adapter without shell or secret inheritance."""

    def __init__(self, repo: Path, python_executable: Path) -> None:
        self.repo = repo.resolve()
        self.python_executable = python_executable

    def execute(self, request: ExecutionRequest) -> Path:
        script = "scripts/run_epipilot_algorithm_round.py"
        tracked = git(self.repo, "ls-files", "--", script)
        if tracked != script or git(
            self.repo,
            "status",
            "--porcelain",
            "--",
            "scripts",
            "src",
            "configs/v25_lock.yaml",
            "configs/v25_candidate.yaml",
            "configs/public_diagnostic_universe_20261018.yaml",
        ):
            raise ValueError("Algorithm adapter and source must be committed and clean")
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}
        }
        env["PYTHONPATH"] = str(self.repo / "src")
        process = subprocess.run(
            [str(self.python_executable), script],
            input=request.model_dump_json(),
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            timeout=7200,
            check=False,
        )
        if process.returncode:
            # Persist no raw subprocess response: it can contain sensitive source data.
            raise RuntimeError(f"Local algorithm execution failed (exit {process.returncode})")
        try:
            relative = json.loads(process.stdout)["receipt_path"]
            return repository_file(self.repo, str(relative))
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("Local adapter returned an invalid receipt path") from error


class ETFAlgorithmComputer:
    """Bridge a fixed ETF algorithm contract to the committed local runner."""

    quality = Quality.MEASUREMENT

    def __init__(
        self,
        *,
        engine: ResearchEngine,
        repository: Path,
        executor: AlgorithmExecutorPort,
        adapter_id: str,
        algorithm_id: AlgorithmId,
    ) -> None:
        if ETF_ALGORITHM_ADAPTERS.get(adapter_id) != algorithm_id:
            raise ValueError("adapter does not match configured algorithm")
        self.engine = engine
        self.repository = repository.resolve()
        self.executor = executor
        self.adapter_id = adapter_id
        self.algorithm_id = algorithm_id

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if root.resolve() != self.repository:
            raise ValueError("ETF algorithm repository does not match contract root")
        if contract.adapter != self.adapter_id:
            raise ValueError("contract adapter does not match configured algorithm")
        research = self.engine.state.research
        if research is None:
            raise ValueError("ETF algorithm requires configured canonical research")
        route_digest = hashlib.sha256(
            f"{self.engine.project_id}:{contract.proposal_id}:{contract.fingerprint()}".encode()
        ).hexdigest()[:24]
        round_index = len(research.reviews) + 1
        request = ExecutionRequest(
            route_run_id=f"carts-{route_digest}",
            round_index=round_index,
            algorithm_id=self.algorithm_id,
        )
        manifest = self.executor.execute(request)
        receipt = verify_execution(self.repository, manifest, request.route_run_id, round_index)
        if receipt.algorithm_id != self.algorithm_id:
            raise ValueError("execution receipt algorithm differs from the registered adapter")
        contracted_inputs = {(item.path, item.sha256) for item in contract.inputs}
        measured_inputs = {(item.path, item.sha256) for item in receipt.input_files}
        if contracted_inputs != measured_inputs:
            raise ValueError("execution receipt inputs differ from the measurement contract")
        metrics = tuple(
            Metric(name=name, value=value)
            for item in receipt.metrics
            for name, value in (
                (
                    f"{self.algorithm_id}.{item.variant}.{item.cost_bps:g}bp.cagr",
                    item.cagr,
                ),
                (
                    f"{self.algorithm_id}.{item.variant}.{item.cost_bps:g}bp.mdd_abs",
                    abs(item.max_drawdown),
                ),
                (
                    f"{self.algorithm_id}.{item.variant}.{item.cost_bps:g}bp.annualized_turnover",
                    item.annualized_turnover,
                ),
            )
        )
        primary = [
            item for item in receipt.metrics if item.variant == "candidate" and item.cost_bps == 5.0
        ]
        if len(primary) != 1:
            raise ValueError("execution receipt requires one candidate 5bp path")
        daily_path = repository_file(self.repository, primary[0].daily_path)
        with daily_path.open(newline="", encoding="utf-8") as handle:
            samples = sum(1 for _ in csv.DictReader(handle))
        if samples < 2:
            raise ValueError("candidate 5bp path requires at least two daily samples")
        return Measured(
            metrics=metrics,
            samples=samples,
            details=(
                f"algorithm_id={self.algorithm_id}",
                f"route_run_id={request.route_run_id}",
                f"receipt_sha256={hashlib.sha256(manifest.read_bytes()).hexdigest()}",
            ),
        )


def etf_algorithm_computers(
    engine: ResearchEngine,
    repository: Path,
    executor: AlgorithmExecutorPort,
) -> Mapping[str, Computer]:
    """Explicit registry for the fixed adapters implemented by the ETF runner."""
    return {
        adapter_id: ETFAlgorithmComputer(
            engine=engine,
            repository=repository,
            executor=executor,
            adapter_id=adapter_id,
            algorithm_id=algorithm_id,
        )
        for adapter_id, algorithm_id in ETF_ALGORITHM_ADAPTERS.items()
    }


def execution_summary(receipt: ExecutionReceipt) -> str:
    """A deterministic, bounded report section anchored in checked artifacts."""
    lines = [
        "## 已验证的主机算法实验",
        f"路线 ID: {receipt.route_run_id}",
        f"算法: {receipt.algorithm_id}; 证据范围: {receipt.scope}",
        "| variant | cost bps | CAGR | MDD | annual turnover |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in receipt.metrics:
        lines.append(
            f"| {row.variant} | {row.cost_bps:g} | {row.cagr:.6%} | "
            f"{row.max_drawdown:.6%} | {row.annualized_turnover:.6f} |"
        )
    lines.extend(
        [
            receipt.protocol,
            "指标已从日频净值/收益/换手文件独立重算;不证明独立前瞻收益或历史点时资格。",
        ]
    )
    return "\n".join(lines)
