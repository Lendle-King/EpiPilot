from datetime import UTC, datetime
from uuid import uuid4

import pytest

from epipilot.core.events import EventType
from epipilot.epistemics.models import HypothesisStatus
from epipilot.research.engine import ResearchEngine
from epipilot.research.models import (
    Assessment,
    Cost,
    ExecutionStatus,
    ExperimentProposal,
    Observation,
    Outcome,
    ResearchGoal,
    ResearchNode,
    Scope,
)
from epipilot.runtime.event_store import InMemoryEventStore
from epipilot.runtime.sqlite_event_store import SqliteEventStore
from epipilot.state.replay import replay_project


class Runner:
    def __init__(self, execution=ExecutionStatus.SUCCEEDED):
        self.calls = 0
        self.execution = execution

    def run(self, proposal, output):
        self.calls += 1
        output.write_text("synthetic check", encoding="utf8")
        return Observation(
            proposal_id=proposal.id,
            execution=self.execution,
            samples=20,
            actual_cost=(Cost(unit="seconds", amount=2.0),),
            artifact=str(output),
            summary="synthetic",
            recorded_at=datetime.now(UTC),
        )


class Verifier:
    def __init__(self, outcome=Outcome.SUPPORT, verified=True):
        self.outcome, self.verified = outcome, verified

    def verify(self, proposal, observation):
        return Assessment(
            proposal_id=proposal.id,
            verified=self.verified,
            outcome=self.outcome,
            reliability=1.0,
            scope=Scope.INTEGRITY,
            reasons=("synthetic independent check",),
        )


def setup(store, tmp_path):
    engine = ResearchEngine(store, "project", tmp_path)
    hid = uuid4()
    p = ExperimentProposal(
        id="check",
        hypothesis_id=hid,
        title="check",
        input_revision="frozen",
        estimated_cost=(Cost(unit="seconds", amount=5.0),),
        definitive_negative=True,
    )
    engine.initialize(
        ResearchGoal(statement="diagnose", budgets=(Cost(unit="seconds", amount=20.0),)),
        (ResearchNode(hypothesis_id=hid, label="hypothesis", basis="user request"),),
        (p,),
    )
    return engine


def test_full_loop_replays_and_cannot_double_submit(tmp_path):
    store = SqliteEventStore(tmp_path / "events.sqlite3")
    engine = setup(store, tmp_path)
    runner = Runner()
    engine.run_next(runner, Verifier())
    assert engine.state.hypotheses[0].status is HypothesisStatus.SUPPORTED
    assert engine.budget()["spent"] == {"seconds": 2.0}
    assert engine.state == replay_project("project", store.load("project"))
    restarted = ResearchEngine(store, "project", tmp_path)
    assert restarted.run_next(runner, Verifier()) is None
    assert runner.calls == 1
    assert restarted.state.tasks[0].status.value == "passed"
    assert restarted.state.current_plan.tasks[0].status.value == "passed"


@pytest.mark.parametrize(
    "outcome,verified,execution,status",
    [
        (Outcome.INCONCLUSIVE, True, ExecutionStatus.SUCCEEDED, HypothesisStatus.INCONCLUSIVE),
        (Outcome.REFUTE, False, ExecutionStatus.SUCCEEDED, HypothesisStatus.PROPOSED),
        (Outcome.REFUTE, True, ExecutionStatus.FAILED, HypothesisStatus.PROPOSED),
        (Outcome.BLOCKED, True, ExecutionStatus.BLOCKED, HypothesisStatus.BLOCKED),
        (Outcome.REFUTE, True, ExecutionStatus.SUCCEEDED, HypothesisStatus.REFUTED),
    ],
)
def test_execution_and_science_are_distinct(tmp_path, outcome, verified, execution, status):
    engine = setup(InMemoryEventStore(), tmp_path)
    engine.run_next(Runner(execution), Verifier(outcome, verified))
    assert engine.state.hypotheses[0].status is status
    assert engine.budget()["spent"]["seconds"] == 2.0


def test_crash_before_observation_abstains_and_cancel_refunds_only_unspent(tmp_path):
    engine = setup(InMemoryEventStore(), tmp_path)
    engine.reserve_next()
    restarted = ResearchEngine(engine.store, "project", tmp_path)
    assert restarted.ranking().stop_reason == "in_flight_requires_resume"
    with pytest.raises(ValueError, match="unrecorded"):
        restarted.resume(Verifier())
    restarted.cancel("check", (Cost(unit="seconds", amount=1.0),), "operator measured elapsed work")
    assert restarted.budget()["remaining"]["seconds"] == 19.0
    with pytest.raises(ValueError):
        restarted.cancel("check", (Cost(unit="seconds", amount=1.0),), "duplicate")


