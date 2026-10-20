from datetime import UTC, datetime

from test_agent_discovery import batch_for, make_agent

from epipilot.epistemics.models import HypothesisStatus
from epipilot.research.frontier import Quality
from epipilot.research.models import Outcome
from epipilot.state.replay import replay_project


def discovered(tmp_path):
    from epipilot.research.discovery import apply_discovery

    engine, root, revision = make_agent(tmp_path)
    batch = batch_for(engine, root, revision)
    apply_discovery(engine, root, batch)
    return engine, root, batch


def test_registered_measurement_resolves_and_replays(tmp_path):
    from epipilot.research.agent_service import reconcile_questions
    from epipilot.research.measurement import MeasurementAdapter

    engine, root, batch = discovered(tmp_path)
    adapter = MeasurementAdapter(engine, root)
    observation = engine.run_next(adapter, adapter)
    assert observation.samples == 2
    assert observation.summary == "registered input-bound measurement"
    assert engine.state.research.reviews[-1].assessment.quality is Quality.MEASUREMENT
    reconcile_questions(engine)
    assert engine.state.research.frontier.closed(batch.findings[0].unknown.unknown_id)
    assert engine.state == replay_project("agent", engine.store.load("agent"))


def test_missing_artifact_and_tampered_metrics_cannot_verify(tmp_path):
    from epipilot.research.measurement import MeasurementAdapter
    from epipilot.research.models import Metric

    engine, root, _batch = discovered(tmp_path)
    adapter = MeasurementAdapter(engine, root)
    p = engine.reserve_next()
    o = adapter.run(p, tmp_path / "result.json")
    assert not adapter.verify(
        p, o.model_copy(update={"metrics": (Metric(name="rows", value=999.0),)})
    ).verified
    (tmp_path / "result.json").unlink()
    assert not adapter.verify(p, o).verified


def test_integrity_only_does_not_resolve_scientific_question(tmp_path):
    from epipilot.research.agent_service import reconcile_questions
    from epipilot.research.discovery import apply_discovery
    from epipilot.research.frontier import Rule
    from epipilot.research.measurement import MeasurementAdapter

    engine, root, revision = make_agent(tmp_path)
    batch = batch_for(engine, root, revision)
    item = batch.proposals[0]
    c = item.contract.model_copy(
        update={"adapter": "integrity", "rules": (Rule(metric="files", minimum=1.0),)}
    )
    item = item.model_copy(
        update={
            "contract": c,
            "proposal": item.proposal.model_copy(update={"input_revision": c.fingerprint()}),
        }
    )
    apply_discovery(engine, root, batch.model_copy(update={"proposals": (item,)}))
    adapter = MeasurementAdapter(engine, root)
    engine.run_next(adapter, adapter)
    reconcile_questions(engine)
    assert not engine.state.research.frontier.closed(batch.findings[0].unknown.unknown_id)
    assert engine.state.hypotheses[0].status is HypothesisStatus.INCONCLUSIVE


def test_inconclusive_and_infrastructure_checks_do_not_resolve(tmp_path):
    from epipilot.research.agent_service import reconcile_questions
    from epipilot.research.models import Assessment, Cost, ExecutionStatus, Observation, Scope

    engine, _root, batch = discovered(tmp_path)

    class Broken:
        def run(self, p, out):
            return Observation(
                proposal_id=p.id,
                execution=ExecutionStatus.FAILED,
                actual_cost=(Cost(unit="wall_seconds", amount=0.1),),
                artifact="missing",
                summary="synthetic failure",
                recorded_at=datetime.now(UTC),
            )

        def verify(self, p, o):
            return Assessment(
                proposal_id=p.id,
                verified=True,
                outcome=Outcome.SUPPORT,
                reliability=1.0,
                quality=Quality.MEASUREMENT,
                scope=Scope.INTEGRITY,
                reasons=("synthetic infrastructure check",),
            )

    engine.run_next(Broken(), Broken())
    reconcile_questions(engine)
    assert not engine.state.research.frontier.closed(batch.findings[0].unknown.unknown_id)


