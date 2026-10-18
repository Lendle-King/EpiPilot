"""Paired seeded synthetic worlds; latent outcomes never enter policy inputs."""

from __future__ import annotations

import json
import math
import random
import statistics
import tempfile
from dataclasses import dataclass
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
    Observation,
    Outcome,
    ResearchGoal,
    ResearchNode,
    Scope,
    Strategy,
)
from epipilot.runtime.event_store import InMemoryEventStore


@dataclass(frozen=True)
class LatentResult:
    proposal_id: str
    utility: float
    observed: float
    outcome: Outcome
    cost: float


@dataclass(frozen=True)
class World:
    goal: ResearchGoal
    nodes: tuple[ResearchNode, ...]
    proposals: tuple[ExperimentProposal, ...]
    latent: tuple[LatentResult, ...]
    scenario: str
    seed: int


def make_world(seed: int, scenario: str) -> World:
    if scenario not in {"cheap_gate", "misleading", "delayed", "flat"}:
        raise ValueError("unknown scenario")
    rng = random.Random(seed)
    project = f"synthetic-{scenario}-{seed}"
    nodes, proposals, latent = [], [], []
    budget = 24.0

    def add(
        name: str,
        cost: float,
        utility: float,
        *,
        info: float = 0.6,
        contribution: float = 0.5,
        gate: str | None = None,
        outcome: Outcome = Outcome.SUPPORT,
        fidelity: Fidelity = Fidelity.F1,
        observed: float | None = None,
    ) -> None:
        hid = stable_id(project, name)
        nodes.append(
            ResearchNode(
                hypothesis_id=hid,
                label=name,
                basis="seeded synthetic generator",
                and_ids=(stable_id(project, gate),) if gate else (),
                contribution=contribution,
                uncertainty=0.8,
            )
        )
        proposals.append(
            ExperimentProposal(
                id=name,
                hypothesis_id=hid,
                title=name,
                input_revision=str(seed),
                kind=ExperimentKind.SIMULATION,
                fidelity=fidelity,
                scope=Scope.SYNTHETIC,
                estimated_cost=(Cost(unit="tokens", amount=cost),),
                cost_uncertainty=0.4,
                information=info,
                contribution=contribution,
                uncertainty=0.8,
                risk=0.05,
                definitive_negative=True,
                minimum_samples=10,
                estimate_source="synthetic public prior; hidden utility not supplied",
            )
        )
        actual = cost * rng.uniform(0.75, 1.25)
        latent.append(
            LatentResult(name, utility, utility if observed is None else observed, outcome, actual)
        )

    if scenario == "cheap_gate":
        # Valuable shared qualification has tiny direct impact, hence the old product
        # heuristic can starve it despite large downstream decision value.
        for i in range(10):
            add(f"distractor-{i}", 3.0, rng.uniform(0.0, 0.25), contribution=0.6)
        add("qualification", 1.0, 0.0, contribution=0.01, info=0.95)
        for i in range(3):
            add(
                f"valuable-{i}",
                7.0,
                rng.uniform(0.86, 1.0),
                contribution=0.9,
                gate="qualification",
                fidelity=Fidelity.F3,
            )
        # A negative qualification saves a large inaccessible subtree.
        add("bad-source", 1.0, 0.0, contribution=0.02, info=0.95, outcome=Outcome.REFUTE)
        add("invalid-training", 18.0, 0.0, gate="bad-source", contribution=0.8)
    elif scenario == "flat":
        # Miscalibrated information priors: FIFO already places the best action first.
        add("already-good", 2.0, 0.95, info=0.05, contribution=0.25, fidelity=Fidelity.F3)
        for i in range(15):
            add(
                f"unhelpful-{i}",
                2.0,
                0.0,
                info=0.95,
                contribution=0.65,
                outcome=Outcome.INCONCLUSIVE,
            )
    elif scenario == "misleading":
        for i in range(8):
            add(
                f"cheap-signal-{i}",
                1.0,
                0.0,
                info=0.95,
                contribution=0.5,
                observed=rng.uniform(0.8, 1.0),
            )
            add(
                f"full-{i}",
                7.0,
                rng.uniform(0.0, 0.4),
                gate=f"cheap-signal-{i}",
                fidelity=Fidelity.F3,
                contribution=0.7,
            )
        add("independent", 9.0, 0.92, info=0.3, contribution=0.6, fidelity=Fidelity.F4)
    else:
        for i in range(10):
            add(
                f"early-{i}",
                1.0,
                0.0,
                info=0.8,
                contribution=0.45,
                outcome=Outcome.INCONCLUSIVE,
                observed=-0.2,
            )
        # Not dependent on an early proxy: no unjustified early stopping rule.
        add("late-bloomer", 12.0, 0.98, info=0.2, contribution=0.35, fidelity=Fidelity.F3)
        add("late-bloomer-2", 10.0, 0.91, info=0.25, contribution=0.4, fidelity=Fidelity.F3)
    return World(
        ResearchGoal(
            statement="synthetic utility >= .85",
            budgets=(Cost(unit="tokens", amount=budget),),
            required_scope=Scope.SYNTHETIC,
        ),
        tuple(nodes),
        tuple(proposals),
        tuple(latent),
        scenario,
        seed,
    )


