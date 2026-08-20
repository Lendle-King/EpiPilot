"""Fail-closed retry policy for repeated executor failures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RetryAction(StrEnum):
    """Supervisor action after a failed task attempt."""

    RETRY = "retry"
    REQUIRE_STRATEGY_CHANGE = "require_strategy_change"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class FailureAttempt:
    """Auditable summary of one failed execution attempt."""

    failure_signature: str
    strategy_id: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.failure_signature.strip():
            raise ValueError("failure signature must not be empty")
        if not self.strategy_id.strip():
            raise ValueError("strategy id must not be empty")
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("failure evidence references must not be empty")


@dataclass(frozen=True, slots=True)
class RetryProposal:
    """Proposed next attempt and the information that justifies retrying."""

    strategy_id: str
    new_evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("retry strategy id must not be empty")
        if any(not ref.strip() for ref in self.new_evidence_refs):
            raise ValueError("retry evidence references must not be empty")


def decide_retry(
    history: tuple[FailureAttempt, ...],
    proposal: RetryProposal,
    *,
    max_same_signature_attempts: int = 2,
) -> RetryAction:
    """Decide whether another attempt is justified.

    An unchanged strategy may be retried only when there is genuinely new evidence.
    Repeated identical failure signatures beyond the configured cap are escalated rather
    than consumed by blind retry loops.
    """
    if max_same_signature_attempts < 1:
        raise ValueError("same-signature attempt cap must be positive")
    if not history:
        return RetryAction.RETRY

    latest = history[-1]
    same_signature = tuple(
        attempt for attempt in history if attempt.failure_signature == latest.failure_signature
    )
    if len(same_signature) >= max_same_signature_attempts:
        return RetryAction.ESCALATE

    changed_strategy = proposal.strategy_id != latest.strategy_id
    previous_evidence = {ref for attempt in history for ref in attempt.evidence_refs}
    has_new_evidence = any(ref not in previous_evidence for ref in proposal.new_evidence_refs)

    if changed_strategy or has_new_evidence:
        return RetryAction.RETRY
    return RetryAction.REQUIRE_STRATEGY_CHANGE