def test_crash_after_observation_resumes_without_rerun(tmp_path):
    engine = setup(InMemoryEventStore(), tmp_path)
    p = engine.reserve_next()
    o = Runner().run(p, tmp_path / "output.json")
    engine.observe(o)
    restarted = ResearchEngine(engine.store, "project", tmp_path)
    restarted.resume(Verifier())
    assert restarted.budget()["spent"]["seconds"] == 2.0
    assert restarted.state.hypotheses[0].status is HypothesisStatus.SUPPORTED
    restarted.resume(Verifier())
    assert len(restarted.state.research.settlements) == 1


def test_raw_observation_does_not_change_hypothesis(tmp_path):
    engine = setup(InMemoryEventStore(), tmp_path)
    p = engine.reserve_next()
    engine.observe(Runner().run(p, tmp_path / "raw"))
    assert engine.state.hypotheses[0].status is HypothesisStatus.PROPOSED
    assert not engine.state.evidence
    assert EventType.EXPERIMENT_OBSERVED in {e.type for e in engine.store.load("project")}


def test_actual_overrun_is_recorded_and_future_work_stops(tmp_path):

    engine = setup(InMemoryEventStore(), tmp_path)
    p = engine.reserve_next()
    o = Runner().run(p, tmp_path / "raw")
    engine.observe(o.model_copy(update={"actual_cost": (Cost(unit="seconds", amount=30.0),)}))
    engine.resume(Verifier())
    assert engine.budget()["spent"]["seconds"] == 30.0
    assert engine.budget()["remaining"]["seconds"] == -10.0


def test_insufficient_samples_never_refute(tmp_path):
    engine = setup(InMemoryEventStore(), tmp_path)
    p = engine.reserve_next()
    o = Runner().run(p, tmp_path / "raw")
    engine.observe(o.model_copy(update={"samples": 0}))
    engine.resume(Verifier(Outcome.REFUTE))
    assert engine.state.hypotheses[0].status is HypothesisStatus.INCONCLUSIVE


def test_development_evidence_cannot_promote_as_independent(tmp_path):
    engine = setup(InMemoryEventStore(), tmp_path)
    p = engine.reserve_next()
    engine.observe(Runner().run(p, tmp_path / "raw"))

    class Promoter(Verifier):
        def verify(self, proposal, observation):
            return (
                super()
                .verify(proposal, observation)
                .model_copy(update={"qualified_for_promotion": True})
            )

    with pytest.raises(ValueError):
        engine.resume(Promoter())
    assert engine.state.hypotheses[0].status is HypothesisStatus.PROPOSED


def test_unchanged_new_id_cannot_retry(tmp_path):
    engine = setup(InMemoryEventStore(), tmp_path)
    p = engine.state.research.proposals[0]
    with pytest.raises(ValueError):
        engine.propose(p.model_copy(update={"id": "same-again"}), str(engine.state.decisions[0].id))


def test_stale_worker_cannot_reserve_second_execution(tmp_path):
    from epipilot.runtime.event_store import EventVersionConflict

    engine = setup(InMemoryEventStore(), tmp_path)
    stale = ResearchEngine(engine.store, "project", tmp_path)
    engine.reserve_next()
    with pytest.raises(EventVersionConflict):
        stale.reserve_next()


def test_verification_wall_time_is_accounted(tmp_path):
    engine = ResearchEngine(InMemoryEventStore(), "wall", tmp_path)
    hid = uuid4()
    p = ExperimentProposal(
        id="check",
        hypothesis_id=hid,
        title="wall",
        input_revision="x",
        estimated_cost=(Cost(unit="wall_seconds", amount=5.0),),
    )
    engine.initialize(
        ResearchGoal(statement="check", budgets=(Cost(unit="wall_seconds", amount=20.0),)),
        (ResearchNode(hypothesis_id=hid, label="check", basis="user"),),
        (p,),
    )
    engine.reserve_next()
    o = Runner().run(p, tmp_path / "raw")
    engine.observe(o.model_copy(update={"actual_cost": (Cost(unit="wall_seconds", amount=2.0),)}))
    engine.resume(Verifier())
    assert engine.budget()["spent"]["wall_seconds"] > 2.0
    assert engine.state.research.reviews[0].verification_cost[0].amount > 0


def test_unreliable_low_fidelity_cannot_unlock_successor(tmp_path):
    from epipilot.research.models import Fidelity

    engine = setup(InMemoryEventStore(), tmp_path)
    first = engine.state.research.proposals[0]
    # Identical mechanism, genuinely higher resource/fidelity stage with explicit prerequisite.
    full = first.model_copy(
        update={"id": "full", "fidelity": Fidelity.F3, "predecessor_ids": (first.id,)}
    )
    engine.propose(full, str(engine.state.decisions[0].id))
    engine.reserve_next()
    engine.observe(Runner().run(first, tmp_path / "raw").model_copy(update={"samples": 0}))
    engine.resume(Verifier())
    assert not engine.ranking().ranked
    assert engine.state.hypotheses[0].status is HypothesisStatus.INCONCLUSIVE


