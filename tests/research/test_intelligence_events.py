import pytest
from test_agent_discovery import make_agent

from epipilot.core.events import EventType
from epipilot.research.budget import BudgetManager
from epipilot.research.intelligence_models import ResearchPolicy, ResearchPolicyConfigured
from epipilot.research.models import Cost
from epipilot.state.replay import replay_project


def policy_for(engine):
    return ResearchPolicy(
        id="v1",
        basis_ref=str(engine.state.decisions[0].id),
        cost_scales=(Cost(unit="wall_seconds", amount=30.0),),
        scout_cost=(Cost(unit="wall_seconds", amount=0.5),),
    )


def test_policy_requires_canonical_user_authority_and_replays(tmp_path):
    engine, _, _ = make_agent(tmp_path)
    policy = policy_for(engine)
    with pytest.raises(ValueError, match="user"):
        engine.append(
            EventType.RESEARCH_POLICY_CONFIGURED,
            ResearchPolicyConfigured(policy=policy.model_copy(update={"basis_ref": "forged"})),
        )
    engine.append(EventType.RESEARCH_POLICY_CONFIGURED, ResearchPolicyConfigured(policy=policy))
    assert engine.state.intelligence.policy == policy
    assert engine.state == replay_project("agent", engine.store.load("agent"))
    with pytest.raises(ValueError, match="duplicate"):
        engine.append(EventType.RESEARCH_POLICY_CONFIGURED, ResearchPolicyConfigured(policy=policy))


def test_scout_overhead_reservation_and_completion_share_budget(tmp_path):
    from uuid import uuid4

    from epipilot.research.discovery import DiscoveryBatch
    from epipilot.research.scout import ScoutService, ScoutSubmission

    engine, root, _ = make_agent(tmp_path)
    engine.append(
        EventType.RESEARCH_POLICY_CONFIGURED, ResearchPolicyConfigured(policy=policy_for(engine))
    )
    service = ScoutService(engine, root)
    request = service.prepare("first")
    assert BudgetManager(engine.state.research).remaining["wall_seconds"] == pytest.approx(29.5)
    assert service.prepare("first") == request
    batch = DiscoveryBatch(
        id=uuid4(),
        based_on_version=request.based_on_version,
        basis_ref=request.authority_refs[0],
        summary="No new evidence",
        no_new_findings_reason="Read snapshot, no justified new question",
    )
    result = service.submit(
        ScoutSubmission(
            operation_id="first", batch=batch, actual_cost=(Cost(unit="wall_seconds", amount=0.2),)
        )
    )
    assert result.status.value == "accepted"
    assert BudgetManager(engine.state.research).spent["wall_seconds"] == pytest.approx(0.2)
    assert BudgetManager(engine.state.research).remaining["wall_seconds"] == pytest.approx(29.8)
    assert (
        service.submit(
            ScoutSubmission(
                operation_id="first",
                batch=batch,
                actual_cost=(Cost(unit="wall_seconds", amount=0.2),),
            )
        )
        == result
    )
    assert engine.state == replay_project("agent", engine.store.load("agent"))
