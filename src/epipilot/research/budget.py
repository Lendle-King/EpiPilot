"""Pure vector accounting and resource compatibility (no implicit free costs)."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from epipilot.core.contracts import ResourceClaim, ResourceKind
from epipilot.research.models import Cost, ExperimentProposal, ResearchState, amounts


@dataclass(frozen=True)
class BudgetManager:
    state: ResearchState

    @property
    def limits(self) -> dict[str, float]:
        base = amounts(self.state.goal.budgets)
        additions: dict[str, float] = {}
        for item in self.state.budget_additions:
            if item.unit not in base:
                raise ValueError("budget addition unit has no base budget")
            additions[item.unit] = additions.get(item.unit, 0.0) + item.amount
        return {unit: limit + additions.get(unit, 0.0) for unit, limit in base.items()}

    @property
    def spent(self) -> dict[str, float]:
        result = dict.fromkeys(self.limits, 0.0)
        for settlement in self.state.settlements:
            for unit, value in amounts(settlement.actual_cost).items():
                result[unit] = result.get(unit, 0.0) + value
        for overhead in self.state.overhead_settlements:
            for unit, value in amounts(overhead.actual_cost).items():
                result[unit] = result.get(unit, 0.0) + value
        return result

    @property
    def remaining(self) -> dict[str, float]:
        result = {k: v - self.spent[k] for k, v in self.limits.items()}
        for reservation in self.state.active:
            for unit, value in amounts(reservation.costs).items():
                result[unit] -= value
        settled = {s.operation_id for s in self.state.overhead_settlements}
        for overhead_reservation in self.state.overhead_reservations:
            if overhead_reservation.operation_id not in settled:
                for unit, value in amounts(overhead_reservation.costs).items():
                    result[unit] -= value
        return result

    def normalized(self, costs: tuple[Cost, ...]) -> float:
        if not costs or not any(c.amount > 0 for c in costs):
            raise ValueError("unknown cost or zero-only estimate")
        limits = self.limits
        if any(c.unit not in limits for c in costs):
            raise ValueError("cost unit has no budget")
        return sum(c.amount / limits[c.unit] for c in costs)

    def estimate(self, proposal: ExperimentProposal) -> tuple[Cost, ...]:
        """Median measured/estimated ratio for matching kind/fidelity; capped prudently."""
        result = []
        for item in proposal.estimated_cost:
            ratios = []
            for s in self.state.settlements:
                old = self.state.proposal(s.proposal_id)
                expected = amounts(old.estimated_cost).get(item.unit, 0.0)
                actual = amounts(s.actual_cost).get(item.unit)
                if (
                    old.kind == proposal.kind
                    and old.fidelity == proposal.fidelity
                    and expected > 0
                    and actual is not None
                    and actual > 0
                ):
                    ratios.append(actual / expected)
            ratios.sort()
            factor = min(4.0, max(0.5, ratios[len(ratios) // 2])) if ratios else 1.0
            result.append(Cost(unit=item.unit, amount=item.amount * factor))
        return tuple(result)

    def reservation(self, proposal: ExperimentProposal) -> tuple[Cost, ...]:
        costs = tuple(
            Cost(unit=c.unit, amount=c.amount * (1 + proposal.cost_uncertainty))
            for c in self.estimate(proposal)
        )
        self.normalized(costs)
        if any(c.amount > self.remaining[c.unit] + 1e-12 for c in costs):
            raise ValueError("budget insufficient (including cost uncertainty)")
        return costs


class ResourceScheduler:
    @staticmethod
    def available(
        requested: tuple[ResourceClaim, ...],
        active: tuple[tuple[ResourceClaim, ...], ...],
        capacities: tuple[ResourceClaim, ...],
    ) -> bool:
        if len({(c.kind, c.key) for c in requested}) != len(requested):
            return False
        if len({(c.kind, c.key) for c in capacities}) != len(capacities):
            return False
        if any(
            not isfinite(c.quantity) or c.quantity <= 0
            for group in (*active, requested, capacities)
            for c in group
        ):
            return False
        limits = {(c.kind, c.key): c.quantity for c in capacities}
        for claim in requested:
            same = [
                c for group in active for c in group if (c.kind, c.key) == (claim.kind, claim.key)
            ]
            if same and (claim.exclusive or any(c.exclusive for c in same)):
                return False
            limit = limits.get((claim.kind, claim.key))
            if limit is None and claim.kind in {
                ResourceKind.CPU,
                ResourceKind.GPU,
                ResourceKind.MEMORY,
                ResourceKind.API_BUDGET,
            }:
                return False
            if limit is not None and claim.quantity + sum(c.quantity for c in same) > limit:
                return False
        return True