@dataclass
class SyntheticAdapter:
    world: World

    def run(self, proposal: ExperimentProposal, output: Path) -> Observation:
        result = next(x for x in self.world.latent if x.proposal_id == proposal.id)
        # Runner reveals observed result only; true utility stays in evaluation code.
        output.write_text(
            json.dumps(
                {
                    "proposal_id": proposal.id,
                    "observed": result.observed,
                    "outcome": result.outcome.value,
                }
            ),
            encoding="utf8",
        )
        return Observation(
            proposal_id=proposal.id,
            execution=ExecutionStatus.SUCCEEDED,
            metrics=(Metric(name="observed", value=result.observed),),
            samples=20,
            actual_cost=(Cost(unit="tokens", amount=result.cost),),
            artifact=str(output),
            summary="simulated observation; not a real research result",
            recorded_at=datetime.now(UTC),
        )

    def verify(self, proposal: ExperimentProposal, observation: Observation) -> Assessment:
        data = json.loads(Path(observation.artifact).read_text(encoding="utf8"))
        result = next(x for x in self.world.latent if x.proposal_id == proposal.id)
        verified = data == {
            "proposal_id": proposal.id,
            "observed": result.observed,
            "outcome": result.outcome.value,
        } and observation.metrics == (Metric(name="observed", value=result.observed),)
        return Assessment(
            proposal_id=proposal.id,
            verified=verified,
            outcome=result.outcome,
            reliability=1.0,
            scope=Scope.SYNTHETIC,
            reasons=("recomputed seeded simulated observation",),
        )


def run_world(
    world: World, strategy: Strategy, output: Path
) -> dict[str, float | str | int | None]:
    engine = ResearchEngine(InMemoryEventStore(), f"bench-{world.scenario}-{world.seed}", output)
    engine.initialize(world.goal, world.nodes, world.proposals)
    adapter = SyntheticAdapter(world)
    truth = {x.proposal_id: x for x in world.latent}
    best_true = max(x.utility for x in world.latent)
    best, spent, regret, wasted, overhead, target = 0.0, 0.0, 0.0, 0.0, 0.0, None
    selected: list[str] = []
    start = perf_counter()
    while True:
        tick = perf_counter()
        ranking = engine.ranking(strategy)
        overhead += perf_counter() - tick
        if not ranking.ranked:
            break
        identifier = ranking.ranked[0].proposal_id
        engine.run_next(adapter, adapter, strategy)
        selected.append(identifier)
        result = truth[identifier]
        spent += result.cost
        best = max(best, result.utility)
        regret += (best_true - best) * result.cost
        # Waste = spending on a direction whose invalid prerequisite is already revealed.
        p = world.proposals[[x.id for x in world.proposals].index(identifier)]
        node = next(x for x in world.nodes if x.hypothesis_id == p.hypothesis_id)
        if any(
            h.status.value == "refuted" and h.id in node.and_ids for h in engine.state.hypotheses
        ):
            wasted += result.cost
        if best >= 0.85 and target is None:
            target = spent
    return {
        "scenario": world.scenario,
        "strategy": strategy.value,
        "seed": world.seed,
        "best_utility": best,
        "identified_best": float(best >= best_true - 1e-12),
        "spent": spent,
        "budget": world.goal.budgets[0].amount,
        "target_cost": target,
        "target_reached": float(target is not None),
        "regret": regret,
        "wasted": wasted,
        "missed_late": float(world.scenario == "delayed" and "late-bloomer" not in selected),
        "runtime_seconds": perf_counter() - start,
        "scheduler_seconds": overhead,
    }


