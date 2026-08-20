"""Registry binding event kinds to their typed payload schemas."""

from __future__ import annotations

from epipilot.core.events import EventType
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

TYPED_EVENT_SCHEMA_VERSION = 2

EVENT_PAYLOAD_TYPES: dict[EventType, type[EventPayload]] = {
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
