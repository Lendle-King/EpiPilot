"""Deterministic context compilation from canonical project-state projections."""

from __future__ import annotations

from dataclasses import dataclass

from epipilot.memory.models import MemoryKind


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One candidate projection into an executor context."""

    item_id: str
    kind: MemoryKind
    content: str
    token_cost: int
    mandatory: bool = False
    relevance: float = 1.0
    authority: float = 1.0
    confidence: float = 1.0
    freshness: float = 1.0
    scope_match: float = 1.0

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("context item id must not be empty")
        if not self.content.strip():
            raise ValueError("context item content must not be empty")
        if self.token_cost <= 0:
            raise ValueError("context item token cost must be positive")
        for name, value in (
            ("relevance", self.relevance),
            ("authority", self.authority),
            ("confidence", self.confidence),
            ("freshness", self.freshness),
            ("scope_match", self.scope_match),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")

    @property
    def score(self) -> float:
        """Return a deterministic relevance/authority/confidence score."""
        return self.relevance * self.authority * self.confidence * self.freshness * self.scope_match


@dataclass(frozen=True, slots=True)
class CompiledContext:
    """Immutable executor-context projection and accounting metadata."""

    items: tuple[ContextItem, ...]
    token_cost: int
    token_budget: int

    @property
    def text(self) -> str:
        """Render selected items in deterministic section order."""
        return "\n\n".join(
            f"[{item.kind.value}:{item.item_id}]\n{item.content}" for item in self.items
        )


class ContextBudgetExceeded(ValueError):
    """Raised when mandatory project context alone exceeds the allowed budget."""


def compile_context(candidates: tuple[ContextItem, ...], *, token_budget: int) -> CompiledContext:
    """Compile a bounded context without ever silently dropping mandatory items.

    Non-mandatory items are ranked by information value per token. Ties are resolved by
    ``item_id`` so the same canonical state always produces the same projection.
    """
    if token_budget <= 0:
        raise ValueError("context token budget must be positive")

    item_ids = [item.item_id for item in candidates]
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("context item ids must be unique")

    mandatory = sorted(
        (item for item in candidates if item.mandatory),
        key=lambda item: item.item_id,
    )
    mandatory_cost = sum(item.token_cost for item in mandatory)
    if mandatory_cost > token_budget:
        raise ContextBudgetExceeded(
            "mandatory context exceeds token budget; refusing to drop authoritative state"
        )

    selected = list(mandatory)
    remaining_budget = token_budget - mandatory_cost

    optional = [item for item in candidates if not item.mandatory]
    optional.sort(key=lambda item: (-(item.score / item.token_cost), -item.score, item.item_id))

    for item in optional:
        if item.token_cost <= remaining_budget:
            selected.append(item)
            remaining_budget -= item.token_cost

    return CompiledContext(
        items=tuple(selected),
        token_cost=token_budget - remaining_budget,
        token_budget=token_budget,
    )
