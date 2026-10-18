"""Single-writer, append-only research memory with validated deterministic replay."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from epipilot.research.models import (
    Claim,
    ClaimStatus,
    EvidenceRecord,
    Experiment,
    OnboardingSnapshot,
    Proposal,
    ResearchEvent,
    ResearchGoal,
    ResearchState,
    Unknown,
)


def _apply(state: ResearchState, event: ResearchEvent) -> None:
    """Apply a validated event; reject invalid links instead of inventing state."""
    payload = event.payload
    kind = event.kind

    if kind == "goal_set":
        if state.goal is not None:
            raise ValueError("goal already exists; only user-approved goal changes may replace it")
        state.goal = ResearchGoal.model_validate(payload)
    elif kind == "unknown_added":
        item = Unknown.model_validate(payload)
        if item.id in state.unknowns:
            raise ValueError("duplicate unknown id")
        state.unknowns[item.id] = item
    elif kind == "claim_added":
        item = Claim.model_validate(payload)
        if item.id in state.claims:
            raise ValueError("duplicate claim id")
        state.claims[item.id] = item
    elif kind == "proposal_added":
        item = Proposal.model_validate(payload)
        if item.id in state.proposals:
            raise ValueError("duplicate proposal id")
        if set(item.depends_on) - state.proposals.keys():
            raise ValueError("proposal dependencies must exist")
        if set(item.unknown_ids) - state.unknowns.keys():
            raise ValueError("proposal references unknown unknowns")
        if set(item.claim_ids) - state.claims.keys():
            raise ValueError("proposal references unknown claims")
        state.proposals[item.id] = item
    elif kind == "experiment_recorded":
        item = Experiment.model_validate(payload["experiment"])
        evidence = EvidenceRecord.model_validate(payload["evidence"])
        if state.goal is None:
            raise ValueError("project has no user goal")
        if item.id in state.experiments or evidence.id in state.evidence:
            raise ValueError("duplicate experiment or evidence id")
        proposal = state.proposals.get(item.proposal_id)
        if proposal is None or proposal.status != "proposed":
            raise ValueError("experiment needs an available proposal")
        if state.spent + item.actual_cost > state.goal.budget + 1e-9:
            raise ValueError("experiment exceeds research budget")
        if evidence.experiment_id != item.id or item.evidence_id != evidence.id:
            raise ValueError("experiment and evidence links disagree")
        if evidence.independently_verified:
            raise ValueError("executor cannot submit independently verified evidence")
        state.experiments[item.id] = item
        state.evidence[evidence.id] = evidence
        state.proposals[item.proposal_id] = proposal.model_copy(
            update={"status": "awaiting_verification"}
        )
    elif kind == "verification_recorded":
        evidence_id = str(payload["evidence_id"])
        evidence = state.evidence.get(evidence_id)
        if evidence is None or evidence.independently_verified:
            raise ValueError("unknown or already verified evidence")
        if not payload["check_argv"] or not payload["check_output_sha256"]:
            raise ValueError("independent check details required")
        state.evidence[evidence_id] = evidence.model_copy(
            update={
                "independently_verified": True,
                "check_argv": list(payload["check_argv"]),
                "check_output_sha256": str(payload["check_output_sha256"]),
                "verified_at": event.created_at,
            }
        )
        experiment = state.experiments[evidence.experiment_id]
        proposal = state.proposals[experiment.proposal_id]
        state.proposals[proposal.id] = proposal.model_copy(
            update={
                "status": "blocked"
                if experiment.outcome == "infrastructure_failure"
                else "completed"
            }
        )
    elif kind == "verification_failed":
        if str(payload["evidence_id"]) not in state.evidence:
            raise ValueError("unknown verification evidence")
        # Retain failed check as an event; never promote an observation.
    elif kind == "claim_reviewed":
        claim = state.claims.get(str(payload["claim_id"]))
        if claim is None:
            raise ValueError("unknown claim")
        evidence_id = str(payload["evidence_id"])
        evidence = state.evidence.get(evidence_id)
        if evidence is None or not evidence.independently_verified:
            raise ValueError("claim review requires independently checked evidence")
        status = ClaimStatus(str(payload["status"]))
        if status not in {ClaimStatus.SUPPORTED, ClaimStatus.REFUTED, ClaimStatus.INCONCLUSIVE}:
            raise ValueError("unsupported claim review status")
        support = list(claim.supporting_evidence_ids)
        contradict = list(claim.contradicting_evidence_ids)
        if evidence_id in support or evidence_id in contradict:
            raise ValueError("evidence already used to review this claim")
        if status == ClaimStatus.SUPPORTED:
            support.append(evidence_id)
        elif status == ClaimStatus.REFUTED:
            contradict.append(evidence_id)
        state.claims[claim.id] = claim.model_copy(
            update={
                "status": status,
                "supporting_evidence_ids": support,
                "contradicting_evidence_ids": contradict,
            }
        )
    elif kind == "unknown_resolved":
        unknown = state.unknowns.get(str(payload["unknown_id"]))
        evidence_id = str(payload["evidence_id"])
        if unknown is None or unknown.status != "open":
            raise ValueError("unknown is missing or already resolved")
        evidence = state.evidence.get(evidence_id)
        if evidence is None or not evidence.independently_verified:
            raise ValueError("resolving unknown requires independently checked evidence")
        state.unknowns[unknown.id] = unknown.model_copy(
            update={"status": "resolved", "resolution_evidence_ids": [evidence_id]}
        )
    elif kind == "onboarding_recorded":
        state.onboarding.append(OnboardingSnapshot.model_validate(payload))
    else:
        raise ValueError(f"unrecognized research event: {kind}")


class ResearchStore:
    """Repository-local event stream, safe for one CLI writer at a time.

    A filesystem creation lock prevents concurrent writers. A crashed writer can
    leave a stale .write.lock; recovery requires deliberate operator intervention.
    Event corruption fails closed. No hidden cache is authoritative.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.events_path = root / "events.jsonl"
        self.lock_path = root / ".write.lock"

    def load(self) -> tuple[ResearchState, int]:
        state = ResearchState()
        sequence = 0
        if not self.events_path.exists():
            return state, sequence
        with self.events_path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ValueError(f"blank event line {line_no}")
                event = ResearchEvent.model_validate_json(line)
                if event.sequence != sequence + 1:
                    raise ValueError(f"out-of-order event at line {line_no}")
                _apply(state, event)
                sequence = event.sequence
        return state, sequence

    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(
                "research store is locked; check for an active writer before removing .write.lock"
            ) from exc
        try:
            os.close(fd)
            yield
        finally:
            self.lock_path.unlink()

    def append(self, kind: str, payload: dict[str, Any]) -> ResearchEvent:
        """Validate against current state BEFORE durably appending the new event."""
        with self._writer_lock():
            old_state, sequence = self.load()
            event = ResearchEvent(sequence=sequence + 1, kind=kind, payload=payload)
            trial = old_state.model_copy(deep=True)
            _apply(trial, event)
            serialized = event.model_dump_json() + "\n"
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            return event

    def add(
        self, kind: str, record: ResearchGoal | Unknown | Claim | Proposal | OnboardingSnapshot
    ) -> ResearchEvent:
        return self.append(kind, record.model_dump(mode="json"))
