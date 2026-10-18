from datetime import UTC, datetime
from uuid import uuid4

import pytest

from epipilot.core.models import HypothesisId
from epipilot.epistemics.models import Hypothesis, HypothesisStatus
from epipilot.research.budget import BudgetManager, ResourceScheduler
from epipilot.research.models import (
    Cost,
    ExperimentProposal,
    ResearchGoal,
    ResearchNode,
    ResearchState,
)
from epipilot.research.policy import rank_candidates, validate_graph
from epipilot.state.project import ProjectState

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def fixture():
    hid = HypothesisId(uuid4())
    goal = ResearchGoal(statement="improve", budgets=(Cost(unit="seconds", amount=100.0),))
    node = ResearchNode(hypothesis_id=hid, label="root", basis="user request")
    p = ExperimentProposal(
        id="one",
        hypothesis_id=hid,
        title="check",
        input_revision="a",
        estimated_cost=(Cost(unit="seconds", amount=10.0),),
    )
    research = ResearchState(goal=goal, nodes=(node,), proposals=(p,))
    state = ProjectState(project_id="p", hypotheses=(Hypothesis(hid, "test"),), research=research)
    return state, p


def test_cost_is_budget_share_and_unknown_not_free():
    state, p = fixture()
    manager = BudgetManager(state.research)
    assert manager.normalized(p.estimated_cost) == pytest.approx(0.1)
    assert rank_candidates(state, NOW).ranked[0].proposal_id == "one"
    unknown = p.model_copy(update={"id": "unknown", "estimated_cost": ()})
    state = state.__class__(
        project_id="p",
        hypotheses=state.hypotheses,
        research=state.research.model_copy(update={"proposals": (unknown,)}),
    )
    assert "unknown cost" in rank_candidates(state, NOW).rejected[0].reason


def test_cycles_and_or_dependencies():
    state, _p = fixture()
    a = state.research.nodes[0]
    b = ResearchNode(hypothesis_id=uuid4(), label="b", basis="user", and_ids=(a.hypothesis_id,))
    with pytest.raises(ValueError, match="cycle"):
        validate_graph((a.model_copy(update={"and_ids": (b.hypothesis_id,)}), b))
    dependent = a.model_copy(update={"or_ids": (b.hypothesis_id,)})
    r = state.research.model_copy(
        update={"nodes": (dependent, b.model_copy(update={"and_ids": ()}))}
    )
    blocked = ProjectState(
        project_id="p",
        hypotheses=(*state.hypotheses, Hypothesis(HypothesisId(b.hypothesis_id), "b")),
        research=r,
    )
    assert not rank_candidates(blocked, NOW).ranked


def test_bad_data_future_inputs_and_overspend_are_rejected():
    state, p = fixture()
    for update, reason in [
        ({"data_qualified": False}, "data"),
        ({"available_at": datetime(2027, 1, 1, tzinfo=UTC)}, "future"),
        ({"estimated_cost": (Cost(unit="seconds", amount=200.0),)}, "budget"),
    ]:
        changed = p.model_copy(update=update)
        r = state.research.model_copy(update={"proposals": (changed,)})
        s = ProjectState(project_id="p", hypotheses=state.hypotheses, research=r)
        assert reason in rank_candidates(s, NOW).rejected[0].reason


def test_resource_mutex():
    from epipilot.core.contracts import ResourceClaim, ResourceKind

    lock = ResourceClaim(ResourceKind.WORKTREE, "repo", exclusive=True)
    assert not ResourceScheduler.available((lock,), ((lock,),), ())
    assert ResourceScheduler.available((lock,), (), ())


def test_inconclusive_prerequisite_does_not_unlock():
    state, p = fixture()
    root = state.research.nodes[0]
    other = HypothesisId(uuid4())
    node = ResearchNode(
        hypothesis_id=other, label="child", basis="user", and_ids=(root.hypothesis_id,)
    )
    child = p.model_copy(update={"id": "child", "hypothesis_id": other})
    hypotheses = (
        Hypothesis(
            root.hypothesis_id,
            "root",
            HypothesisStatus.INCONCLUSIVE,
            predictions=("x",),
            falsification_conditions=("y",),
        ),
        Hypothesis(other, "child"),
    )
    s = ProjectState(
        project_id="p",
        hypotheses=hypotheses,
        research=state.research.model_copy(update={"nodes": (root, node), "proposals": (child,)}),
    )
    assert not rank_candidates(s, NOW).ranked


def test_resource_without_gpu_capacity_abstains():
    from epipilot.core.contracts import ResourceClaim, ResourceKind

    claim = ResourceClaim(ResourceKind.GPU, "gpu0")
    assert not ResourceScheduler.available((claim,), (), ())


def test_no_decision_value_abstains():
    state, p = fixture()
    p = p.model_copy(update={"information": 0.0, "contribution": 0.0, "uncertainty": 0.0})
    s = ProjectState(
        project_id="p",
        hypotheses=state.hypotheses,
        research=state.research.model_copy(update={"proposals": (p,)}),
    )
    assert not rank_candidates(s, NOW).ranked


