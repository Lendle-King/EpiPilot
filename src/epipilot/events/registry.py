"""Registry binding event kinds to their typed payload schemas."""

from __future__ import annotations

from epipilot.core.events import EventType
from epipilot.engineering.models import ManagedEvent
from epipilot.events.payloads import (
    ContextCompiledPayload,
    DecisionMadePayload,
    EventPayload,
    EvidenceRecordedPayload,
    ExecutorObservationRecordedPayload,
    HypothesisCreatedPayload,
    PlanVersionCreatedPayload,
    RequirementAddedPayload,
    TaskCreatedPayload,
    TaskStartedPayload,
    TaskStatusChangedPayload,
    TaskSupersededPayload,
    UnknownRegisteredPayload,
    VerificationFailedPayload,
    VerificationPassedPayload,
)
from epipilot.research.events import (
    BudgetSettled,
    ExperimentObserved,
    ExperimentReserved,
    ExperimentReviewed,
    ProposalAdded,
    ResearchBudgetExtended,
    ResearchConfigured,
    ResearchGoalRevised,
    ResearchNodeAdded,
)
from epipilot.research.frontier import FrontierDiscovered, FrontierInitialized, QuestionResolved
from epipilot.research.intelligence_models import (
    DecisionForecastRecorded,
    DecisionOutcomeRecorded,
    ResearchPolicyConfigured,
    ScoutCompleted,
    ScoutStarted,
)

TYPED_EVENT_SCHEMA_VERSION = 2

EVENT_PAYLOAD_TYPES: dict[EventType, type[EventPayload]] = {
    EventType.RESEARCH_POLICY_CONFIGURED: ResearchPolicyConfigured,
    EventType.SCOUT_STARTED: ScoutStarted,
    EventType.SCOUT_COMPLETED: ScoutCompleted,
    EventType.DECISION_FORECAST_RECORDED: DecisionForecastRecorded,
    EventType.DECISION_OUTCOME_RECORDED: DecisionOutcomeRecorded,
    EventType.MANAGED_WORK: ManagedEvent,
    EventType.RESEARCH_FRONTIER_INITIALIZED: FrontierInitialized,
    EventType.RESEARCH_FRONTIER_DISCOVERED: FrontierDiscovered,
    EventType.RESEARCH_QUESTION_RESOLVED: QuestionResolved,
    EventType.RESEARCH_NODE_ADDED: ResearchNodeAdded,
    EventType.RESEARCH_CONFIGURED: ResearchConfigured,
    EventType.RESEARCH_GOAL_REVISED: ResearchGoalRevised,
    EventType.RESEARCH_BUDGET_EXTENDED: ResearchBudgetExtended,
    EventType.PROPOSAL_ADDED: ProposalAdded,
    EventType.EXPERIMENT_RESERVED: ExperimentReserved,
    EventType.EXPERIMENT_OBSERVED: ExperimentObserved,
    EventType.EXPERIMENT_REVIEWED: ExperimentReviewed,
    EventType.BUDGET_SETTLED: BudgetSettled,
    EventType.REQUIREMENT_ADDED: RequirementAddedPayload,
    EventType.DECISION_MADE: DecisionMadePayload,
    EventType.UNKNOWN_REGISTERED: UnknownRegisteredPayload,
    EventType.HYPOTHESIS_CREATED: HypothesisCreatedPayload,
    EventType.EVIDENCE_RECORDED: EvidenceRecordedPayload,
    EventType.TASK_CREATED: TaskCreatedPayload,
    EventType.TASK_STARTED: TaskStartedPayload,
    EventType.TASK_STATUS_CHANGED: TaskStatusChangedPayload,
    EventType.CONTEXT_COMPILED: ContextCompiledPayload,
    EventType.EXECUTOR_OBSERVATION_RECORDED: ExecutorObservationRecordedPayload,
    EventType.VERIFICATION_PASSED: VerificationPassedPayload,
    EventType.VERIFICATION_FAILED: VerificationFailedPayload,
    EventType.TASK_SUPERSEDED: TaskSupersededPayload,
    EventType.PLAN_VERSION_CREATED: PlanVersionCreatedPayload,
}

if set(EVENT_PAYLOAD_TYPES) != set(EventType):
    missing = set(EventType) - set(EVENT_PAYLOAD_TYPES)
    extra = set(EVENT_PAYLOAD_TYPES) - set(EventType)
    raise RuntimeError(f"event payload registry mismatch: missing={missing}, extra={extra}")