def test_replanning_preserves_existing_task_dependencies(tmp_path):
    from epipilot.core.models import TaskStatus
    from epipilot.events.payloads import (
        PlanBasisPayload,
        PlanDependencyPayload,
        PlanVersionCreatedPayload,
        TaskCreatedPayload,
        TaskStatusChangedPayload,
    )
    from epipilot.planning.graph import PlanBasisKind

    engine = setup(InMemoryEventStore(), tmp_path)
    old = engine.state.tasks[0].id
    new = uuid4()
    engine.append(
        EventType.TASK_CREATED, TaskCreatedPayload(task_id=new, objective="existing dependent")
    )
    engine.append(
        EventType.TASK_STATUS_CHANGED,
        TaskStatusChangedPayload(task_id=new, status=TaskStatus.READY),
    )
    basis = str(engine.state.decisions[0].id)
    engine.append(
        EventType.PLAN_VERSION_CREATED,
        PlanVersionCreatedPayload(
            version=2,
            task_ids=(old, new),
            dependencies=(PlanDependencyPayload(predecessor=old, successor=new),),
            basis=(PlanBasisPayload(kind=PlanBasisKind.DECISION, reference_id=basis),),
        ),
    )
    p = engine.state.research.proposals[0]
    engine.propose(p.model_copy(update={"id": "different", "input_revision": "new input"}), basis)
    assert len(engine.state.current_plan.dependencies) == 1


def test_new_hypothesis_can_extend_tree_without_rewriting_history(tmp_path):
    engine = setup(InMemoryEventStore(), tmp_path)
    before = engine.store.load("project")
    node = ResearchNode(
        hypothesis_id=uuid4(),
        label="new falsifiable route",
        basis=str(engine.state.decisions[0].id),
    )
    engine.add_hypothesis(node, ("new result predicted",), ("counterexample",))
    assert len(engine.state.hypotheses) == 2
    assert len(engine.state.research.nodes) == 2
    assert engine.store.load("project")[: len(before)] == before


@pytest.mark.parametrize("boundary", range(1, 8))
def test_every_finish_append_boundary_is_restartable(tmp_path, monkeypatch, boundary):
    engine = setup(InMemoryEventStore(), tmp_path)
    p = engine.reserve_next()
    engine.observe(Runner().run(p, tmp_path / "raw"))
    original = engine.append
    count = 0

    def fail_one(kind, payload):
        nonlocal count
        count += 1
        if count == boundary:
            raise RuntimeError("injected process interruption")
        original(kind, payload)

    monkeypatch.setattr(engine, "append", fail_one)
    with pytest.raises(RuntimeError):
        engine.resume(Verifier())
    restarted = ResearchEngine(engine.store, "project", tmp_path)
    restarted.resume(Verifier())
    assert restarted.budget()["spent"]["seconds"] == 2.0
    assert len(restarted.state.research.settlements) == 1
    assert len(restarted.state.research.reviews) == 1
    assert len(restarted.state.evidence) == 1


def test_observation_cannot_predate_reservation(tmp_path):
    from datetime import timedelta

    engine = setup(InMemoryEventStore(), tmp_path)
    p = engine.reserve_next()
    o = Runner().run(p, tmp_path / "raw")
    with pytest.raises(ValueError):
        engine.observe(o.model_copy(update={"recorded_at": datetime.now(UTC) - timedelta(days=1)}))


def test_fidelity_predecessor_support_must_still_be_current(tmp_path):
    from epipilot.research.models import Fidelity, Strategy

    engine = setup(InMemoryEventStore(), tmp_path)
    p = engine.state.research.proposals[0]
    basis = str(engine.state.decisions[0].id)
    engine.propose(p.model_copy(update={"id": "refute", "input_revision": "counterexample"}), basis)
    node = ResearchNode(hypothesis_id=uuid4(), label="new child", basis=basis)
    engine.add_hypothesis(node, ("prediction",), ("counterexample",))
    engine.propose(
        p.model_copy(
            update={
                "id": "child",
                "hypothesis_id": node.hypothesis_id,
                "fidelity": Fidelity.F3,
                "predecessor_ids": (p.id,),
            }
        ),
        basis,
    )
    engine.run_next(Runner(), Verifier(), Strategy.FIFO)
    engine.run_next(Runner(), Verifier(Outcome.REFUTE), Strategy.FIFO)
    assert engine.state.hypotheses[0].status is HypothesisStatus.REFUTED
    assert not engine.ranking().ranked