def test_counterevidence_reopens_without_reclosing_on_same_evidence(tmp_path):
    import hashlib
    from uuid import uuid4

    import pytest

    from epipilot.core.events import EventType
    from epipilot.research.agent_service import AgentService, reconcile_questions
    from epipilot.research.discovery import AgentProposal, DiscoveryBatch, apply_discovery
    from epipilot.research.frontier import InputFile, QuestionResolved, Resolution
    from epipilot.research.measurement import MeasurementAdapter

    engine, root, batch = discovered(tmp_path)
    AgentService(engine, root).run_next()
    (root / "data.csv").write_text(
        "date,scale_observed\n2020-01-01,0\n2021-01-01,1\n", encoding="utf8"
    )
    original = batch.proposals[0]
    contract = original.contract.model_copy(
        update={
            "proposal_id": "counter",
            "inputs": (
                InputFile(
                    path="data.csv",
                    sha256=hashlib.sha256((root / "data.csv").read_bytes()).hexdigest(),
                ),
            ),
        }
    )
    proposal = original.proposal.model_copy(
        update={"id": "counter", "input_revision": contract.fingerprint()}
    )
    apply_discovery(
        engine,
        root,
        DiscoveryBatch(
            id=uuid4(),
            based_on_version=engine.state.event_version,
            basis_ref=batch.basis_ref,
            summary="New data contradict temporal boundary",
            proposals=(AgentProposal(proposal=proposal, contract=contract),),
            no_new_findings_reason="Existing question; changed input tests counterevidence",
        ),
    )
    adapter = MeasurementAdapter(engine, root)
    # Canonical engine permits a verification experiment even when the discovery policy
    # abstains from spending on an already resolved exploration question.
    engine.run_next(adapter, adapter)
    reconcile_questions(engine)
    uid = batch.findings[0].unknown.unknown_id
    assert not engine.state.research.frontier.closed(uid)
    before = engine.state.event_version
    reconcile_questions(engine)
    assert engine.state.event_version == before
    from epipilot.research.frontier_reducer import validate_resolution

    with pytest.raises(ValueError):
        validate_resolution(
            engine.state,
            Resolution(
                unknown_id=uid,
                evidence_id=engine.state.research.reviews[0].evidence_id,
                resolved=True,
                rationale="old invalidated support",
            ),
        )
    from epipilot.research.frontier import Purpose

    restored = contract.model_copy(
        update={"proposal_id": "retest", "purpose": Purpose.VERIFY, "boundary_date": "2021-01-01"}
    )
    retest = proposal.model_copy(update={"id": "retest", "input_revision": restored.fingerprint()})
    apply_discovery(
        engine,
        root,
        DiscoveryBatch(
            id=uuid4(),
            based_on_version=engine.state.event_version,
            basis_ref=batch.basis_ref,
            summary="Changed preregistered boundary tests a revised scope",
            proposals=(AgentProposal(proposal=retest, contract=restored),),
            no_new_findings_reason="Recheck existing reopened question under new boundary",
        ),
    )
    service = AgentService(engine, root)
    assert service.iterate().proposal_id == "retest"
    service.run_next()
    assert engine.state.research.frontier.closed(uid)
    with pytest.raises(ValueError):
        engine.append(
            EventType.RESEARCH_QUESTION_RESOLVED,
            QuestionResolved(
                resolution=Resolution(
                    unknown_id=uid,
                    evidence_id=engine.state.research.reviews[-1].evidence_id,
                    resolved=True,
                    rationale="reuse same evidence",
                )
            ),
        )


