import pytest
from test_agent_discovery import batch_for
from test_decision_value import estimate
from test_scout import ready

from epipilot.research.intelligence_models import CandidateDisposition, OpportunityCandidate
from epipilot.research.models import Cost
from epipilot.state.replay import replay_project


def submitted(tmp_path, zero_legacy=False, purpose=None):
    from epipilot.research.scout import ScoutSubmission

    engine, root, revision, scout = ready(tmp_path)
    scout.prepare("research-one")
    batch = batch_for(engine, root, revision)
    if zero_legacy:
        item = batch.proposals[0]
        item = item.model_copy(
            update={
                "proposal": item.proposal.model_copy(
                    update={"information": 0.0, "uncertainty": 0.0, "contribution": 0.0}
                )
            }
        )
        batch = batch.model_copy(update={"proposals": (item,)})
    if purpose is not None:
        item = batch.proposals[0]
        batch = batch.model_copy(
            update={
                "proposals": (
                    item.model_copy(
                        update={"contract": item.contract.model_copy(update={"purpose": purpose})}
                    ),
                )
            }
        )
    finding = batch.findings[0]
    candidate = OpportunityCandidate(
        id="candidate",
        topic_key=finding.topic_key,
        question=finding.unknown.question,
        alternatives=finding.alternatives,
        decision_effect="Select temporal coverage repair or abandon provider route",
        source_refs=(f"git:{revision}:README.md:2",),
        proposal_ids=("profile",),
        disposition=CandidateDisposition.ACCEPTED,
        reason="Test decision-relevant coverage",
    )
    scout.submit(
        ScoutSubmission(
            operation_id="research-one",
            batch=batch,
            candidates=(candidate,),
            estimates=(estimate().model_copy(update={"proposal_id": "profile"}),),
            actual_cost=(Cost(unit="wall_seconds", amount=0.1),),
        )
    )
    return engine, root


def test_full_cycle_freezes_prediction_and_reconciles_verified_result(tmp_path):
    from epipilot.research.agent_service import AgentService

    engine, root = submitted(tmp_path)
    service = AgentService(engine, root)
    before = engine.state.event_version
    packet = service.iterate()
    assert engine.state.event_version == before
    assert packet.proposal_id == "profile"
    service.run_next()
    assert len(engine.state.intelligence.forecasts) == 1
    assert len(engine.state.intelligence.outcomes) == 1
    outcome = engine.state.intelligence.outcomes[0]
    assert outcome.observed_gain is None
    assert outcome.actual_cost == engine.state.research.settlements[0].actual_cost
    assert engine.state == replay_project("agent", engine.store.load("agent"))
    version = engine.state.event_version
    service.verify()
    assert engine.state.event_version == version


def test_new_score_is_not_prefiltered_by_old_heuristic(tmp_path):
    from epipilot.research.agent_service import AgentService

    engine, root = submitted(tmp_path, zero_legacy=True)
    packet = AgentService(engine, root).iterate()
    assert packet.proposal_id == "profile"
    assert packet.ranking.ranked[0].score > 0


def test_cancellation_is_accounted_without_scientific_gain(tmp_path):
    from epipilot.research.agent_service import AgentService

    engine, root = submitted(tmp_path)
    AgentService(engine, root).start()
    engine.cancel("profile", (Cost(unit="wall_seconds", amount=0.15),), "Measured cancelled run")
    assert engine.state.intelligence.outcomes[0].outcome.value == "failure"
    assert engine.state.intelligence.outcomes[0].observed_gain is None


