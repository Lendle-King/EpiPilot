from __future__ import annotations

import pytest

from epipilot.context.compiler import (
    ContextBudgetExceeded,
    ContextItem,
    ContextKind,
    compile_context,
)


def _item(
    item_id: str,
    *,
    token_cost: int,
    mandatory: bool = False,
    relevance: float = 1.0,
    authority: float = 1.0,
) -> ContextItem:
    return ContextItem(
        item_id=item_id,
        kind=ContextKind.NORMATIVE if mandatory else ContextKind.SEMANTIC,
        content=f"content for {item_id}",
        token_cost=token_cost,
        mandatory=mandatory,
        relevance=relevance,
        authority=authority,
    )


def test_mandatory_context_is_never_dropped_for_optional_items() -> None:
    mandatory = _item("constraint", token_cost=80, mandatory=True, relevance=0.1)
    optional = _item("similar-memory", token_cost=80, relevance=1.0)

    compiled = compile_context((optional, mandatory), token_budget=100)

    assert compiled.items == (mandatory,)
    assert compiled.token_cost == 80


def test_compiler_fails_closed_if_mandatory_context_exceeds_budget() -> None:
    mandatory = _item("constraint", token_cost=101, mandatory=True)

    with pytest.raises(ContextBudgetExceeded):
        compile_context((mandatory,), token_budget=100)


def test_optional_items_are_ranked_by_value_per_token() -> None:
    expensive = _item("expensive", token_cost=80, relevance=0.8)
    efficient = _item("efficient", token_cost=40, relevance=0.7)

    compiled = compile_context((expensive, efficient), token_budget=80)

    assert compiled.items == (efficient,)
