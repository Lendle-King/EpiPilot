"""Narrow Codex-facing bridge over EpiPilot canonical event state."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from epipilot.core.events import EventType
from epipilot.core.models import (
    EvidenceId,
    EvidenceKind,
    HypothesisId,
    Provenance,
    new_evidence_id,
    utc_now,
)
from epipilot.epistemics.models import (
    HypothesisStatus,
    ResolutionMode,
    UnknownId,
    UnknownImpact,
    new_hypothesis_id,
    new_unknown_id,
)
from epipilot.events.codec import make_project_event
from epipilot.events.payloads import (
    DecisionMadePayload,
    EventPayload,
    EvidenceRecordedPayload,
    ExperimentConcludedPayload,
    ExperimentPredictionPayload,
    ExperimentPreregisteredPayload,
    HypothesisCreatedPayload,
    HypothesisUpdatedPayload,
    RequirementAddedPayload,
    UnknownRegisteredPayload,
    UnknownResolvedPayload,
)
from epipilot.requirements.frontier import DecisionQuestion
from epipilot.requirements.models import (
    DecisionAuthority,
    ProjectContract,
    RequirementKind,
    new_decision_id,
    new_requirement_id,
)
from epipilot.research.contracts import (
    ExperimentContract,
    ExperimentId,
    ExperimentStatus,
    ResearchDirective,
    new_experiment_id,
)
from epipilot.research.policy import choose_research_directive
from epipilot.runtime.event_store import EventStore
from epipilot.state.project import ProjectState
from epipilot.state.reducer import reduce_event
from epipilot.state.replay import replay_project


@dataclass(slots=True)
class CodexResearchBridge:
    """Persist and expose only typed research transitions needed by the Codex plugin."""

    event_store: EventStore

    def project_ids(self) -> tuple[str, ...]:
        return self.event_store.aggregate_ids()

    def state(self, project_id: str) -> ProjectState:
        return replay_project(project_id, self.event_store.load(project_id))

    def start_project(
        self,
        *,
        project_id: str,
        goal: str,
        success_criteria: tuple[str, ...] = (),
        hard_constraints: tuple[str, ...] = (),
        budgets: tuple[str, ...] = (),
        forbidden_actions: tuple[str, ...] = (),
        provenance_source: str = "codex:user",
    ) -> ProjectState:
        if self.event_store.version(project_id) != 0:
            raise ValueError(f"project {project_id!r} already has canonical events")
        values = (goal, *success_criteria, *hard_constraints, *budgets, *forbidden_actions)
        if any(not value.strip() for value in values):
            raise ValueError("project requirements must not contain empty statements")
        requirements = (
            (RequirementKind.GOAL, goal),
            *((RequirementKind.SUCCESS_CRITERION, item) for item in success_criteria),
            *((RequirementKind.HARD_CONSTRAINT, item) for item in hard_constraints),
            *((RequirementKind.BUDGET, item) for item in budgets),
            *((RequirementKind.FORBIDDEN_ACTION, item) for item in forbidden_actions),
        )
        for kind, statement in requirements:
            identifier = new_requirement_id()
            self._append(
                EventType.REQUIREMENT_ADDED,
                project_id,
                RequirementAddedPayload(
                    requirement_id=UUID(str(identifier)),
                    kind=kind,
                    statement=statement,
                    provenance_source=provenance_source,
                    provenance_scope=f"project/{project_id}",
                    provenance_created_at=utc_now(),
                ),
            )
        return self.state(project_id)

    def record_decision(
        self,
        *,
        project_id: str,
        question: str,
        choice: str,
        rationale: str,
        authority: DecisionAuthority,
        basis_refs: tuple[str, ...] = (),
        reversible: bool = True,
    ) -> str:
        identifier = new_decision_id()
        self._append(
            EventType.DECISION_MADE,
            project_id,
            DecisionMadePayload(
                decision_id=UUID(str(identifier)),
                question=question,
                choice=choice,
                authority=authority,
                rationale=rationale,
                basis_refs=basis_refs,
                reversible=reversible,
            ),
        )
        return str(identifier)

    def register_unknown(
        self,
        *,
        project_id: str,
        question: str,
        impact: UnknownImpact,
        resolution_mode: ResolutionMode,
        value_of_information: float = 1.0,
        decision_sensitivity: float = 1.0,
    ) -> UnknownId:
        identifier = new_unknown_id()
        self._append(
            EventType.UNKNOWN_REGISTERED,
            project_id,
            UnknownRegisteredPayload(
                unknown_id=UUID(str(identifier)),
                question=question,
                impact=impact,
                resolution_mode=resolution_mode,
                value_of_information=value_of_information,
                decision_sensitivity=decision_sensitivity,
            ),
        )
        return identifier

    def create_hypothesis(
        self,
        *,
        project_id: str,
        statement: str,
        predictions: tuple[str, ...],
        falsification_conditions: tuple[str, ...],
        confidence: float = 0.5,
    ) -> HypothesisId:
        if not predictions or not falsification_conditions:
            raise ValueError("active hypothesis requires predictions and falsification conditions")
        identifier = new_hypothesis_id()
        self._append(
            EventType.HYPOTHESIS_CREATED,
            project_id,
            HypothesisCreatedPayload(
                hypothesis_id=UUID(str(identifier)),
                statement=statement,
                status=HypothesisStatus.ACTIVE,
                confidence=confidence,
                predictions=predictions,
                falsification_conditions=falsification_conditions,
            ),
        )
        return identifier

    def preregister_experiment(
        self,
        *,
        project_id: str,
        contract: ExperimentContract,
    ) -> ExperimentId:
        identifier = new_experiment_id()
        self._append(
            EventType.EXPERIMENT_PREREGISTERED,
            project_id,
            ExperimentPreregisteredPayload(
                experiment_id=UUID(str(identifier)),
                unknown_id=UUID(str(contract.unknown_id)),
                objective=contract.objective,
                hypothesis_ids=tuple(UUID(str(value)) for value in contract.hypothesis_ids),
                controlled_variables=contract.controlled_variables,
                measurements=contract.measurements,
                predictions=tuple(
                    ExperimentPredictionPayload(
                        hypothesis_id=UUID(str(item.hypothesis_id)),
                        expected_observation=item.expected_observation,
                        falsification_condition=item.falsification_condition,
                    )
                    for item in contract.predictions
                ),
                decision_rule=contract.decision_rule,
                budget=contract.budget,
                resource_claims=contract.resource_claims,
            ),
        )
        return identifier

    def conclude_experiment(
        self,
        *,
        project_id: str,
        experiment_id: ExperimentId,
        status: ExperimentStatus,
        evidence_ids: tuple[EvidenceId, ...],
        conclusion: str,
    ) -> None:
        self._append(
            EventType.EXPERIMENT_CONCLUDED,
            project_id,
            ExperimentConcludedPayload(
                experiment_id=UUID(str(experiment_id)),
                status=status,
                evidence_ids=tuple(UUID(str(value)) for value in evidence_ids),
                conclusion=conclusion,
            ),
        )

    def record_observation(
        self,
        *,
        project_id: str,
        kind: EvidenceKind,
        summary: str,
        provenance: Provenance,
    ) -> EvidenceId:
        """Record a non-authoritative observation from the interactive executor."""
        return self._record_evidence(
            project_id=project_id,
            kind=kind,
            summary=summary,
            provenance=provenance,
            independently_verified=False,
        )

    def _record_verified_evidence(
        self,
        *,
        project_id: str,
        kind: EvidenceKind,
        summary: str,
        provenance: Provenance,
    ) -> EvidenceId:
        return self._record_evidence(
            project_id=project_id,
            kind=kind,
            summary=summary,
            provenance=provenance,
            independently_verified=True,
        )

    def _record_evidence(
        self,
        *,
        project_id: str,
        kind: EvidenceKind,
        summary: str,
        provenance: Provenance,
        independently_verified: bool,
    ) -> EvidenceId:
        identifier = new_evidence_id()
        self._append(
            EventType.EVIDENCE_RECORDED,
            project_id,
            EvidenceRecordedPayload(
                evidence_id=UUID(str(identifier)),
                kind=kind,
                summary=summary,
                provenance_source=provenance.source,
                provenance_scope=provenance.scope,
                provenance_created_at=provenance.created_at,
                independently_verified=independently_verified,
            ),
        )
        return identifier

    def update_hypothesis(
        self,
        *,
        project_id: str,
        hypothesis_id: HypothesisId,
        status: HypothesisStatus,
        confidence: float,
        supporting_evidence: tuple[EvidenceId, ...] = (),
        contradicting_evidence: tuple[EvidenceId, ...] = (),
        superseded_by: HypothesisId | None = None,
    ) -> None:
        self._append(
            EventType.HYPOTHESIS_UPDATED,
            project_id,
            HypothesisUpdatedPayload(
                hypothesis_id=UUID(str(hypothesis_id)),
                status=status,
                confidence=confidence,
                supporting_evidence=tuple(UUID(str(item)) for item in supporting_evidence),
                contradicting_evidence=tuple(UUID(str(item)) for item in contradicting_evidence),
                superseded_by=UUID(str(superseded_by)) if superseded_by is not None else None,
            ),
        )

    def resolve_unknown(
        self,
        *,
        project_id: str,
        unknown_id: UnknownId,
        evidence_ids: tuple[EvidenceId, ...] = (),
        decision_ids: tuple[str, ...] = (),
    ) -> None:
        self._append(
            EventType.UNKNOWN_RESOLVED,
            project_id,
            UnknownResolvedPayload(
                unknown_id=UUID(str(unknown_id)),
                evidence_ids=tuple(UUID(str(item)) for item in evidence_ids),
                decision_ids=tuple(UUID(item) for item in decision_ids),
            ),
        )

    def next_directive(
        self,
        project_id: str,
        *,
        pending_decisions: tuple[DecisionQuestion, ...] = (),
    ) -> ResearchDirective:
        state = self.state(project_id)
        contract = ProjectContract(
            project_id=project_id,
            requirements=state.requirements,
            decisions=state.decisions,
        )
        return choose_research_directive(contract, state, pending_decisions=pending_decisions)

    def _append(self, event_type: EventType, project_id: str, payload: EventPayload) -> None:
        state = self.state(project_id)
        event = make_project_event(event_type, project_id, payload)
        reduce_event(state, event)
        self.event_store.append(event, expected_version=state.event_version)