def test_forecast_tampering_and_reservation_without_forecast_are_rejected(tmp_path):
    from datetime import UTC, datetime

    import pytest

    from epipilot.core.events import EventType
    from epipilot.research.budget import BudgetManager
    from epipilot.research.decision_policy import make_forecast, registered_contracts
    from epipilot.research.events import ExperimentReserved
    from epipilot.research.intelligence_models import DecisionForecastRecorded
    from epipilot.research.models import Reservation

    engine, _ = submitted(tmp_path)
    when = datetime.now(UTC)
    forecast = make_forecast(
        engine.state,
        "profile",
        when,
        engine.state.intelligence.starts[0].request.git_revision,
        registered_contracts(engine.state),
    )
    bad = forecast.model_copy(
        update={"scores": (forecast.scores[0].model_copy(update={"score": 999.0}),)}
    )
    version = engine.state.event_version
    with pytest.raises(ValueError, match="forecast differs"):
        engine.append(EventType.DECISION_FORECAST_RECORDED, DecisionForecastRecorded(forecast=bad))
    assert engine.state.event_version == version
    reservation = ExperimentReserved(
        reservation=Reservation(
            proposal_id="profile",
            costs=BudgetManager(engine.state.research).reservation(
                engine.state.research.proposal("profile")
            ),
            score_explanation="forged",
            at=when,
        )
    )
    with pytest.raises(ValueError) as rejected:
        engine.append(EventType.EXPERIMENT_RESERVED, reservation)
    assert "forecast" in str(rejected.value.__cause__)
    assert engine.state.event_version == version


def test_forecast_is_atomic_if_reservation_rejected(tmp_path):
    from datetime import UTC, datetime

    import pytest

    from epipilot.core.events import EventType
    from epipilot.research.decision_policy import make_forecast, registered_contracts
    from epipilot.research.events import ExperimentReserved
    from epipilot.research.intelligence_models import DecisionForecastRecorded
    from epipilot.research.models import Reservation

    engine, _ = submitted(tmp_path)
    when = datetime.now(UTC)
    forecast = make_forecast(
        engine.state,
        "profile",
        when,
        engine.state.intelligence.starts[0].request.git_revision,
        registered_contracts(engine.state),
    )
    version = engine.state.event_version
    with pytest.raises(ValueError):
        engine.append_batch(
            (
                (EventType.DECISION_FORECAST_RECORDED, DecisionForecastRecorded(forecast=forecast)),
                (
                    EventType.EXPERIMENT_RESERVED,
                    ExperimentReserved(
                        reservation=Reservation(
                            proposal_id="profile",
                            costs=(Cost(unit="wall_seconds", amount=999.0),),
                            at=when,
                            score_explanation="wrong costs",
                        )
                    ),
                ),
            )
        )
    assert engine.store.version("agent") == version
    assert not engine.state.intelligence.forecasts


def test_all_new_events_roundtrip_in_durable_store(tmp_path):
    from epipilot.research.agent_service import AgentService
    from epipilot.runtime.sqlite_event_store import SqliteEventStore

    engine, root = submitted(tmp_path)
    AgentService(engine, root).run_next()
    store = SqliteEventStore(tmp_path / "copy.sqlite3")
    store.append_many(engine.store.load("agent"), expected_version=0)
    assert replay_project("agent", store.load("agent")) == engine.state


@pytest.mark.parametrize("child_cost,mutex", [(100.0, False), (1.0, True)])
def test_unaffordable_unlock_cannot_inflate_decision_value(tmp_path, child_cost, mutex):
    from datetime import UTC, datetime

    from test_agent_discovery import batch_for

    from epipilot.research.decision_policy import registered_contracts, score_candidate
    from epipilot.research.discovery import apply_discovery
    from epipilot.research.intelligence_models import DecisionOption

    engine, root = submitted(tmp_path)
    batch = batch_for(engine, root, engine.state.intelligence.starts[0].request.git_revision)
    f = batch.findings[0]
    parent = engine.state.research.proposal("profile")
    f = f.model_copy(
        update={
            "topic_key": "child",
            "question": f.question.model_copy(update={"topic_key": "child"}),
            "node": f.node.model_copy(
                update={
                    "and_ids": (parent.hypothesis_id,),
                    "mutex_ids": (parent.hypothesis_id,) if mutex else (),
                }
            ),
        }
    )
    item = batch.proposals[0]
    c = item.contract.model_copy(update={"proposal_id": "child", "boundary_date": "2022-01-01"})
    p = item.proposal.model_copy(
        update={
            "id": "child",
            "input_revision": c.fingerprint(),
            "estimated_cost": (Cost(unit="wall_seconds", amount=child_cost),),
        }
    )
    apply_discovery(
        engine,
        root,
        batch.model_copy(
            update={
                "findings": (f,),
                "proposals": (item.model_copy(update={"proposal": p, "contract": c}),),
            }
        ),
    )
    e = estimate(True).model_copy(
        update={
            "proposal_id": "profile",
            "options": (
                DecisionOption(key="child", baseline_utility=0.5, proposal_id="child"),
                DecisionOption(key="stop", baseline_utility=0.4),
            ),
        }
    )
    score = score_candidate(engine.state, e, datetime.now(UTC), registered_contracts(engine.state))
    assert score.value == 0
    assert not any(mask[0] for mask in score.enabled_after)