def test_carts_preserves_budget_for_verified_progression():
    from uuid import uuid4

    from epipilot.research.models import Fidelity

    state, p = fixture()
    root = state.research.nodes[0]
    hid = HypothesisId(uuid4())
    node = ResearchNode(
        hypothesis_id=hid, label="validated path", basis="user", and_ids=(root.hypothesis_id,)
    )
    full = p.model_copy(
        update={
            "id": "full",
            "hypothesis_id": hid,
            "fidelity": Fidelity.F3,
            "estimated_cost": (Cost(unit="seconds", amount=80.0),),
            "cost_uncertainty": 0.0,
        }
    )
    cheap = p.model_copy(
        update={"id": "cheap", "estimated_cost": (Cost(unit="seconds", amount=30.0),)}
    )
    hypothesis = Hypothesis(
        root.hypothesis_id,
        "root",
        HypothesisStatus.SUPPORTED,
        predictions=("x",),
        falsification_conditions=("y",),
        supporting_evidence=(uuid4(),),
    )
    r = state.research.model_copy(update={"nodes": (root, node), "proposals": (cheap, full)})
    s = ProjectState(project_id="p", hypotheses=(hypothesis, Hypothesis(hid, "new")), research=r)
    assert rank_candidates(s, NOW).ranked[0].proposal_id == "full"


def test_duplicate_resource_claims_cannot_evade_quota():
    from epipilot.core.contracts import ResourceClaim, ResourceKind

    cpu = ResourceClaim(ResourceKind.CPU, "cpu", quantity=1.0)
    assert not ResourceScheduler.available((cpu, cpu), (), (cpu,))


def test_requested_improvement_requires_measured_improvement():
    from epipilot.research.models import (
        Assessment,
        ExecutionStatus,
        Fidelity,
        Metric,
        MetricConstraint,
        Observation,
        Outcome,
        Review,
        Scope,
    )
    from epipilot.research.policy import goal_met

    state, p = fixture()
    goal = state.research.goal.model_copy(
        update={
            "minimum_improvement": 0.05,
            "constraints": (MetricConstraint(name="utility", minimum=0.8),),
            "minimum_samples": 1,
        }
    )
    p = p.model_copy(update={"fidelity": Fidelity.F4, "scope": Scope.INDEPENDENT})
    o = Observation(
        proposal_id=p.id,
        execution=ExecutionStatus.SUCCEEDED,
        samples=10,
        actual_cost=(Cost(unit="seconds", amount=1.0),),
        artifact="fixture",
        summary="x",
        recorded_at=NOW,
        metrics=(Metric(name="utility", value=0.9),),
    )
    review = Review(
        evidence_id=uuid4(),
        assessment=Assessment(
            proposal_id=p.id,
            verified=True,
            outcome=Outcome.SUPPORT,
            reliability=1.0,
            scope=Scope.INDEPENDENT,
            qualified_for_promotion=True,
            reasons=("test",),
        ),
    )
    r = state.research.model_copy(
        update={"goal": goal, "proposals": (p,), "observations": (o,), "reviews": (review,)}
    )
    s = ProjectState(project_id="p", hypotheses=state.hypotheses, research=r)
    assert not goal_met(s)


def test_refuted_ancestor_invalidates_transitive_support():
    state, p = fixture()
    aid = state.research.nodes[0].hypothesis_id
    bid, cid = HypothesisId(uuid4()), HypothesisId(uuid4())
    a = Hypothesis(
        aid,
        "a",
        HypothesisStatus.REFUTED,
        predictions=("x",),
        falsification_conditions=("y",),
        contradicting_evidence=(uuid4(),),
    )
    b = Hypothesis(
        bid,
        "b",
        HypothesisStatus.SUPPORTED,
        predictions=("x",),
        falsification_conditions=("y",),
        supporting_evidence=(uuid4(),),
    )
    nodes = (
        state.research.nodes[0],
        ResearchNode(hypothesis_id=bid, label="b", basis="e", and_ids=(aid,)),
        ResearchNode(hypothesis_id=cid, label="c", basis="e", and_ids=(bid,)),
    )
    r = state.research.model_copy(
        update={"nodes": nodes, "proposals": (p.model_copy(update={"hypothesis_id": cid}),)}
    )
    s = ProjectState(project_id="p", hypotheses=(a, b, Hypothesis(cid, "c")), research=r)
    assert not rank_candidates(s, NOW).ranked


def test_nan_resource_cannot_bypass_quota():
    from epipilot.core.contracts import ResourceClaim, ResourceKind

    cpu = ResourceClaim(ResourceKind.CPU, "cpu", quantity=float("nan"))
    limit = ResourceClaim(ResourceKind.CPU, "cpu", quantity=1.0)
    assert not ResourceScheduler.available((cpu,), (), (limit,))
