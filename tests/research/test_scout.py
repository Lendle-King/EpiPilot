import subprocess
from uuid import uuid4

import pytest
from test_agent_discovery import batch_for, make_agent
from test_intelligence_events import policy_for

from epipilot.core.events import EventType
from epipilot.research.intelligence_models import ResearchPolicyConfigured
from epipilot.research.models import Cost


def ready(tmp_path):
    from epipilot.research.scout import ScoutService

    engine, root, revision = make_agent(tmp_path)
    engine.append(
        EventType.RESEARCH_POLICY_CONFIGURED, ResearchPolicyConfigured(policy=policy_for(engine))
    )
    return engine, root, revision, ScoutService(engine, root)


def test_stale_submission_settles_cost_without_registering_findings(tmp_path):
    from epipilot.events.payloads import TaskCreatedPayload
    from epipilot.research.scout import ScoutSubmission

    engine, root, revision, service = ready(tmp_path)
    service.prepare("one")
    batch = batch_for(engine, root, revision)
    engine.append(
        EventType.TASK_CREATED, TaskCreatedPayload(task_id=uuid4(), objective="concurrent")
    )
    result = service.submit(
        ScoutSubmission(
            operation_id="one", batch=batch, actual_cost=(Cost(unit="wall_seconds", amount=0.3),)
        )
    )
    assert result.status.value == "stale"
    assert not engine.state.hypotheses
    assert engine.budget()["spent"]["wall_seconds"] == 0.3


def test_bad_source_settles_actual_cost_without_partial_discovery(tmp_path):
    from epipilot.research.scout import ScoutSubmission

    engine, root, revision, service = ready(tmp_path)
    service.prepare("one")
    batch = batch_for(engine, root, revision)
    batch = batch.model_copy(
        update={"sources": (batch.sources[0].model_copy(update={"quote": "invented"}),)}
    )
    result = service.submit(
        ScoutSubmission(
            operation_id="one", batch=batch, actual_cost=(Cost(unit="wall_seconds", amount=0.2),)
        )
    )
    assert result.status.value == "invalid"
    assert not engine.state.hypotheses
    assert not engine.state.research.frontier.discoveries
    assert engine.budget()["spent"]["wall_seconds"] == 0.2


def test_epoch_does_not_refresh_from_own_audit_events(tmp_path):
    from epipilot.research.discovery import DiscoveryBatch
    from epipilot.research.scout import ScoutSubmission

    _engine, _, _, service = ready(tmp_path)
    request = service.prepare("one")
    batch = DiscoveryBatch(
        id=uuid4(),
        based_on_version=request.based_on_version,
        basis_ref=request.authority_refs[0],
        summary="No new research",
        no_new_findings_reason="No new justified findings",
    )
    service.submit(
        ScoutSubmission(
            operation_id="one", batch=batch, actual_cost=(Cost(unit="wall_seconds", amount=0.1),)
        )
    )
    with pytest.raises(ValueError, match="unchanged"):
        service.prepare("two")


def test_source_anchor_reads_pinned_file_absent_from_user_checkout(tmp_path):
    from epipilot.research.discovery import apply_discovery

    engine, root, revision = make_agent(tmp_path)
    (root / "new.md").write_text("A new committed scientific document observation.\n")
    subprocess.run(["git", "-C", str(root), "add", "new.md"], check=True)
    tree = subprocess.check_output(["git", "-C", str(root), "write-tree"], text=True).strip()
    new_revision = subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=s@example.invalid",
            "commit-tree",
            tree,
            "-p",
            revision,
            "-m",
            "synthetic isolated result",
        ],
        text=True,
    ).strip()
    (root / "new.md").unlink()
    batch = batch_for(engine, root, revision)
    anchor = batch.sources[0].model_copy(
        update={
            "revision": new_revision,
            "path": "new.md",
            "line": 1,
            "quote": "A new committed scientific document observation.",
        }
    )
    apply_discovery(engine, root, batch.model_copy(update={"sources": (anchor,)}))
    assert len(engine.state.hypotheses) == 1
    assert not (root / "new.md").exists()


