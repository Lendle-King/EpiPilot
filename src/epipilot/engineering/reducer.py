from epipilot.engineering.models import ManagedEvent, WorkStatus


def validate_managed(history: tuple[ManagedEvent, ...], event: ManagedEvent) -> None:
    if (event.policy is None) == (event.work is None):
        raise ValueError("managed event requires exactly one policy or work record")
    policies = [e.policy for e in history if e.policy is not None]
    if event.policy is not None:
        if policies:
            raise ValueError("managed policy cannot be overwritten")
        return
    work = event.work
    assert work is not None
    if not policies:
        raise ValueError("managed work requires explicit policy")
    prior = next(
        (
            e.work
            for e in reversed(history)
            if e.work is not None and e.work.operation_id == work.operation_id
        ),
        None,
    )
    if prior is None:
        if work.status is not WorkStatus.ACQUIRING:
            raise ValueError("work must begin with acquiring intent")
    elif (work.cwd, work.base, work.scope, work.forbidden) != (
        prior.cwd,
        prior.base,
        prior.scope,
        prior.forbidden,
    ):
        raise ValueError("lease identity and scope are immutable")
    if prior is not None:
        transitions = {
            WorkStatus.ACQUIRING: {WorkStatus.ACTIVE},
            WorkStatus.ACTIVE: {WorkStatus.COMMITTING},
            WorkStatus.COMMITTING: {WorkStatus.COMMITTING, WorkStatus.COMMITTED},
            WorkStatus.COMMITTED: {WorkStatus.VERIFYING, WorkStatus.BLOCKED},
            WorkStatus.VERIFYING: {WorkStatus.VERIFYING, WorkStatus.VERIFIED, WorkStatus.BLOCKED},
            WorkStatus.VERIFIED: {WorkStatus.INTEGRATING, WorkStatus.VERIFYING},
            WorkStatus.INTEGRATING: {
                WorkStatus.INTEGRATED,
                WorkStatus.INTEGRATING,
                WorkStatus.VERIFYING,
            },
            WorkStatus.INTEGRATED: {WorkStatus.INTEGRATED, WorkStatus.PUBLISHING},
            WorkStatus.PUBLISHING: {WorkStatus.PUBLISHING, WorkStatus.PUSHED},
            WorkStatus.PUSHED: {WorkStatus.PUSHED},
            WorkStatus.BLOCKED: {WorkStatus.VERIFYING, WorkStatus.VERIFIED, WorkStatus.BLOCKED},
        }
        if work.status not in transitions[prior.status]:
            raise ValueError("invalid managed lifecycle transition")
        if prior.task_commit and work.task_commit != prior.task_commit:
            raise ValueError("task receipt is immutable")
        if (
            work.status is not WorkStatus.VERIFYING
            and prior.candidate
            and (
                work.candidate,
                work.candidate_tree,
                work.integration_base,
                work.profile_hash,
                work.environment_hash,
            )
            != (
                prior.candidate,
                prior.candidate_tree,
                prior.integration_base,
                prior.profile_hash,
                prior.environment_hash,
            )
        ):
            raise ValueError("candidate receipt is immutable outside verifying intent")
        permitted = {
            WorkStatus.ACTIVE: {"status"},
            WorkStatus.COMMITTING: {"status"},
            WorkStatus.COMMITTED: {"status", "task_commit"},
            WorkStatus.VERIFYING: {
                "status",
                "candidate",
                "candidate_tree",
                "integration_base",
                "profile_hash",
                "environment_hash",
                "reason",
            },
            WorkStatus.VERIFIED: {"status"},
            WorkStatus.BLOCKED: {"status", "reason"},
            WorkStatus.INTEGRATING: {"status"},
            WorkStatus.INTEGRATED: {"status", "integrated_commit", "release_intent", "released"},
            WorkStatus.PUBLISHING: {"status", "artifact_refs"},
            WorkStatus.PUSHED: {"status", "release_intent", "released", "publication_result"},
        }
        changed = {
            key for key, value in work.model_dump().items() if value != prior.model_dump()[key]
        }
        if changed - permitted[work.status]:
            raise ValueError("forged receipt fields outside authorized transition")
        if work.released and (
            not prior.release_intent
            or work.status not in {WorkStatus.INTEGRATED, WorkStatus.PUSHED}
        ):
            raise ValueError("release requires completed work and release intent")
    if work.status in {
        WorkStatus.VERIFIED,
        WorkStatus.INTEGRATING,
        WorkStatus.INTEGRATED,
        WorkStatus.PUBLISHING,
        WorkStatus.PUSHED,
    } and not all(
        (
            work.task_commit,
            work.candidate,
            work.candidate_tree,
            work.integration_base,
            work.profile_hash,
        )
    ):
        raise ValueError("verified work requires immutable candidate binding")
    if (
        work.status in {WorkStatus.INTEGRATED, WorkStatus.PUBLISHING, WorkStatus.PUSHED}
        and work.integrated_commit != work.candidate
    ):
        raise ValueError("integration must equal verified candidate")
    if work.status in {WorkStatus.PUBLISHING, WorkStatus.PUSHED} and not work.artifact_refs:
        raise ValueError("publication requires milestone artifact refs")
