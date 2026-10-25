"""Conditional VoI is explicit and must not bypass prior experiment state."""

from __future__ import annotations

from pathlib import Path

from test_decision_value import make_child, make_estimate
from test_research_plugin import setup_store

from epipilot.research.cli import _next_action
from epipilot.research.models import (
    ContractStatus,
    ExperimentContract,
    MetricDirection,
    MetricRule,
    Proposal,
    ProposalKind,
    ResearchState,
)
from epipilot.research.store import ResearchStore


def frontier(tmp_path: Path, *, budget: float = 100) -> ResearchStore:
    store = setup_store(tmp_path, budget=budget)
    make_child(store)
    store.add(
        "proposal_added",
        Proposal(
            id="Q",
            title="Appealing immediate optimization",
            kind=ProposalKind.EXPLORE,
            rationale="High immediate score independent of the decision table",
            acceptance="Matched external evaluation",
            unknown_ids=["U"],
            claim_ids=["C"],
            information_gain=1,
            expected_improvement=1,
            unlock_value=1,
            novelty=1,
            estimated_cost=1,
            risk=0,
        ),
    )
    return store


def with_frozen_estimate(store: ResearchStore) -> ResearchState:
    estimate = make_estimate()
    store.append("decision_estimate_registered", estimate.model_dump(mode="json"))
    return store.load()[0]


def test_default_heuristic_preserved_conditional_selects_different_action(
    tmp_path: Path,
) -> None:
    state = with_frozen_estimate(frontier(tmp_path))
    baseline = _next_action(state)
    candidate = _next_action(state, policy="conditional")
    assert baseline["selection_policy"] == "heuristic"
    assert baseline["task"]["proposal"]["id"] == "Q"
    assert candidate["selection_policy"] == "conditional"
    assert candidate["task"]["proposal"]["id"] == "P"
    assert candidate["decision_score"]["net_value"] > 0
    assert "not observed causal" in candidate["qualification"]


def test_missing_forecast_does_not_fallback_silently(tmp_path: Path) -> None:
    state, _ = frontier(tmp_path).load()
    candidate = _next_action(state, policy="conditional")
    assert candidate["status"] == "decision_forecast_required"


def test_negative_net_value_produces_explicit_stop(tmp_path: Path) -> None:
    state = with_frozen_estimate(frontier(tmp_path, budget=2))
    result = _next_action(state, policy="conditional")
    assert result["status"] == "decision_value_stop"
    assert result["decision_score"]["net_value"] <= 0


def test_inflight_work_preempts_optional_policy(tmp_path: Path) -> None:
    state = with_frozen_estimate(frontier(tmp_path))
    state.contracts["PRE-1"] = ExperimentContract(
        id="PRE-1",
        proposal_id="P",
        git_revision="a" * 40,
        config_ref="config.json",
        dataset_ref="data.json",
        baseline_ref="baseline",
        controls=["same task seeds"],
        decision_rule="predeclared outcome",
        falsification_rule="result not reached",
        artifact_ref="results/metrics.json",
        metric_rules=[
            MetricRule(name="score", direction=MetricDirection.AT_LEAST, threshold=0.8)
        ],
        verifier_argv=["python", "-m", "json.tool", "{artifact}"],
        max_cost=1,
        status=ContractStatus.AWAITING_VERIFICATION,
    )
    result = _next_action(state, policy="conditional")
    assert result["status"] == "verify"
    assert result["contract"]["id"] == "PRE-1"