def test_cli_exposes_host_submission_schema_without_mutation(capsys):
    import json

    from epipilot.research.agent_cli import main

    assert main(["--project", "synthetic", "scout", "schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["title"] == "ScoutSubmission"
    assert "DecisionEstimate" in schema["$defs"]


def test_active_scout_is_exposed_by_readonly_agent_packet(tmp_path):
    from epipilot.research.agent_service import AgentService

    engine, root, _, scout = ready(tmp_path)
    q = scout.prepare("one")
    version = engine.state.event_version
    packet = AgentService(engine, root).iterate()
    assert packet.scout_request == q
    assert packet.research_policy.id == "v1"
    assert version == engine.state.event_version


def test_unknown_cost_keeps_reservation_and_overrun_blocks_next_launch(tmp_path):
    from epipilot.research.discovery import DiscoveryBatch
    from epipilot.research.scout import ScoutSubmission

    engine, _, _, scout = ready(tmp_path)
    request = scout.prepare("one")
    batch = DiscoveryBatch(
        id=uuid4(),
        based_on_version=request.based_on_version,
        basis_ref=request.authority_refs[0],
        summary="Nothing new",
        no_new_findings_reason="No finding",
    )
    with pytest.raises(ValueError, match="every reserved unit"):
        scout.submit(ScoutSubmission(operation_id="one", batch=batch, actual_cost=()))
    assert len(engine.state.intelligence.active) == 1
    scout.submit(
        ScoutSubmission(
            operation_id="one", batch=batch, actual_cost=(Cost(unit="wall_seconds", amount=31.0),)
        )
    )
    assert engine.budget()["remaining"]["wall_seconds"] == pytest.approx(-1.0)


def test_external_snapshot_hash_does_not_certify_scientific_claim(tmp_path):
    import hashlib
    from datetime import UTC, datetime

    from epipilot.research.intelligence_models import SourceSnapshot
    from epipilot.research.source_snapshots import validate_source_snapshot

    output = tmp_path / "artifacts"
    output.mkdir()
    raw = b"A published paper reports a possible mechanism.\n"
    (output / "note.txt").write_bytes(raw)
    source = SourceSnapshot(
        id="paper",
        url="https://example.invalid/paper",
        title="Synthetic paper",
        artifact="note.txt",
        sha256=hashlib.sha256(raw).hexdigest(),
        accessed_at=datetime.now(UTC),
        access_level="abstract",
        locator="abstract paragraph 1",
        line=1,
        quote=raw.decode().strip(),
    )
    validate_source_snapshot(output, source)
    (output / "note.txt").write_text("Tampered note")
    with pytest.raises(ValueError, match="hash"):
        validate_source_snapshot(output, source)


def test_unknown_downstream_reference_rejects_submission_before_execution(tmp_path):
    from test_decision_value import estimate

    from epipilot.research.intelligence_models import (
        CandidateDisposition,
        DecisionOption,
        OpportunityCandidate,
    )
    from epipilot.research.scout import ScoutSubmission

    engine, root, revision, scout = ready(tmp_path)
    scout.prepare("one")
    batch = batch_for(engine, root, revision)
    f = batch.findings[0]
    candidate = OpportunityCandidate(
        id="c",
        topic_key=f.topic_key,
        question=f.unknown.question,
        alternatives=f.alternatives,
        decision_effect="choose method",
        source_refs=(f"git:{revision}:README.md:2",),
        disposition=CandidateDisposition.ACCEPTED,
        reason="Relevant",
    )
    e = estimate()
    e = e.model_copy(
        update={
            "proposal_id": "profile",
            "options": (
                *e.options,
                DecisionOption(key="bad", baseline_utility=0.0, proposal_id="not-registered"),
            ),
            "branches": tuple(
                b.model_copy(update={"utilities": (*b.utilities, 0.0)}) for b in e.branches
            ),
        }
    )
    result = scout.submit(
        ScoutSubmission(
            operation_id="one",
            batch=batch,
            candidates=(candidate,),
            estimates=(e,),
            actual_cost=(Cost(unit="wall_seconds", amount=0.2),),
        )
    )
    assert result.status.value == "invalid"
    assert not engine.state.hypotheses
    assert engine.budget()["spent"]["wall_seconds"] == 0.2
