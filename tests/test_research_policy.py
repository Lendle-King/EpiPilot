from datetime import UTC, datetime
from uuid import UUID

from epipilot.core.models import EvidenceId, HypothesisId, Provenance, RequirementId
from epipilot.epistemics.models import (
    ResolutionMode,
    Unknown,
    UnknownId,
    UnknownImpact,
    UnknownStatus,
)
from epipilot.requirements.frontier import (
    DecisionImpact,
    DecisionOwner,
    DecisionQuestion,
)
from epipilot.requirements.models import (
    ProjectContract,
    Requirement,
    RequirementKind,
)
from epipilot.research.contracts import (
    ExperimentContract,
    ExperimentPrediction,
    ResearchDirectiveKind,
)
from epipilot.research.policy import choose_research_directive
from epipilot.state.project import ProjectState

CREATED_AT = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)
GOAL_ID = RequirementId(UUID("00000000-0000-0000-0000-000000000501"))
SUCCESS_ID = RequirementId(UUID("00000000-0000-0000-0000-000000000502"))
UNKNOWN_ID = UnknownId(UUID("00000000-0000-0000-0000-000000000503"))
EVIDENCE_ID = EvidenceId(UUID("00000000-0000-0000-0000-000000000504"))
H1 = HypothesisId(UUID("00000000-0000-0000-0000-000000000505"))
H2 = HypothesisId(UUID("00000000-0000-0000-0000-000000000506"))


def _requirements() -> tuple[Requirement, ...]:
    provenance = Provenance(source="user", scope="project/research", created_at=CREATED_AT)
    return (
        Requirement(
            id=GOAL_ID,
            kind=RequirementKind.GOAL,
            statement="Find the root cause",
            provenance=provenance,
        ),
        Requirement(
            id=SUCCESS_ID,
            kind=RequirementKind.SUCCESS_CRITERION,
            statement="A fresh held-out experiment reproduces the repair",
            provenance=provenance,
        ),
    )


def test_policy_investigates_open_technical_unknown() -> None:
    requirements = _requirements()
    contract = ProjectContract(project_id="research", requirements=requirements)
    unknown = Unknown(
        id=UNKNOWN_ID,
        question="Why does the policy collapse?",
        impact=UnknownImpact.HIGH,
        resolution_mode=ResolutionMode.EXPERIMENT,
    )
    state = ProjectState(project_id="research", requirements=requirements, unknowns=(unknown,))

    directive = choose_research_directive(contract, state)

    assert directive.kind is ResearchDirectiveKind.INVESTIGATE
    assert directive.unknown_id == UNKNOWN_ID


def test_policy_asks_for_high_impact_user_decision() -> None:
    requirements = _requirements()
    contract = ProjectContract(project_id="research", requirements=requirements)
    state = ProjectState(project_id="research", requirements=requirements)
    question = DecisionQuestion(
        question="May the evaluator be changed?",
        owner=DecisionOwner.USER,
        impact=DecisionImpact.HIGH,
    )

    directive = choose_research_directive(
        contract,
        state,
        pending_decisions=(question,),
    )

    assert directive.kind is ResearchDirectiveKind.ASK_USER
    assert directive.questions == ("May the evaluator be changed?",)


def test_resolved_unknown_reaches_synthesis_not_automatic_acceptance() -> None:
    requirements = _requirements()
    contract = ProjectContract(project_id="research", requirements=requirements)
    unknown = Unknown(
        id=UNKNOWN_ID,
        question="Why does the policy collapse?",
        impact=UnknownImpact.HIGH,
        resolution_mode=ResolutionMode.EXPERIMENT,
        status=UnknownStatus.RESOLVED,
        resolution_evidence=(EVIDENCE_ID,),
    )
    state = ProjectState(project_id="research", requirements=requirements, unknowns=(unknown,))

    directive = choose_research_directive(contract, state)

    assert directive.kind is ResearchDirectiveKind.SYNTHESIZE
    assert "acceptance" in directive.reason


def test_policy_surfaces_reversible_safe_default() -> None:
    requirements = _requirements()
    contract = ProjectContract(project_id="research", requirements=requirements)
    unknown = Unknown(
        id=UNKNOWN_ID,
        question="Which reversible cache size should the probe use?",
        impact=UnknownImpact.LOW,
        resolution_mode=ResolutionMode.SAFE_DEFAULT,
    )
    state = ProjectState(project_id="research", requirements=requirements, unknowns=(unknown,))

    directive = choose_research_directive(contract, state)

    assert directive.kind is ResearchDirectiveKind.USE_SAFE_DEFAULT
    assert directive.unknown_id == UNKNOWN_ID


def test_experiment_contract_requires_prediction_for_every_hypothesis() -> None:
    experiment = ExperimentContract(
        unknown_id=UNKNOWN_ID,
        objective="Discriminate representation failure from head-boundary failure",
        hypothesis_ids=(H1, H2),
        controlled_variables=("frozen representation",),
        measurements=("held-out probe accuracy",),
        predictions=(
            ExperimentPrediction(
                hypothesis_id=H1,
                expected_observation="probe accuracy is low",
                falsification_condition="probe accuracy >= 0.95",
            ),
            ExperimentPrediction(
                hypothesis_id=H2,
                expected_observation="probe accuracy remains high",
                falsification_condition="probe accuracy < 0.95",
            ),
        ),
        decision_rule="Choose the hypothesis whose preregistered prediction matches the probe.",
        budget="one frozen-probe run",
    )

    assert {item.hypothesis_id for item in experiment.predictions} == {H1, H2}
