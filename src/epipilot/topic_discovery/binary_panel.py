"""Descriptive discovery from binary outcomes with explicit independent-root accounting."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from statistics import mean
from typing import Self

from pydantic import Field, StrictBool, model_validator

from epipilot.topic_discovery.models import DiscoveryRecord


class BinaryObservation(DiscoveryRecord):
    """One measured trial, not necessarily an independent statistical sample."""

    root_id: str = Field(min_length=1)
    condition: str = Field(min_length=1)
    repeat_id: str = Field(min_length=1)
    success: StrictBool
    local_predicate: StrictBool | None = None

    @model_validator(mode="after")
    def nonblank_identifiers(self) -> Self:
        if not all(value.strip() for value in (self.root_id, self.condition, self.repeat_id)):
            raise ValueError("observation identifiers must not be blank")
        return self


class RootCell(DiscoveryRecord):
    root_id: str
    condition: str
    trials: int
    successes: int
    rate: float


class LocalPredicateAudit(DiscoveryRecord):
    measured_trials: int
    unknown_trials: int
    positive_trials: int
    positive_roots: list[str]
    counterexample_trials: int
    counterexample_roots: list[str]


class BinaryPanelSummary(DiscoveryRecord):
    trials: int
    independent_roots: int
    cells: list[RootCell]
    root_balanced_rates: dict[str, float]
    local_predicate: LocalPredicateAudit
    qualification: str = (
        "Descriptive results for supplied root IDs, not a population estimate or causal proof."
    )


class RootContrast(DiscoveryRecord):
    root_id: str
    pairs: int
    gains: int
    reversals: int
    both_success: int
    both_failure: int
    net_gain: float


class PairedContrast(DiscoveryRecord):
    treatment: str
    control: str
    roots: list[RootContrast]
    unmatched_treatment: int
    unmatched_control: int
    root_balanced_net_gain: float | None
    qualification: str = (
        "Pairs match root and repeat ID only. Independence and exchangeability require audit."
    )


def _index(
    observations: Sequence[BinaryObservation],
) -> dict[tuple[str, str, str], BinaryObservation]:
    indexed = {}
    for row in observations:
        key = (row.root_id, row.condition, row.repeat_id)
        if key in indexed:
            raise ValueError(f"duplicate root/condition/repeat: {key}")
        indexed[key] = row
    if not indexed:
        raise ValueError("at least one observation is required")
    return indexed


def summarize_binary_panel(observations: Sequence[BinaryObservation]) -> BinaryPanelSummary:
    """Find observed counterexamples to local-predicate sufficiency and root-balanced rates."""
    indexed = _index(observations)
    groups: dict[tuple[str, str], list[BinaryObservation]] = defaultdict(list)
    for row in indexed.values():
        groups[(row.root_id, row.condition)].append(row)
    cells = []
    by_condition: dict[str, list[float]] = defaultdict(list)
    for (root_id, condition), rows in sorted(groups.items()):
        successes = sum(row.success for row in rows)
        rate = successes / len(rows)
        cells.append(
            RootCell(
                root_id=root_id,
                condition=condition,
                trials=len(rows),
                successes=successes,
                rate=rate,
            )
        )
        by_condition[condition].append(rate)

    measured = [row for row in indexed.values() if row.local_predicate is not None]
    positive = [row for row in measured if row.local_predicate is True]
    counter = [row for row in positive if not row.success]
    return BinaryPanelSummary(
        trials=len(indexed),
        independent_roots=len({row.root_id for row in indexed.values()}),
        cells=cells,
        root_balanced_rates={key: mean(rates) for key, rates in by_condition.items()},
        local_predicate=LocalPredicateAudit(
            measured_trials=len(measured),
            unknown_trials=len(indexed) - len(measured),
            positive_trials=len(positive),
            positive_roots=sorted({row.root_id for row in positive}),
            counterexample_trials=len(counter),
            counterexample_roots=sorted({row.root_id for row in counter}),
        ),
    )


def compare_paired_conditions(
    observations: Sequence[BinaryObservation],
    *,
    treatment: str,
    control: str,
) -> PairedContrast:
    """Compare matched trials, retaining incomplete-pair counts and equal root weights."""
    if treatment == control:
        raise ValueError("treatment and control must differ")
    indexed = _index(observations)
    t = {(r, s): row for (r, c, s), row in indexed.items() if c == treatment}
    c = {(r, s): row for (r, name, s), row in indexed.items() if name == control}
    if not t or not c:
        raise ValueError("both conditions must be observed")
    by_root: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for key in sorted(t.keys() & c.keys()):
        by_root[key[0]].append((t[key].success, c[key].success))
    roots = []
    for root_id, pairs in sorted(by_root.items()):
        gains = sum(a and not b for a, b in pairs)
        reversals = sum(not a and b for a, b in pairs)
        roots.append(
            RootContrast(
                root_id=root_id,
                pairs=len(pairs),
                gains=gains,
                reversals=reversals,
                both_success=sum(a and b for a, b in pairs),
                both_failure=sum(not a and not b for a, b in pairs),
                net_gain=(gains - reversals) / len(pairs),
            )
        )
    unmatched_treatment = len(t.keys() - c.keys())
    unmatched_control = len(c.keys() - t.keys())
    # Do not give an aggregate effect for an incomplete paired panel.
    aggregate = None
    if roots and not unmatched_treatment and not unmatched_control:
        aggregate = mean(root.net_gain for root in roots)
    return PairedContrast(
        treatment=treatment,
        control=control,
        roots=roots,
        unmatched_treatment=unmatched_treatment,
        unmatched_control=unmatched_control,
        root_balanced_net_gain=aggregate,
    )