def test_agent_cannot_downgrade_scientific_quality_floor(tmp_path):
    import pytest

    from epipilot.research.discovery import apply_discovery
    from epipilot.research.frontier import Rule

    engine, root, revision = make_agent(tmp_path)
    batch = batch_for(engine, root, revision)
    f = batch.findings[0]
    f = f.model_copy(
        update={"question": f.question.model_copy(update={"minimum_quality": Quality.INTEGRITY})}
    )
    item = batch.proposals[0]
    c = item.contract.model_copy(
        update={"adapter": "integrity", "rules": (Rule(metric="files", minimum=1.0),)}
    )
    item = item.model_copy(
        update={
            "contract": c,
            "proposal": item.proposal.model_copy(update={"input_revision": c.fingerprint()}),
        }
    )
    before = engine.state.event_version
    with pytest.raises(ValueError):
        apply_discovery(
            engine, root, batch.model_copy(update={"findings": (f,), "proposals": (item,)})
        )
    assert engine.state.event_version == before


def test_offline_measurement_cannot_self_label_independent_scope(tmp_path):
    from epipilot.research.agent_service import AgentService
    from epipilot.research.discovery import apply_discovery
    from epipilot.research.models import Scope

    engine, root, revision = make_agent(tmp_path)
    batch = batch_for(engine, root, revision)
    f = batch.findings[0]
    f = f.model_copy(
        update={"question": f.question.model_copy(update={"evidence_scope": "independent"})}
    )
    item = batch.proposals[0]
    item = item.model_copy(
        update={"proposal": item.proposal.model_copy(update={"scope": Scope.INDEPENDENT})}
    )
    apply_discovery(engine, root, batch.model_copy(update={"findings": (f,), "proposals": (item,)}))
    AgentService(engine, root).run_next()
    assert not engine.state.research.frontier.closed(f.unknown.unknown_id)
    assert engine.state.hypotheses[0].status is not HypothesisStatus.SUPPORTED


def test_verification_finish_crash_is_atomic_and_recoverable(tmp_path, monkeypatch):
    import pytest

    from epipilot.core.events import EventType
    from epipilot.research.agent_service import AgentService
    from epipilot.research.engine import ResearchEngine

    engine, root, _batch = discovered(tmp_path)
    store = engine.store
    original = store.append_many

    def crash(events, *, expected_version):
        if any(e.type is EventType.EXPERIMENT_REVIEWED for e in events):
            raise RuntimeError("injected durable batch failure")
        return original(events, expected_version=expected_version)

    monkeypatch.setattr(
        type(store),
        "append_many",
        lambda self, events, *, expected_version: crash(events, expected_version=expected_version),
    )
    with pytest.raises(RuntimeError, match="injected"):
        AgentService(engine, root).run_next()
    assert not engine.state.research.reviews
    assert not engine.state.verifications
    (root / "data.csv").write_text("date,scale_observed\n2020-01-01,0\n", encoding="utf8")
    monkeypatch.undo()
    restarted = ResearchEngine(store, "agent", engine.output)
    AgentService(restarted, root).verify()
    assert not restarted.state.research.active
    assert not restarted.state.research.reviews[-1].assessment.verified
    assert restarted.budget()["spent"]["wall_seconds"] > 0


def test_restart_finishes_pending_knowledge_reconciliation(tmp_path):
    from epipilot.research.agent_service import Action, AgentService
    from epipilot.research.engine import ResearchEngine
    from epipilot.research.measurement import MeasurementAdapter

    engine, root, batch = discovered(tmp_path)
    adapter = MeasurementAdapter(engine, root)
    engine.run_next(adapter, adapter)  # interruption after settlement, before reconciliation
    restarted = ResearchEngine(engine.store, "agent", engine.output)
    service = AgentService(restarted, root)
    assert service.iterate().action is Action.RESUME
    service.verify()
    assert restarted.state.research.frontier.closed(batch.findings[0].unknown.unknown_id)
    assert service.iterate().action is Action.DISCOVER


