from enum import StrEnum

from pydantic import Field

from epipilot.events.payloads import EventPayload


class WorkStatus(StrEnum):
    ACQUIRING = "acquiring"
    ACTIVE = "active"
    COMMITTING = "committing"
    COMMITTED = "committed"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    INTEGRATING = "integrating"
    INTEGRATED = "integrated"
    PUBLISHING = "publishing"
    PUSHED = "pushed"
    BLOCKED = "blocked"


class ManagedPolicy(EventPayload):
    repository: str
    common_dir: str
    worktree_root: str
    integration_branch: str = "epipilot/integration"
    remote: str
    remote_url: str
    publication_ref: str
    acceptance: tuple[tuple[str, ...], ...] = Field(min_length=1)
    profile: str = "engineering-v1"
    timeout_seconds: int = Field(default=300, gt=0, le=3600)


class WorkLease(EventPayload):
    operation_id: str
    cwd: str
    base: str
    scope: tuple[str, ...] = Field(min_length=1)
    forbidden: tuple[str, ...] = ()
    status: WorkStatus
    task_commit: str = ""
    candidate: str = ""
    candidate_tree: str = ""
    integration_base: str = ""
    profile_hash: str = ""
    environment_hash: str = ""
    released: bool = False
    release_intent: bool = False
    integrated_commit: str = ""
    artifact_refs: tuple[str, ...] = ()
    reason: str = ""
    publication_result: str = ""


class MilestoneSource(StrEnum):
    ENGINEERING_DELIVERY = "engineering_delivery"
    RESEARCH_CHANGE = "research_change"
    STAGE_REPORT = "stage_report"


class PublicationDue(EventPayload):
    operation_id: str
    source: MilestoneSource
    references: tuple[str, str]
    commit: str


class ManagedEvent(EventPayload):
    policy: ManagedPolicy | None = None
    work: WorkLease | None = None
