from uuid import uuid4

import pytest

from epipilot.core.events import EventType
from epipilot.events.codec import make_project_event
from epipilot.events.payloads import TaskCreatedPayload
from epipilot.runtime.event_store import DuplicateEvent, EventVersionConflict, InMemoryEventStore
from epipilot.runtime.sqlite_event_store import SqliteEventStore


@pytest.mark.parametrize("durable", [False, True])
def test_atomic_batch_rollback_and_concurrency(tmp_path, durable):
    store = SqliteEventStore(tmp_path / "state.sqlite3") if durable else InMemoryEventStore()
    event = make_project_event(
        EventType.TASK_CREATED, "p", TaskCreatedPayload(task_id=uuid4(), objective="synthetic")
    )
    with pytest.raises(DuplicateEvent):
        store.append_many((event, event), expected_version=0)
    assert store.version("p") == 0
    assert store.append_many((event,), expected_version=0) == 1
    with pytest.raises(EventVersionConflict):
        store.append_many(
            (
                make_project_event(
                    EventType.TASK_CREATED,
                    "p",
                    TaskCreatedPayload(task_id=uuid4(), objective="stale"),
                ),
            ),
            expected_version=0,
        )
    assert store.version("p") == 1


def make_agent(tmp_path):
    import subprocess

    from epipilot.research.discovery import enable_frontier
    from epipilot.research.engine import ResearchEngine
    from epipilot.research.models import Cost, ResearchGoal

    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text(
        "Synthetic research evidence\nMissing values may cluster by date.\n", encoding="utf8"
    )
    (root / "data.csv").write_text(
        "date,scale_observed\n2018-01-01,0\n2020-01-01,1\n", encoding="utf8"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    engine = ResearchEngine(InMemoryEventStore(), "agent", tmp_path / "artifacts")
    engine.initialize(
        ResearchGoal(
            statement="Understand missingness", budgets=(Cost(unit="wall_seconds", amount=30.0),)
        ),
        (),
        (),
    )
    enable_frontier(engine, root)
    return engine, root, revision


def batch_for(engine, root, revision):
    import hashlib

    from epipilot.epistemics.models import ResolutionMode, UnknownImpact
    from epipilot.events.payloads import HypothesisCreatedPayload, UnknownRegisteredPayload
    from epipilot.research.discovery import AgentProposal, DiscoveryBatch, Finding, SourceAnchor
    from epipilot.research.frontier import (
        Facet,
        InputFile,
        MeasurementContract,
        Purpose,
        Question,
        Rule,
    )
    from epipilot.research.models import Cost, ExperimentProposal, ResearchNode

    hid, uid = uuid4(), uuid4()
    basis = str(engine.state.decisions[0].id)
    contract = MeasurementContract(
        proposal_id="profile",
        purpose=Purpose.EXPLORE,
        adapter="missingness",
        git_revision=revision,
        inputs=(
            InputFile(
                path="data.csv", sha256=hashlib.sha256((root / "data.csv").read_bytes()).hexdigest()
            ),
        ),
        rules=(Rule(metric="missing_before_boundary", minimum=1.0),),
    )
    return DiscoveryBatch(
        id=uuid4(),
        based_on_version=engine.state.event_version,
        basis_ref=basis,
        summary="Distinguish old coverage boundary from recent random failures",
        sources=(
            SourceAnchor(
                revision=revision,
                path="README.md",
                line=2,
                quote="Missing values may cluster by date.",
            ),
        ),
        findings=(
            Finding(
                topic_key="missingness-date",
                facet=Facet.BOUNDARY,
                hypothesis=HypothesisCreatedPayload(
                    hypothesis_id=hid,
                    statement="Missing values concentrate before boundary",
                    predictions=("before-boundary count >=1",),
                    falsification_conditions=("zero missing rows before boundary",),
                ),
                node=ResearchNode(
                    hypothesis_id=hid,
                    label="Missing values concentrate before boundary",
                    basis=basis,
                ),
                unknown=UnknownRegisteredPayload(
                    unknown_id=uid,
                    question="Where do missing values cluster?",
                    impact=UnknownImpact.HIGH,
                    resolution_mode=ResolutionMode.EXPERIMENT,
                ),
                question=Question(
                    unknown_id=uid,
                    topic_key="missingness-date",
                    facet=Facet.BOUNDARY,
                    hypothesis_ids=(hid,),
                    evidence_scope="integrity",
                ),
                alternatives=(
                    "Historical provider coverage boundary",
                    "Recent acquisition failures",
                ),
            ),
        ),
        proposals=(
            AgentProposal(
                proposal=ExperimentProposal(
                    id="profile",
                    hypothesis_id=hid,
                    title="Profile missingness",
                    unknown_ids=(uid,),
                    input_revision=contract.fingerprint(),
                    estimated_cost=(Cost(unit="wall_seconds", amount=2.0),),
                    definitive_negative=True,
                ),
                contract=contract,
            ),
        ),
    )


def test_discovery_is_atomic_grounded_and_replayable(tmp_path):
    from epipilot.research.discovery import apply_discovery
    from epipilot.state.replay import replay_project

    engine, root, revision = make_agent(tmp_path)
    batch = batch_for(engine, root, revision)
    before = engine.state.event_version
    bad = batch.model_copy(
        update={"sources": (batch.sources[0].model_copy(update={"quote": "invented source"}),)}
    )
    with pytest.raises(ValueError, match="quote"):
        apply_discovery(engine, root, bad)
    assert engine.state.event_version == before
    apply_discovery(engine, root, batch)
    assert len(engine.state.hypotheses) == len(engine.state.unknowns) == 1
    assert len(engine.state.research.frontier.questions) == 1
    assert engine.state.hypotheses[0].status.value == "proposed"
    assert engine.state == replay_project("agent", engine.store.load("agent"))
    with pytest.raises(ValueError, match=r"stale|duplicate"):
        apply_discovery(engine, root, batch)


def test_public_claim_creation_cannot_supply_supported_status(tmp_path):
    from epipilot.epistemics.models import HypothesisStatus
    from epipilot.research.discovery import apply_discovery

    engine, root, revision = make_agent(tmp_path)
    batch = batch_for(engine, root, revision)
    f = batch.findings[0]
    forged = f.model_copy(
        update={
            "hypothesis": f.hypothesis.model_copy(
                update={"status": HypothesisStatus.SUPPORTED, "supporting_evidence": (uuid4(),)}
            )
        }
    )
    with pytest.raises(ValueError):
        apply_discovery(engine, root, batch.model_copy(update={"findings": (forged,)}))
    assert not engine.state.hypotheses


def test_atomic_discovery_accepts_mutually_exclusive_new_alternatives(tmp_path):
    from epipilot.research.discovery import apply_discovery

    engine, root, revision = make_agent(tmp_path)
    batch = batch_for(engine, root, revision)
    other = batch_for(engine, root, revision).findings[0]
    first = batch.findings[0]
    first = first.model_copy(
        update={"node": first.node.model_copy(update={"mutex_ids": (other.node.hypothesis_id,)})}
    )
    other = other.model_copy(
        update={
            "topic_key": "alternative",
            "question": other.question.model_copy(update={"topic_key": "alternative"}),
            "node": other.node.model_copy(update={"mutex_ids": (first.node.hypothesis_id,)}),
        }
    )
    apply_discovery(engine, root, batch.model_copy(update={"findings": (first, other)}))
    assert len(engine.state.research.nodes) == 2


@pytest.mark.parametrize("alias", ["./data.csv", "linked.csv"])
def test_alias_inputs_cannot_inflate_samples(tmp_path, alias):
    from epipilot.research.discovery import apply_discovery
    from epipilot.research.frontier import InputFile

    engine, root, revision = make_agent(tmp_path)
    if alias == "linked.csv":
        import os

        os.link(root / "data.csv", root / alias)
    batch = batch_for(engine, root, revision)
    item = batch.proposals[0]
    c = item.contract.model_copy(
        update={
            "inputs": (
                *item.contract.inputs,
                InputFile(path=alias, sha256=item.contract.inputs[0].sha256),
            )
        }
    )
    item = item.model_copy(
        update={
            "contract": c,
            "proposal": item.proposal.model_copy(
                update={"input_revision": c.fingerprint(), "minimum_samples": 4}
            ),
        }
    )
    before = engine.state.event_version
    with pytest.raises(ValueError):
        apply_discovery(engine, root, batch.model_copy(update={"proposals": (item,)}))
    assert engine.state.event_version == before


def test_alias_rename_cannot_bypass_unchanged_experiment(tmp_path):
    import os

    from epipilot.research.agent_service import AgentService
    from epipilot.research.discovery import AgentProposal, DiscoveryBatch, apply_discovery
    from epipilot.research.frontier import InputFile, Purpose

    engine, root, revision = make_agent(tmp_path)
    batch = batch_for(engine, root, revision)
    apply_discovery(engine, root, batch)
    AgentService(engine, root).run_next()
    os.link(root / "data.csv", root / "alias.csv")
    old = batch.proposals[0]
    c = old.contract.model_copy(
        update={
            "proposal_id": "alias",
            "purpose": Purpose.VERIFY,
            "inputs": (InputFile(path="alias.csv", sha256=old.contract.inputs[0].sha256),),
        }
    )
    p = old.proposal.model_copy(update={"id": "alias", "input_revision": c.fingerprint()})
    with pytest.raises(ValueError):
        apply_discovery(
            engine,
            root,
            DiscoveryBatch(
                id=uuid4(),
                based_on_version=engine.state.event_version,
                basis_ref=batch.basis_ref,
                summary="Renaming is not new evidence",
                no_new_findings_reason="same question",
                proposals=(AgentProposal(proposal=p, contract=c),),
            ),
        )
