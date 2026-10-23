"""Single-writer append-only research state with fail-closed, deterministic replay.

Schema-v1 streams remain readable. New schema-v2 events require pre-registration
and measurement validation before claim review or unknown resolution.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from epipilot.research.decision_models import DecisionEstimate, DecisionForecast
from epipilot.research.decision_value import rank_decision, record_outcome
from epipilot.research.models import (
    Claim,
    ClaimStatus,
    ContractStatus,
    EvidenceRecord,
    Experiment,
    ExperimentContract,
    OnboardingSnapshot,
    Proposal,
    ResearchEvent,
    ResearchGoal,
    ResearchState,
    Unknown,
)


def _related_evidence(
    state: ResearchState, evidence_id: str
) -> tuple[EvidenceRecord, Experiment, ExperimentContract]:
    evidence = state.evidence.get(evidence_id)
    if evidence is None:
        raise ValueError("unknown evidence")
    experiment = state.experiments[evidence.experiment_id]
    if experiment.contract_id is None or experiment.contract_id not in state.contracts:
        raise ValueError("legacy observations require a new validated experiment")
    return evidence, experiment, state.contracts[experiment.contract_id]


def _apply(state: ResearchState, event: ResearchEvent) -> None:
    """Apply one event to a disposable state projection; no external side effects."""
    p = event.payload
    kind = event.kind
    legacy = event.schema_version == 1

    if kind == "goal_set":
        if state.goal is not None:
            raise ValueError("goal already exists; goal changes need explicit user authorization")
        state.goal = ResearchGoal.model_validate(p)
    elif kind == "unknown_added":
        new_unknown = Unknown.model_validate(p)
        if new_unknown.id in state.unknowns:
            raise ValueError("duplicate unknown id")
        state.unknowns[new_unknown.id] = new_unknown
    elif kind == "claim_added":
        new_claim = Claim.model_validate(p)
        if new_claim.id in state.claims:
            raise ValueError("duplicate claim id")
        state.claims[new_claim.id] = new_claim
    elif kind == "proposal_added":
        new_proposal = Proposal.model_validate(p)
        if new_proposal.id in state.proposals:
            raise ValueError("duplicate proposal id")
        if set(new_proposal.depends_on) - state.proposals.keys():
            raise ValueError("proposal dependencies must exist")
        if set(new_proposal.unknown_ids) - state.unknowns.keys():
            raise ValueError("proposal references unknown unknowns")
        if set(new_proposal.claim_ids) - state.claims.keys():
            raise ValueError("proposal references unknown claims")
        if not legacy and any(
            previous.title.strip().casefold() == new_proposal.title.strip().casefold()
            and previous.kind == new_proposal.kind
            and previous.unknown_ids == new_proposal.unknown_ids
            and previous.claim_ids == new_proposal.claim_ids
            and previous.status != "aborted"
            for previous in state.proposals.values()
        ):
            raise ValueError("duplicate research proposal; supply a genuinely revised hypothesis")
        state.proposals[new_proposal.id] = new_proposal
    elif kind == "decision_estimate_registered":
        if legacy:
            raise ValueError("decision estimates require schema v2")
        estimate = DecisionEstimate.model_validate(p)
        proposed = state.proposals.get(estimate.proposal_id)
        if proposed is None or proposed.status != "proposed":
            raise ValueError("decision estimate requires a proposed research action")
        if estimate.proposal_id in state.decision_estimates:
            raise ValueError("decision estimate already registered; create a new proposal")
        if any(
            option.proposal_id is not None
            and (option.proposal_id == proposed.id or option.proposal_id not in state.proposals)
            for option in estimate.options
        ):
            raise ValueError("decision table contains unknown or self-referential options")
        state.decision_estimates[proposed.id] = estimate
    elif kind == "experiment_preregistered":
        registration = ExperimentContract.model_validate(p)
        if state.goal is None:
            raise ValueError("research goal required before pre-registration")
        if registration.id in state.contracts:
            raise ValueError("duplicate experiment contract")
        proposal = state.proposals.get(registration.proposal_id)
        if proposal is None or proposal.status != "proposed":
            raise ValueError("pre-registration requires an available proposal")
        if any(state.proposals[parent].status != "completed" for parent in proposal.depends_on):
            raise ValueError("pre-registration dependencies not completed")
        if not proposal.unknown_ids and not proposal.claim_ids:
            raise ValueError("pre-registration must link a claim or a research unknown")
        if registration.max_cost > state.remaining + 1e-9:
            raise ValueError("pre-registered experiment exceeds remaining budget")
        estimate = state.decision_estimates.get(proposal.id)
        if estimate is not None:
            frozen_score = rank_decision(state, estimate)
            state.decision_forecasts[registration.id] = DecisionForecast(
                contract_id=registration.id,
                git_revision=registration.git_revision,
                recorded_at=event.created_at,
                estimate=estimate,
                score=frozen_score,
            )
        state.contracts[registration.id] = registration
        state.proposals[proposal.id] = proposal.model_copy(update={"status": "preregistered"})
    elif kind == "experiment_started":
        started_contract = state.contracts.get(str(p["contract_id"]))
        if started_contract is None or started_contract.status is not ContractStatus.PREREGISTERED:
            raise ValueError("only a pre-registered experiment can start")
        if started_contract.max_cost > state.remaining + 1e-9:
            raise ValueError("insufficient budget to reserve experiment cost")
        state.contracts[started_contract.id] = started_contract.model_copy(
            update={"status": ContractStatus.RUNNING}
        )
        proposal = state.proposals[started_contract.proposal_id]
        state.proposals[proposal.id] = proposal.model_copy(update={"status": "running"})
    elif kind == "experiment_recorded":
        exp = Experiment.model_validate(p["experiment"])
        recorded_evidence = EvidenceRecord.model_validate(p["evidence"])
        if state.goal is None:
            raise ValueError("project has no user goal")
        if exp.id in state.experiments or recorded_evidence.id in state.evidence:
            raise ValueError("duplicate experiment or evidence id")
        proposal = state.proposals.get(exp.proposal_id)
        if proposal is None:
            raise ValueError("experiment references an unknown proposal")
        if recorded_evidence.experiment_id != exp.id or exp.evidence_id != recorded_evidence.id:
            raise ValueError("experiment and evidence links disagree")
        if recorded_evidence.independently_verified or recorded_evidence.measurement_validated:
            raise ValueError("executor may only record unverified observations")
        if legacy:
            if proposal.status != "proposed":
                raise ValueError("legacy experiment requires an available proposal")
            if state.spent + exp.actual_cost > state.goal.budget + 1e-9:
                raise ValueError("legacy experiment exceeds budget")
        else:
            contract = state.contracts.get(exp.contract_id or "")
            if (
                contract is None
                or contract.status is not ContractStatus.RUNNING
                or contract.proposal_id != exp.proposal_id
            ):
                raise ValueError("experiment must be started from its frozen contract")
            if (
                exp.git_revision != contract.git_revision
                or exp.config_ref != contract.config_ref
                or exp.dataset_ref != contract.dataset_ref
            ):
                raise ValueError("recorded experiment disagrees with frozen configuration")
            if exp.outcome != "infrastructure_failure":
                if contract.artifact_ref not in exp.artifact_refs:
                    raise ValueError("pre-registered metric artifact was not recorded")
                if set(exp.metrics) != {r.name for r in contract.metric_rules}:
                    raise ValueError("recorded metric keys disagree with pre-registration")
            contract_status = (
                ContractStatus.BLOCKED
                if exp.outcome == "infrastructure_failure"
                else ContractStatus.AWAITING_VERIFICATION
            )
            state.contracts[contract.id] = contract.model_copy(update={"status": contract_status})
        state.experiments[exp.id] = exp
        state.evidence[recorded_evidence.id] = recorded_evidence
        proposal_status = (
            "blocked"
            if not legacy and exp.outcome == "infrastructure_failure"
            else "awaiting_verification"
        )
        state.proposals[proposal.id] = proposal.model_copy(update={"status": proposal_status})
    elif kind == "verification_recorded":
        evidence_id = str(p["evidence_id"])
        evidence = state.evidence.get(evidence_id)
        if evidence is None or evidence.independently_verified:
            raise ValueError("unknown or already verified evidence")
        if not p.get("check_argv") or not p.get("check_output_sha256"):
            raise ValueError("independent check details required")
        if legacy:
            state.evidence[evidence_id] = evidence.model_copy(
                update={
                    "independently_verified": True,
                    "check_argv": list(p["check_argv"]),
                    "check_output_sha256": str(p["check_output_sha256"]),
                    "verified_at": event.created_at,
                }
            )
            exp = state.experiments[evidence.experiment_id]
            proposal = state.proposals[exp.proposal_id]
            status = "blocked" if exp.outcome == "infrastructure_failure" else "completed"
            state.proposals[proposal.id] = proposal.model_copy(update={"status": status})
        else:
            _, exp, contract = _related_evidence(state, evidence_id)
            if contract.status is not ContractStatus.AWAITING_VERIFICATION:
                raise ValueError("experiment is not awaiting verification")
            expected_argv = [
                value.replace("{artifact}", contract.artifact_ref)
                for value in contract.verifier_argv
            ]
            if p["check_argv"] != expected_argv or p.get("exit_code") != 0:
                raise ValueError("check differs from frozen verifier or did not pass")
            if not p.get("artifact_sha256") or not p.get("check_output_sha256"):
                raise ValueError("metric artifact and verifier hashes are required")
            metrics = dict(p["validated_metrics"])
            if set(metrics) != {rule.name for rule in contract.metric_rules}:
                raise ValueError("validated metrics do not match frozen contract")
            if not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                for value in metrics.values()
            ):
                raise ValueError("validated measurements must be finite numbers")
            if any(abs(metrics[key] - exp.metrics[key]) > 1e-9 for key in metrics):
                raise ValueError("agent observations disagree with independently read measurements")
            passed = all(rule.passed(metrics[rule.name]) for rule in contract.metric_rules)
            if exp.outcome == "improved" and not passed:
                raise ValueError("reported improvement contradicts frozen metrics")
            if exp.outcome == "no_gain" and passed:
                raise ValueError("reported no-gain contradicts frozen metrics")
            verdict = (
                "inconclusive"
                if exp.outcome == "inconclusive"
                else ("supported" if passed else "refuted")
            )
            state.evidence[evidence_id] = evidence.model_copy(
                update={
                    "independently_verified": True,
                    "measurement_validated": True,
                    "scientific_verdict": verdict,
                    "validated_metrics": metrics,
                    "artifact_sha256": str(p["artifact_sha256"]),
                    "check_argv": expected_argv,
                    "check_output_sha256": str(p["check_output_sha256"]),
                    "verified_at": event.created_at,
                }
            )
            state.contracts[contract.id] = contract.model_copy(
                update={"status": ContractStatus.COMPLETED}
            )
            forecast_outcome = record_outcome(state, contract.id, evidence_id)
            if forecast_outcome is not None:
                state.decision_outcomes[contract.id] = forecast_outcome
            proposal = state.proposals[contract.proposal_id]
            state.proposals[proposal.id] = proposal.model_copy(update={"status": "completed"})
    elif kind == "verification_failed":
        evidence_id = str(p["evidence_id"])
        evidence = state.evidence.get(evidence_id)
        if evidence is None or evidence.independently_verified:
            raise ValueError("unknown or already verified evidence")
        if not legacy:
            _, _, contract = _related_evidence(state, evidence_id)
            if contract.status is not ContractStatus.AWAITING_VERIFICATION:
                raise ValueError("only an awaiting-verification experiment can fail checking")
            state.evidence[evidence_id] = evidence.model_copy(
                update={"failure_count": evidence.failure_count + 1}
            )
            state.contracts[contract.id] = contract.model_copy(
                update={"status": ContractStatus.BLOCKED}
            )
            proposal = state.proposals[contract.proposal_id]
            state.proposals[proposal.id] = proposal.model_copy(update={"status": "blocked"})
    elif kind == "verification_retried":
        contract = state.contracts.get(str(p["contract_id"]))
        reason = str(p.get("reason", "")).strip()
        if contract is None or contract.status is not ContractStatus.BLOCKED:
            raise ValueError("only a blocked verification can be retried")
        if not reason or reason in contract.retry_reasons:
            raise ValueError("retry needs a new, explicit corrective strategy")
        experiments = [x for x in state.experiments.values() if x.contract_id == contract.id]
        if len(experiments) != 1 or experiments[0].outcome == "infrastructure_failure":
            raise ValueError("execution failure needs a newly pre-registered experiment")
        evidence = state.evidence[experiments[0].evidence_id]
        if evidence.failure_count >= 3:
            raise ValueError("repeated failures require a new proposal and experiment")
        state.contracts[contract.id] = contract.model_copy(
            update={
                "status": ContractStatus.AWAITING_VERIFICATION,
                "retry_reasons": [*contract.retry_reasons, reason],
            }
        )
        proposal = state.proposals[contract.proposal_id]
        state.proposals[proposal.id] = proposal.model_copy(
            update={"status": "awaiting_verification"}
        )
    elif kind == "experiment_aborted":
        contract = state.contracts.get(str(p["contract_id"]))
        cost = float(p["actual_cost"])
        if not math.isfinite(cost) or cost < 0 or not str(p.get("reason", "")).strip():
            raise ValueError("aborting an experiment needs actual cost and a reason")
        if contract is None or contract.status not in {
            ContractStatus.PREREGISTERED,
            ContractStatus.RUNNING,
        }:
            raise ValueError("only a pre-registered or running experiment can be aborted")
        state.contracts[contract.id] = contract.model_copy(
            update={"status": ContractStatus.ABORTED, "aborted_cost": cost}
        )
        proposal = state.proposals[contract.proposal_id]
        state.proposals[proposal.id] = proposal.model_copy(update={"status": "aborted"})
    elif kind == "claim_reviewed":
        claim = state.claims.get(str(p["claim_id"]))
        if claim is None:
            raise ValueError("unknown claim")
        evidence_id = str(p["evidence_id"])
        evidence = state.evidence.get(evidence_id)
        if evidence is None or not evidence.independently_verified:
            raise ValueError("claim review requires independently checked evidence")
        verdict = ClaimStatus(str(p["status"]))
        if verdict not in {ClaimStatus.SUPPORTED, ClaimStatus.REFUTED, ClaimStatus.INCONCLUSIVE}:
            raise ValueError("unsupported claim review status")
        if not legacy:
            _, _, contract = _related_evidence(state, evidence_id)
            proposal = state.proposals[contract.proposal_id]
            if (
                not evidence.measurement_validated
                or claim.id not in proposal.claim_ids
                or evidence.scientific_verdict != verdict.value
                or not str(p.get("rationale", "")).strip()
            ):
                raise ValueError("claim verdict must match validated, linked experiment")
        support = list(claim.supporting_evidence_ids)
        contradict = list(claim.contradicting_evidence_ids)
        inconclusive = list(claim.inconclusive_evidence_ids)
        if evidence_id in support or evidence_id in contradict or evidence_id in inconclusive:
            raise ValueError("evidence already reviewed for this claim")
        if verdict == ClaimStatus.SUPPORTED:
            support.append(evidence_id)
        elif verdict == ClaimStatus.REFUTED:
            contradict.append(evidence_id)
        else:
            inconclusive.append(evidence_id)
        next_status = verdict
        if support and contradict:
            next_status = ClaimStatus.INCONCLUSIVE
        state.claims[claim.id] = claim.model_copy(
            update={
                "status": next_status,
                "supporting_evidence_ids": support,
                "contradicting_evidence_ids": contradict,
                "inconclusive_evidence_ids": inconclusive,
            }
        )
    elif kind == "unknown_resolved":
        unresolved = state.unknowns.get(str(p["unknown_id"]))
        evidence_id = str(p["evidence_id"])
        if unresolved is None or unresolved.status != "open":
            raise ValueError("unknown is missing or already resolved")
        evidence = state.evidence.get(evidence_id)
        if evidence is None or not evidence.independently_verified:
            raise ValueError("resolving unknown requires independently checked evidence")
        if not legacy:
            _, _, contract = _related_evidence(state, evidence_id)
            proposal = state.proposals[contract.proposal_id]
            if (
                unresolved.id not in proposal.unknown_ids
                or not evidence.measurement_validated
                or evidence.scientific_verdict == "inconclusive"
                or not str(p.get("rationale", "")).strip()
                or not proposal.claim_ids
                or any(
                    state.claims[cid].status not in {ClaimStatus.SUPPORTED, ClaimStatus.REFUTED}
                    or evidence_id
                    not in (
                        state.claims[cid].supporting_evidence_ids
                        + state.claims[cid].contradicting_evidence_ids
                    )
                    for cid in proposal.claim_ids
                )
            ):
                raise ValueError("unknown resolution needs linked, conclusive reviewed claims")
        state.unknowns[unresolved.id] = unresolved.model_copy(
            update={"status": "resolved", "resolution_evidence_ids": [evidence_id]}
        )
    elif kind == "onboarding_recorded":
        state.onboarding.append(OnboardingSnapshot.model_validate(p))
    else:
        raise ValueError(f"unrecognized research event: {kind}")


class ResearchStore:
    """Repository-local stream; one writer under a filesystem create-exclusive lock."""

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
                "research store is locked; check active writer before removing .write.lock"
            ) from exc
        try:
            os.close(fd)
            yield
        finally:
            self.lock_path.unlink()

    def append(self, kind: str, payload: dict[str, Any]) -> ResearchEvent:
        """Check the complete transition before writing a flushed, durable event."""
        with self._writer_lock():
            old_state, sequence = self.load()
            event = ResearchEvent(sequence=sequence + 1, kind=kind, payload=payload)
            trial = old_state.model_copy(deep=True)
            _apply(trial, event)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return event

    def add(
        self,
        kind: str,
        record: ResearchGoal | Unknown | Claim | Proposal | OnboardingSnapshot | ExperimentContract,
    ) -> ResearchEvent:
        return self.append(kind, record.model_dump(mode="json"))
