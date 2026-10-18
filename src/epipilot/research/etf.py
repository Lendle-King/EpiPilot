"""Read-only QUANT_ETF adapter: authentic audits, no strategy/data fabrication."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from epipilot.research.engine import ResearchEngine, stable_id
from epipilot.research.models import (
    Assessment,
    Cost,
    ExecutionStatus,
    ExperimentKind,
    ExperimentProposal,
    Fidelity,
    Metric,
    MetricConstraint,
    Observation,
    Outcome,
    ResearchGoal,
    ResearchNode,
    Scope,
    Strategy,
    Value,
)
from epipilot.runtime.event_store import InMemoryEventStore

V8_REF = "origin/v8-ordered-research-tree"
STRESS_REF = "origin/epipilot-execution-stress-f2c666cc"
STRESS_PATH = "docs/epipilot_execution_stress_f2c666cc.md"
PANEL_MANIFEST = "docs/data_sources/public_current_scale_panel_20261018.json"
PANEL_DIR = "data/processed/public_current_scale_panel_20261018"
JOURNALS = tuple(
    f"state/{strategy}/{kind}.csv"
    for strategy in ("v25_shadow", "v3_forward_shadow", "v5_forward_shadow")
    for kind in ("signals", "events")
)
PIT_INPUTS = (
    "selected_constituent_sampling.csv",
    "raw_announced_financial_fields.csv",
    "historical_index_snapshots.csv",
    "index_weight_audit.csv",
)


class StressRow(Value):
    branch: str
    cost: int
    slippage: int
    cagr: float
    mdd: float


class StressAudit(Value):
    scenarios: int
    monotonic: bool
    m1_delta_pp: float
    m2_delta_pp: float
    rows: tuple[StressRow, ...]
    scope: str = "saved report consistency only; original daily ledger unavailable"


def parse_stress(text: str) -> StressAudit:
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 14 and cells[0] in {"M0", "M1", "M2"}:
            rows.append(
                StressRow(
                    branch=cells[0],
                    cost=int(cells[1]),
                    slippage=int(cells[2]),
                    cagr=float(cells[3].rstrip("%")),
                    mdd=float(cells[4].rstrip("%")),
                )
            )
    grid = {(x.branch, x.cost, x.slippage): x for x in rows}
    expected = {(b, c, s) for b in ("M0", "M1", "M2") for c in (5, 10, 20) for s in (0, 5, 10)}
    if len(rows) != 27 or set(grid) != expected:
        raise ValueError("expected 27 unique stress scenarios")
    monotonic = all(
        y.cagr <= x.cagr + 0.0001 and y.mdd <= x.mdd + 0.0001
        for x in rows
        for y in rows
        if x.branch == y.branch
        and (
            (x.cost == y.cost and x.slippage < y.slippage)
            or (x.slippage == y.slippage and x.cost < y.cost)
        )
    )
    return StressAudit(
        scenarios=len(rows),
        monotonic=monotonic,
        m1_delta_pp=grid["M1", 5, 0].cagr - grid["M0", 5, 0].cagr,
        m2_delta_pp=grid["M2", 5, 0].cagr - grid["M0", 5, 0].cagr,
        rows=tuple(rows),
    )


class JournalAudit(Value):
    missing: tuple[str, ...]
    qualified: bool = False
    completed_intervals: int | None = None
    reason: str = "original captured journals and run provenance required; no backfill"


class DataAudit(Value):
    symbols: int
    rows: int
    failures: tuple[str, ...]
    manifest_sha256: str
    scale_missing_rows: int
    scope: str = (
        "current-universe development integrity only; no historical "
        "membership/publication qualification"
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ETFAdapter:
    def __init__(self, repository: Path):
        self.repository = repository.resolve()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repository), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def blob(self, ref: str, path: str) -> str:
        return self.git("show", "--end-of-options", f"{self.pin(ref)}:{path}") + "\n"

    def pin(self, ref: str) -> str:
        return self.git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")

    def audit_journals(self) -> JournalAudit:
        missing = tuple(p for p in JOURNALS if not (self.repository / p).is_file())
        # Existence alone cannot establish capture provenance or mature paired returns.
        return JournalAudit(
            missing=missing,
            reason="original strategy journal unavailable"
            if missing
            else "journals exist; original capture provenance and paired replay not yet qualified",
        )

    def audit_data(self) -> DataAudit:
        manifest_path = self.repository / PANEL_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf8"))
        failures: list[str] = []
        total, missing_scale = 0, 0
        records = manifest["records"]
        for record in records:
            code = record["ts_code"]
            if not re.fullmatch(r"\d{6}\.(SH|SZ)", code):
                raise ValueError("unexpected mainland ETF identifier")
            path = self.repository / PANEL_DIR / f"{code}.csv"
            if not path.is_file():
                failures.append(f"{code}:missing")
                continue
            with path.open(newline="", encoding="utf8") as stream:
                rows = list(csv.DictReader(stream))
            dates = [r["date"] for r in rows]
            total += len(rows)
            missing_scale += sum(
                r.get("scale_observed", "").lower() not in {"true", "1", "1.0"} for r in rows
            )
            checks = {
                "hash": digest(path) == record["processed_sha256"],
                "rows": len(rows) == record["rows"],
                "dates": bool(dates)
                and dates == sorted(set(dates))
                and dates[0] == record["first_date"]
                and dates[-1] == record["last_date"],
                "prices": all(float(r["open"]) > 0 and float(r["close"]) > 0 for r in rows),
                "cutoff": all(d <= manifest["requested_end"] for d in dates),
            }
            failures.extend(f"{code}:{name}" for name, ok in checks.items() if not ok)
        return DataAudit(
            symbols=len(records),
            rows=total,
            failures=tuple(failures),
            manifest_sha256=digest(manifest_path),
            scale_missing_rows=missing_scale,
        )

    def audit_pit(self) -> dict[str, Any]:
        """Intentionally schema-less report artifact; no raw financial data is exported."""
        # Specific expected artifacts only; no credential lookup/network fallback.
        roots = (self.repository / "results/v7_b2_asof_lineage", self.repository / "results/v7_b1")
        available = {name: any((root / name).is_file() for root in roots) for name in PIT_INPUTS}
        return {
            "available": available,
            "qualified": False,
            "reason": (
                "original publication/receipt vintages and PIT membership not "
                "independently established; "
                "ann_date alone is not first availability; financial alpha remains unknown"
            ),
        }

    def collect(self, identifier: str, input_revision: str | None = None) -> dict[str, Any]:
        """Runner/verifier independently read authoritative inputs, not each other's verdicts."""
        if identifier == "journal-availability":
            return self.audit_journals().model_dump(mode="json")
        if identifier == "stress-consistency":
            text = self.blob(input_revision or self.pin(STRESS_REF), STRESS_PATH)
            audit = parse_stress(text)
            return {
                **audit.model_dump(mode="json"),
                "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        if identifier == "public-data-integrity":
            if input_revision and digest(self.repository / PANEL_MANIFEST) != input_revision:
                raise ValueError("input manifest changed; new proposal required")
            return self.audit_data().model_dump(mode="json")
        if identifier == "pit-source-qualification":
            return self.audit_pit()
        raise ValueError("no qualified runner for this experiment")

    @staticmethod
    def interpret(identifier: str, data: dict[str, Any]) -> tuple[Outcome, int, tuple[Metric, ...]]:
        if identifier == "journal-availability":
            return (
                Outcome.BLOCKED,
                0,
                (Metric(name="missing_journal_files", value=float(len(data["missing"]))),),
            )
        if identifier == "pit-source-qualification":
            return Outcome.BLOCKED, 0, (Metric(name="qualified_pit_sources", value=0.0),)
        if identifier == "public-data-integrity":
            outcome = Outcome.SUPPORT if not data["failures"] else Outcome.REFUTE
            return (
                outcome,
                int(data["rows"]),
                (
                    Metric(name="rows_checked", value=float(data["rows"])),
                    Metric(name="symbols_checked", value=float(data["symbols"])),
                    Metric(name="integrity_failures", value=float(len(data["failures"]))),
                    Metric(name="scale_missing_rows", value=float(data["scale_missing_rows"])),
                ),
            )
        if identifier == "stress-consistency":
            return (
                (Outcome.SUPPORT if data["monotonic"] else Outcome.REFUTE),
                int(data["scenarios"]),
                (
                    Metric(name="scenarios_checked", value=float(data["scenarios"])),
                    Metric(name="m1_cagr_delta_pp", value=float(data["m1_delta_pp"])),
                    Metric(name="m2_cagr_delta_pp", value=float(data["m2_delta_pp"])),
                ),
            )
        raise ValueError("unknown ETF experiment")

    def run(self, proposal: ExperimentProposal, output: Path) -> Observation:
        start = perf_counter()
        data = self.collect(proposal.id, proposal.input_revision)
        outcome, samples, metrics = self.interpret(proposal.id, data)
        output.write_text(
            json.dumps(
                {
                    "proposal_id": proposal.id,
                    "input_revision": proposal.input_revision,
                    "data": data,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf8",
        )
        elapsed = perf_counter() - start
        return Observation(
            proposal_id=proposal.id,
            execution=ExecutionStatus.BLOCKED
            if outcome is Outcome.BLOCKED
            else ExecutionStatus.SUCCEEDED,
            metrics=metrics,
            samples=samples,
            actual_cost=(Cost(unit="wall_seconds", amount=elapsed),),
            artifact=str(output),
            summary=f"read-only {proposal.id}; no independent ETF return claim",
            recorded_at=datetime.now(UTC),
        )

    def verify(self, proposal: ExperimentProposal, observation: Observation) -> Assessment:
        data = json.loads(Path(observation.artifact).read_text(encoding="utf8"))
        fresh = self.collect(
            proposal.id, proposal.input_revision
        )  # independent read/recomputation after execution
        outcome, samples, metrics = self.interpret(proposal.id, fresh)
        verified = (
            data
            == {
                "proposal_id": proposal.id,
                "input_revision": proposal.input_revision,
                "data": fresh,
            }
            and observation.metrics == metrics
            and observation.samples == samples
        )
        return Assessment(
            proposal_id=proposal.id,
            verified=verified,
            outcome=outcome if verified else Outcome.INVALID,
            reliability=1.0 if verified else 0.0,
            scope=Scope.INTEGRITY,
            reasons=(
                "independent artifact/source recomputation" if verified else "artifact mismatch",
                "integrity evidence only; no OOS/forward alpha qualification",
            ),
        )

    def initialize(self, engine: ResearchEngine) -> None:
        revision = self.pin(V8_REF)
        stress = self.pin(STRESS_REF)
        project = engine.project_id
        descriptions = {
            "v25": "Frozen V2.5 baseline; historical 6.71% CAGR / -12.43% MDD (development report)",
            "v3": "Frozen V3-ADK parent; ~8.08% / -12.07% development report",
            "v5": (
                "V5-C residual cash historical improvement requires independent "
                "forward confirmation"
            ),
            "forward": "Authentic V25/V3/V5 captured journals and mature intervals are available",
            "v6": "V6-A high-return parent; 12.228% / -23.847%, development only",
            "pit": "Original financial/index membership PIT vintages are available and qualified",
            "overseas": (
                "Overseas source is qualified at domestic decision time; H5 "
                "positive does not override H20 negative"
            ),
            "action": "Incremental alpha has qualified paired net action value",
            "execution": "Actual opens/spreads/capacity observations qualify execution assumptions",
            "combination": (
                "Full portfolio attains >=15% CAGR and <=20% MDD on independent "
                "prospective evidence"
            ),
            "overlay": (
                "Old risk overlay paused after weak deployable action value; no "
                "automatic reactivation"
            ),
            "data": "Current local public ETF panel matches its recorded integrity manifest",
            "stress": "Saved 27-scenario execution report is internally consistent",
        }
        deps = {
            "action": ("pit",),
            "combination": ("action", "execution", "forward"),
            "execution": ("stress",),
        }
        nodes = tuple(
            ResearchNode(
                hypothesis_id=stable_id(project, key),
                label=label,
                basis=f"Git {revision} V8 reports; observations only",
                source_refs=(
                    f"git:{stress}:{STRESS_PATH}"
                    if key == "stress"
                    else f"git:{revision}:docs/"
                    + {
                        "v5": "experiment_v5c_global_low_downside_bond_20261007.md",
                        "pit": "v7_b2_asof_lineage_20261014.md",
                        "overseas": "v7_independent_alpha_20261013.md",
                        "overlay": "v4_phase_diagram_20261007.md",
                    }.get(key, "v8_priority_tree_20261014.md"),
                ),
                imported_route_state={
                    "v25": "frozen baseline; forward not independently qualified",
                    "v3": ("frozen parent; recent continuous journal not locally verified"),
                    "v5": "development-positive; zero mature intervals documented Oct14",
                    "v6": "development-only; drawdown above target",
                    "pit": "B1/B2 strict PIT qualification blocked",
                    "overseas": (
                        "H5 small positive, H20 negative; no deployable alpha qualification"
                    ),
                    "overlay": "paused after weak action-value results",
                }.get(key, "qualification pending"),
                and_ids=tuple(stable_id(project, k) for k in deps.get(key, ())),
                contribution=0.95 if key in {"combination", "action", "forward"} else 0.5,
                uncertainty=0.9,
            )
            for key, label in descriptions.items()
        )
        proposals = []

        def add(
            identifier: str,
            key: str,
            cost: float | None,
            info: float,
            *,
            qualified: bool = True,
            reason: str = "",
            fidelity: Fidelity = Fidelity.F0,
        ) -> None:
            input_revision = stress if key == "stress" else revision
            if key == "data":
                if (self.repository / PANEL_MANIFEST).is_file():
                    input_revision = digest(self.repository / PANEL_MANIFEST)
                else:
                    input_revision = "missing-public-panel"
                    qualified, reason = False, "public panel manifest missing"
            proposals.append(
                ExperimentProposal(
                    id=identifier,
                    hypothesis_id=stable_id(project, key),
                    title=descriptions[key],
                    input_revision=input_revision,
                    kind=ExperimentKind.DATA_QUALIFICATION
                    if fidelity is Fidelity.F0
                    else ExperimentKind.DIAGNOSTIC,
                    fidelity=fidelity,
                    scope=Scope.INTEGRITY,
                    estimated_cost=()
                    if cost is None
                    else (Cost(unit="wall_seconds", amount=cost),),
                    cost_uncertainty=1.0,
                    information=info,
                    contribution=0.15 if key == "pit" else 0.5,
                    uncertainty=0.9,
                    risk=0.05,
                    data_qualified=qualified,
                    data_reason=reason,
                    minimum_samples=1,
                    definitive_negative=True,
                    input_refs=(input_revision,),
                    estimate_source=(
                        "explicit local audit wall-time estimate, not historical research cost"
                    ),
                )
            )

        add("journal-availability", "forward", 0.2, 0.95)
        add("pit-source-qualification", "pit", 0.3, 0.95)
        add("stress-consistency", "stress", 0.5, 0.8, fidelity=Fidelity.F1)
        add("public-data-integrity", "data", 5.0, 0.75)
        add(
            "incremental-action-value",
            "action",
            None,
            0.9,
            qualified=False,
            reason="original PIT vintages, mature labels, frozen purged/embargo split unavailable",
        )
        add(
            "actual-execution-capacity",
            "execution",
            None,
            0.8,
            qualified=False,
            reason="original orders and contemporaneous opening quotes/spreads unavailable",
        )
        add(
            "overseas-incremental-alpha",
            "overseas",
            None,
            0.8,
            qualified=False,
            reason=(
                "domestic-cutoff receipt lineage and sufficient nonoverlapping labels unavailable"
            ),
        )
        add(
            "full-prospective-portfolio",
            "combination",
            None,
            0.9,
            qualified=False,
            reason="independent mature forward intervals and previous gates unavailable",
        )
        add(
            "legacy-risk-overlay",
            "overlay",
            None,
            0.0,
            qualified=False,
            reason="paused; V4 action-value negative, no changed evidence",
        )
        engine.initialize(
            ResearchGoal(
                statement=(
                    "Mainland listed ETF daily decisions; earliest next open; CAGR "
                    ">=15%, |MDD|<=20%"
                ),
                budgets=(Cost(unit="wall_seconds", amount=120.0),),
                constraints=(
                    MetricConstraint(name="cagr", minimum=0.15),
                    MetricConstraint(name="mdd_abs", maximum=0.2),
                ),
                required_scope=Scope.PROSPECTIVE,
                minimum_samples=63,
            ),
            nodes,
            tuple(proposals),
        )


def available_records(records: list[dict[str, Any]], at: datetime) -> list[dict[str, Any]]:
    if at.tzinfo is None:
        raise ValueError("replay time must be aware")
    return [r for r in records if datetime.fromisoformat(r["available_at"]) <= at]


def historical_replay(adapter: ETFAdapter) -> dict[str, Any]:
    """Chronological evidence replay. Unknown historical costs => abstain, no invented savings."""
    sources = (
        (
            "4659c48",
            "docs/v4_phase_diagram_20261007.md",
            "V4",
            "pause overlay training pending deployable action value",
        ),
        (
            "ab4f5d4",
            "docs/experiment_v5c_global_low_downside_bond_20261007.md",
            "V5-C",
            "freeze and seek forward confirmation",
        ),
        (
            "a819225",
            "docs/v7_independent_alpha_20261013.md",
            "V7-A1",
            "H5 small positive cannot override H20 negative",
        ),
        (
            "2cb778d",
            "docs/v7_b2_asof_lineage_20261014.md",
            "V7-B2",
            "prioritize original PIT lineage before training",
        ),
        (
            "f38e0aa",
            "docs/v8_priority_tree_20261014.md",
            "V8",
            "restore authentic forward journals; no backfill",
        ),
        (
            "e9768f8",
            STRESS_PATH,
            "execution",
            "report consistency first; original ledger and opening quote calibration next",
        ),
    )
    records: list[dict[str, Any]] = []
    for revision, path, route, decision in sources:
        pin = adapter.pin(revision)
        text = adapter.blob(pin, path)
        committed, authored = adapter.git("show", "-s", "--format=%cI%n%aI", pin).splitlines()
        records.append(
            {
                "revision": pin,
                "path": path,
                "available_at": committed,
                "authored_at": authored,
                "route": route,
                "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "summary": decision,
                "cost": None,
                "evidence_scope": Scope.DEVELOPMENT.value,
            }
        )
    records.sort(key=lambda r: r["available_at"])
    decisions = []
    for record in records:
        at = datetime.fromisoformat(record["available_at"])
        visible = available_records(records, at)
        comparisons = []
        for scenario in ("unknown", "pit_cheaper", "pit_dearer"):
            replay_engine = _replay_frontier(visible, at, scenario)
            for strategy in (Strategy.FIFO, Strategy.EXISTING, Strategy.CARTS):
                ranking = replay_engine.ranking(strategy, at)
                comparisons.append(
                    {
                        "cost_scenario": scenario,
                        "costs_are_measured": False,
                        "strategy": strategy.value,
                        "ranking": ranking.model_dump(mode="json"),
                    }
                )
        decisions.append(
            {
                "at": at.isoformat(),
                "visible_routes": [r["route"] for r in visible],
                "new_information": record["summary"],
                "scheduler_comparisons": comparisons,
                "decision": "abstain_from_cost_ranking",
                "reason": (
                    "actual historical costs unknown; qualitative gate follows "
                    "currently revealed sources only"
                ),
            }
        )
    return {
        "protocol": (
            "conservative Git committer availability; dates in returns are not"
            " knowledge dates; "
            "no claimed counterfactual portfolio returns or measured resource savings"
        ),
        "records": records,
        "decisions": decisions,
        "limitation": (
            "counterfactual ranking uses labeled relative-cost assumptions only; "
            "exact historical costs unknown; no measured historical efficiency "
            "or investment-return comparison"
        ),
    }


def _replay_frontier(
    visible: list[dict[str, Any]], at: datetime, cost_scenario: str
) -> ResearchEngine:
    """Only introduce candidates motivated by already-available source observations."""
    project = "etf-replay-frontier"
    nodes: list[ResearchNode] = []
    proposals: list[ExperimentProposal] = []
    seen = {r["route"] for r in visible}

    def add(
        key: str,
        title: str,
        *,
        cost: float,
        impact: float = 0.5,
        prerequisite: str | None = None,
        qualified: bool = True,
        reason: str = "",
    ) -> None:
        hid = stable_id(project, key)
        nodes.append(
            ResearchNode(
                hypothesis_id=hid,
                label=title,
                basis=";".join(r["revision"] for r in visible),
                and_ids=(stable_id(project, prerequisite),) if prerequisite else (),
                contribution=impact,
                uncertainty=0.9,
            )
        )
        proposals.append(
            ExperimentProposal(
                id=key,
                hypothesis_id=hid,
                title=title,
                input_revision=visible[-1]["revision"],
                available_at=at,
                estimated_cost=()
                if cost_scenario == "unknown"
                else (Cost(unit="relative_estimate", amount=cost),),
                cost_uncertainty=0.5,
                information=0.9,
                contribution=impact,
                uncertainty=0.9,
                data_qualified=qualified,
                data_reason=reason,
                estimate_source=(
                    "labeled sensitivity assumption; NOT historical measured resource/time"
                ),
            )
        )

    if "V4" in seen:
        add(
            "paired-action-audit",
            "Audit deployable paired action value before further risk training",
            cost=2.0,
        )
        add(
            "old-overlay-training",
            "Old learned risk overlay training",
            cost=10.0,
            qualified=False,
            reason="revealed V4 weak/negative action-value evidence; no changed premise",
        )
    if "V5-C" in seen:
        add(
            "v5-forward-audit",
            "Frozen V5-C improvement -> authentic forward journal qualification",
            cost=1.0,
        )
        add(
            "v5-retune",
            "Retune previously inspected V5 development window",
            cost=5.0,
            qualified=False,
            reason="frozen forward protocol; repeated historical analysis is not new OOS",
        )
    if "V7-A1" in seen:
        add(
            "overseas-cutoff-audit",
            "Qualify overseas availability and mature nonoverlapping action labels",
            cost=2.0,
        )
    if "V7-B2" in seen:
        pit_cost = 0.5 if cost_scenario == "pit_cheaper" else 4.0
        add(
            "primary-pit-source",
            "Restore original publication/receipt PIT financial and membership sources",
            cost=pit_cost,
            impact=0.1,
        )
        add(
            "financial-alpha",
            "Paired financial alpha after PIT eligibility",
            cost=8.0,
            impact=0.95,
            prerequisite="primary-pit-source",
            qualified=False,
            reason="B1/B2 revealed no independently qualified historical vintages",
        )
    if "V8" in seen:
        add(
            "forward-capture-provenance",
            "Audit original strategy workflow capture and mature intervals",
            cost=0.5,
        )
    if "execution" in seen:
        add("stress-report-audit", "Saved stress report consistency", cost=0.5)
        add(
            "actual-opening-capacity",
            "Contemporaneous opening quotes and capacity calibration",
            cost=6.0,
            qualified=False,
            reason="ADV proxy does not establish real opening depth; quotes absent",
        )
    engine = ResearchEngine(InMemoryEventStore(), project, Path("unused-read-only-replay"))
    engine.initialize(
        ResearchGoal(
            statement="ETF research decision frontier",
            budgets=(Cost(unit="relative_estimate", amount=30.0),),
        ),
        tuple(nodes),
        tuple(proposals),
    )
    return engine
