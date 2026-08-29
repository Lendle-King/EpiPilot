import sys
from pathlib import Path

import pytest

from epipilot.core.models import EvidenceKind, Provenance
from epipilot.epistemics.models import (
    HypothesisStatus,
    ResolutionMode,
    UnknownImpact,
    UnknownStatus,
)
from epipilot.integrations.codex.bridge import CodexResearchBridge
from epipilot.integrations.codex.verifier import CommandProbeVerifier
from epipilot.requirements.models import DecisionAuthority
from epipilot.research.contracts import ResearchDirectiveKind
from epipilot.runtime.event_store import InMemoryEventStore
from epipilot.state.errors import InvalidEventOrder


def test_codex_bridge_closes_verified_epistemic_loop() -> None:
    store = InMemoryEventStore()
    bridge = CodexResearchBridge(store)
    bridge.start_project(
        project_id="query-collapse",
        goal="Explain and repair query-cloud collapse",
        success_criteria=("Fresh held-out evaluation verifies the repair",),
        hard_constraints=("Do not change evaluator semantics",),
    )
    unknown_id = bridge.register_unknown(
        project_id="query-collapse",
        question="Is the collapse caused by a bad request-head boundary?",
        impact=UnknownImpact.HIGH,
        resolution_mode=ResolutionMode.EXPERIMENT,
    )
    hypothesis_id = bridge.create_hypothesis(
        project_id="query-collapse",
        statement="The representation is sufficient but the request-head boundary is misplaced.",
        predictions=("A frozen representation probe remains accurate.",),
        falsification_conditions=("Held-out probe accuracy falls below 0.95.",),
    )

    before = bridge.next_directive("query-collapse")
    assert before.kind is ResearchDirectiveKind.INVESTIGATE
    assert before.unknown_id == unknown_id

    verification = CommandProbeVerifier(bridge).run(
        project_id="query-collapse",
        name="frozen-probe-check",
        argv=(sys.executable, "-c", "raise SystemExit(0)"),
        cwd=Path("."),
        scope="project/query-collapse/experiment/frozen-probe",
    )
    assert verification.passed
    evidence_id = verification.evidence_id
    bridge.update_hypothesis(
        project_id="query-collapse",
        hypothesis_id=hypothesis_id,
        status=HypothesisStatus.SUPPORTED,
        confidence=0.95,
        supporting_evidence=(evidence_id,),
    )
    bridge.resolve_unknown(
        project_id="query-collapse",
        unknown_id=unknown_id,
        evidence_ids=(evidence_id,),
    )

    state = bridge.state("query-collapse")
    assert state.hypotheses[0].status is HypothesisStatus.SUPPORTED
    assert state.unknowns[0].status is UnknownStatus.RESOLVED
    assert state.unknowns[0].resolution_evidence == (evidence_id,)
    assert bridge.next_directive("query-collapse").kind is ResearchDirectiveKind.SYNTHESIZE


def test_unverified_evidence_cannot_support_or_resolve() -> None:
    store = InMemoryEventStore()
    bridge = CodexResearchBridge(store)
    bridge.start_project(
        project_id="unsafe",
        goal="Diagnose a failure",
        success_criteria=("Independent evidence identifies the cause",),
    )
    unknown_id = bridge.register_unknown(
        project_id="unsafe",
        question="Is the executor report correct?",
        impact=UnknownImpact.HIGH,
        resolution_mode=ResolutionMode.EXPERIMENT,
    )
    hypothesis_id = bridge.create_hypothesis(
        project_id="unsafe",
        statement="The executor report is correct.",
        predictions=("An independent check agrees.",),
        falsification_conditions=("An independent check disagrees.",),
    )
    evidence_id = bridge.record_observation(
        project_id="unsafe",
        kind=EvidenceKind.EXECUTOR_REPORT,
        summary="I think this is the root cause.",
        provenance=Provenance(source="codex", scope="project/unsafe"),
    )

    with pytest.raises(InvalidEventOrder):
        bridge.update_hypothesis(
            project_id="unsafe",
            hypothesis_id=hypothesis_id,
            status=HypothesisStatus.SUPPORTED,
            confidence=0.9,
            supporting_evidence=(evidence_id,),
        )

    with pytest.raises(InvalidEventOrder):
        bridge.resolve_unknown(
            project_id="unsafe",
            unknown_id=unknown_id,
            evidence_ids=(evidence_id,),
        )
    state = bridge.state("unsafe")
    assert state.hypotheses[0].status is HypothesisStatus.ACTIVE
    assert state.unknowns[0].status is UnknownStatus.OPEN


def test_safe_default_unknown_resolves_from_canonical_decision() -> None:
    store = InMemoryEventStore()
    bridge = CodexResearchBridge(store)
    bridge.start_project(
        project_id="safe-default",
        goal="Run a bounded probe",
        success_criteria=("Probe result is independently checked",),
    )
    unknown_id = bridge.register_unknown(
        project_id="safe-default",
        question="Which reversible probe batch size should be used?",
        impact=UnknownImpact.LOW,
        resolution_mode=ResolutionMode.SAFE_DEFAULT,
    )

    assert bridge.next_directive("safe-default").kind is ResearchDirectiveKind.USE_SAFE_DEFAULT

    decision_id = bridge.record_decision(
        project_id="safe-default",
        question="Which reversible probe batch size should be used?",
        choice="Use batch size 8",
        rationale="It is a low-risk reversible default within the declared budget.",
        authority=DecisionAuthority.SYSTEM,
    )
    bridge.resolve_unknown(
        project_id="safe-default",
        unknown_id=unknown_id,
        decision_ids=(decision_id,),
    )

    state = bridge.state("safe-default")
    assert state.unknowns[0].status is UnknownStatus.RESOLVED
    assert state.unknowns[0].resolution_decisions == (decision_id,)
