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
from epipilot.research.contracts import (
    ExperimentContract,
    ExperimentPrediction,
    ExperimentStatus,
    ResearchDirectiveKind,
)
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
    assert bridge.project_ids() == ("query-collapse",)
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

    experiment_id = bridge.preregister_experiment(
        project_id="query-collapse",
        contract=ExperimentContract(
            unknown_id=unknown_id,
            objective="Run a frozen representation probe.",
            hypothesis_ids=(hypothesis_id,),
            controlled_variables=("representation weights frozen",),
            measurements=("held-out probe accuracy",),
            predictions=(
                ExperimentPrediction(
                    hypothesis_id=hypothesis_id,
                    expected_observation="accuracy >= 0.95",
                    falsification_condition="accuracy < 0.95",
                ),
            ),
            decision_rule="Support the boundary hypothesis when accuracy >= 0.95.",
            budget="one frozen-probe run",
        ),
    )
    pending = bridge.next_directive("query-collapse")
    assert pending.kind is ResearchDirectiveKind.RUN_EXPERIMENT
    assert pending.experiment_id == experiment_id

    verification = CommandProbeVerifier(bridge).run(
        project_id="query-collapse",
        name="frozen-probe-check",
        argv=(sys.executable, "-c", "raise SystemExit(0)"),
        cwd=Path(),
        scope="project/query-collapse/experiment/frozen-probe",
    )
    assert verification.passed
    evidence_id = verification.evidence_id
    bridge.conclude_experiment(
        project_id="query-collapse",
        experiment_id=experiment_id,
        status=ExperimentStatus.CONCLUDED,
        evidence_ids=(evidence_id,),
        conclusion="The preregistered frozen probe passed.",
    )
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
    assert state.experiments[0].status is ExperimentStatus.CONCLUDED
    assert state.experiments[0].evidence_ids == (evidence_id,)
    assert state.hypotheses[0].status is HypothesisStatus.SUPPORTED
    assert state.unknowns[0].status is UnknownStatus.RESOLVED
    assert bridge.next_directive("query-collapse").kind is ResearchDirectiveKind.SYNTHESIZE


def test_unverified_evidence_cannot_support_resolve_or_conclude() -> None:
    bridge = CodexResearchBridge(InMemoryEventStore())
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
    experiment_id = bridge.preregister_experiment(
        project_id="unsafe",
        contract=ExperimentContract(
            unknown_id=unknown_id,
            objective="Independently check the executor claim.",
            hypothesis_ids=(hypothesis_id,),
            controlled_variables=(),
            measurements=("independent check result",),
            predictions=(
                ExperimentPrediction(
                    hypothesis_id=hypothesis_id,
                    expected_observation="check agrees",
                    falsification_condition="check disagrees",
                ),
            ),
            decision_rule="Accept the claim only if the independent check agrees.",
            budget="one check",
        ),
    )
    evidence_id = bridge.record_observation(
        project_id="unsafe",
        kind=EvidenceKind.EXECUTOR_REPORT,
        summary="I think this is the root cause.",
        provenance=Provenance(source="codex", scope="project/unsafe"),
    )

    with pytest.raises(InvalidEventOrder):
        bridge.conclude_experiment(
            project_id="unsafe",
            experiment_id=experiment_id,
            status=ExperimentStatus.CONCLUDED,
            evidence_ids=(evidence_id,),
            conclusion="Executor says it passed.",
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
    assert state.experiments[0].status is ExperimentStatus.PREREGISTERED
    assert state.hypotheses[0].status is HypothesisStatus.ACTIVE
    assert state.unknowns[0].status is UnknownStatus.OPEN


def test_safe_default_unknown_resolves_from_canonical_decision() -> None:
    bridge = CodexResearchBridge(InMemoryEventStore())
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