def test_standalone_forecast_cannot_persist_or_double_count_an_experiment(tmp_path):
    from datetime import UTC, datetime

    import pytest

    from epipilot.core.events import EventType
    from epipilot.research.decision_policy import make_forecast, registered_contracts
    from epipilot.research.intelligence_models import DecisionForecastRecorded

    engine, root = submitted(tmp_path)
    forecast = make_forecast(
        engine.state,
        "profile",
        datetime.now(UTC),
        engine.state.intelligence.starts[0].request.git_revision,
        registered_contracts(engine.state),
    )
    version = engine.state.event_version
    with pytest.raises(ValueError, match="atomic"):
        engine.append(
            EventType.DECISION_FORECAST_RECORDED, DecisionForecastRecorded(forecast=forecast)
        )
    assert engine.store.version("agent") == version
    from epipilot.research.agent_service import AgentService

    AgentService(engine, root).run_next()
    assert len(engine.state.intelligence.outcomes) == len(engine.state.research.settlements) == 1


def test_forged_forecast_revision_is_rejected(tmp_path):
    from datetime import UTC, datetime

    import pytest

    from epipilot.research.decision_policy import make_forecast, registered_contracts

    engine, _ = submitted(tmp_path)
    with pytest.raises(ValueError, match=r"revision|epoch"):
        make_forecast(
            engine.state, "profile", datetime.now(UTC), "0" * 40, registered_contracts(engine.state)
        )


def test_public_engine_resume_recovers_outcome_after_settlement_crash(tmp_path):
    from epipilot.core.events import EventType
    from epipilot.research.agent_service import AgentService
    from epipilot.research.engine import ResearchEngine
    from epipilot.research.measurement import MeasurementAdapter
    from epipilot.runtime.event_store import InMemoryEventStore

    engine, root = submitted(tmp_path)
    AgentService(engine, root).run_next()
    events = engine.store.load("agent")
    index = next(i for i, e in enumerate(events) if e.type is EventType.BUDGET_SETTLED)
    store = InMemoryEventStore()
    store.append_many(events[: index + 1], expected_version=0)
    recovered = ResearchEngine(store, "agent", engine.output)
    assert not recovered.state.intelligence.outcomes
    recovered.resume(MeasurementAdapter(recovered, root))
    assert len(recovered.state.intelligence.outcomes) == 1
    version = recovered.state.event_version
    recovered.resume(MeasurementAdapter(recovered, root))
    assert recovered.state.event_version == version


def test_new_policy_does_not_reuse_old_epoch_estimates(tmp_path):
    from epipilot.core.events import EventType
    from epipilot.research.agent_service import AgentService
    from epipilot.research.intelligence_models import ResearchPolicyConfigured

    engine, root = submitted(tmp_path)
    policy = engine.state.intelligence.policy.model_copy(update={"id": "new-policy"})
    engine.append(EventType.RESEARCH_POLICY_CONFIGURED, ResearchPolicyConfigured(policy=policy))
    assert not engine.state.intelligence.estimates
    assert not AgentService(engine, root).iterate().ranking.ranked


def test_optimize_requires_supported_baseline_with_new_score(tmp_path):
    from epipilot.research.agent_service import AgentService
    from epipilot.research.frontier import Purpose

    engine, root = submitted(tmp_path, purpose=Purpose.OPTIMIZE)
    packet = AgentService(engine, root).iterate()
    assert not packet.ranking.ranked
    assert any("baseline" in x.reason for x in packet.ranking.rejected)