def test_etf_algorithm_computer_uses_bound_receipt_and_contract_metrics(tmp_path):
    import hashlib
    from datetime import UTC, datetime

    from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
    from epipilot.workspace.algorithm_execution import (
        ETFAlgorithmComputer,
        ExecutionMetric,
        ExecutionReceipt,
        FileDigest,
    )

    engine, root, _batch = discovered(tmp_path)
    input_path = root / "input.csv"
    input_path.write_text("synthetic input\n", encoding="utf-8")
    input_digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
    daily_path = root / "candidate_daily.csv"
    daily_path.write_text(
        "date,return,equity,turnover\n2020-01-02,0,100,0\n2020-01-03,0,100,0\n2020-01-06,0,100,0\n",
        encoding="utf-8",
    )
    daily_digest = hashlib.sha256(daily_path.read_bytes()).hexdigest()
    adapter_id = "quant_etf_dual_momentum_126trend_135vol_v1"
    calls = []

    class FakeExecutor:
        def execute(self, request):
            calls.append(request)
            receipt_path = root / "receipt.json"
            now = datetime.now(UTC)
            receipt = ExecutionReceipt(
                route_run_id=request.route_run_id,
                round_index=request.round_index,
                algorithm_id=request.algorithm_id,
                started_at_utc=now,
                finished_at_utc=now,
                scope="exploratory_public_history",
                initial_cash=100,
                input_files=[FileDigest(path="input.csv", sha256=input_digest)],
                artifacts=[FileDigest(path="candidate_daily.csv", sha256=daily_digest)],
                metrics=[
                    ExecutionMetric(
                        variant=variant,
                        cost_bps=5,
                        daily_path="candidate_daily.csv",
                        cagr=0,
                        max_drawdown=0,
                        annualized_turnover=0,
                    )
                    for variant in ("baseline", "candidate")
                ],
                protocol="synthetic fixed algorithm receipt for unit testing only",
            )
            receipt_path.write_text(receipt.model_dump_json(), encoding="utf-8")
            return receipt_path

    contract = MeasurementContract(
        proposal_id="synthetic-etf-proposal",
        purpose=Purpose.OPTIMIZE,
        adapter=adapter_id,
        git_revision="a" * 40,
        inputs=(InputFile(path="input.csv", sha256=input_digest),),
        rules=(Rule(metric="dual_momentum_126trend_135vol.candidate.5bp.cagr", minimum=0),),
    )
    computer = ETFAlgorithmComputer(
        engine=engine,
        repository=root,
        executor=FakeExecutor(),
        adapter_id=adapter_id,
        algorithm_id="dual_momentum_126trend_135vol",
    )

    measured = computer.measure(root, contract)

    assert len(calls) == 1
    assert calls[0].round_index == len(engine.state.research.reviews) + 1
    assert calls[0].algorithm_id == "dual_momentum_126trend_135vol"
    assert measured.samples == 3
    metrics = {metric.name: metric.value for metric in measured.metrics}
    assert metrics["dual_momentum_126trend_135vol.candidate.5bp.cagr"] == 0
    assert metrics["dual_momentum_126trend_135vol.candidate.5bp.mdd_abs"] == 0


def test_etf_algorithm_computer_rejects_mismatched_contract_adapter(tmp_path):
    import pytest

    from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
    from epipilot.workspace.algorithm_execution import ETFAlgorithmComputer

    engine, root, _batch = discovered(tmp_path)
    contract = MeasurementContract(
        proposal_id="wrong-algorithm",
        purpose=Purpose.OPTIMIZE,
        adapter="quant_etf_dual_momentum_126trend_135vol_v1",
        git_revision="a" * 40,
        inputs=(InputFile(path="data.csv", sha256="0" * 64),),
        rules=(Rule(metric="x", minimum=0),),
    )

    class WrongExecutor:
        def execute(self, _request):
            raise AssertionError("adapter mismatch must be rejected before execution")

    computer = ETFAlgorithmComputer(
        engine=engine,
        repository=root,
        executor=WrongExecutor(),
        adapter_id="quant_etf_dual_momentum_126trend_v1",
        algorithm_id="dual_momentum_126trend",
    )
    with pytest.raises(ValueError, match="adapter does not match"):
        computer.measure(root, contract)