def benchmark(seeds: int = 50, start_seed: int = 0) -> dict[str, Any]:
    """JSON report boundary; paired seeds are the independent sampling units."""
    if seeds < 2:
        raise ValueError("at least two seeds required for uncertainty reporting")
    runs: list[dict[str, float | str | int | None]] = []
    with tempfile.TemporaryDirectory(prefix="epipilot-carts-bench-") as tmp:
        for scenario in ("cheap_gate", "misleading", "delayed", "flat"):
            for seed in range(start_seed, start_seed + seeds):
                world = make_world(seed, scenario)
                for strategy in Strategy:
                    out = Path(tmp) / f"{scenario}-{seed}-{strategy.value}"
                    runs.append(run_world(world, strategy, out))
    summary: list[dict[str, float | str | int]] = []
    metrics = (
        "best_utility",
        "identified_best",
        "spent",
        "target_reached",
        "regret",
        "wasted",
        "missed_late",
        "runtime_seconds",
        "scheduler_seconds",
    )
    for scenario in ("cheap_gate", "misleading", "delayed", "flat"):
        for strategy in Strategy:
            group = [
                r for r in runs if r["scenario"] == scenario and r["strategy"] == strategy.value
            ]
            row: dict[str, float | str | int] = {
                "scenario": scenario,
                "strategy": strategy.value,
                "runs": seeds,
                "budget": 24.0,
            }
            for metric in metrics:
                values = [float(str(r[metric])) for r in group]
                mean = statistics.mean(values)
                half = 1.96 * statistics.stdev(values) / math.sqrt(seeds)
                row.update(
                    {
                        metric + "_mean": mean,
                        metric + "_ci95_low": mean - half,
                        metric + "_ci95_high": mean + half,
                    }
                )
            costs = [float(str(r["target_cost"])) for r in group if r["target_cost"] is not None]
            row["target_cost_conditional_mean"] = statistics.mean(costs) if costs else -1.0
            summary.append(row)
    paired = []
    for scenario in ("cheap_gate", "misleading", "delayed", "flat"):
        for baseline in (Strategy.FIFO, Strategy.EXISTING):
            delta = []
            for seed in range(start_seed, start_seed + seeds):
                rows = [r for r in runs if r["scenario"] == scenario and r["seed"] == seed]
                carts = next(r for r in rows if r["strategy"] == "carts")
                old = next(r for r in rows if r["strategy"] == baseline.value)
                delta.append(float(str(carts["best_utility"])) - float(str(old["best_utility"])))
            mean = statistics.mean(delta)
            half = 1.96 * statistics.stdev(delta) / math.sqrt(seeds)
            paired.append(
                {
                    "scenario": scenario,
                    "baseline": baseline.value,
                    "utility_difference": mean,
                    "ci95_low": mean - half,
                    "ci95_high": mean + half,
                }
            )
    return {
        "start_seed": start_seed,
        "paired_differences": paired,
        "protocol": (
            "same seed/latent world/budget/feasibility for all policies; 95% normal seed CIs; "
            "target cost conditional on success (-1 means none); no oracle input to policies; "
            "simulated costs, real measured scheduler runtime; "
            "waste uses already-refuted prerequisite only"
        ),
        "summary": summary,
        "runs": runs,
    }
