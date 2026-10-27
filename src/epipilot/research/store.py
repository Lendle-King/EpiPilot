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
from epipilot.research.exploration import ensure_action_allowed, pending_research, readiness
from epipilot.research.models import (
    Claim,
    ClaimStatus,
    ContractStatus,
    EvidenceRecord,
    Experiment,
    ExperimentContract,
    ExplorationConfig,
    ExplorationStage,
    OnboardingSnapshot,
    ProblemInsight,
    Proposal,
    ProposalKind,
    ResearchEvent,
    ResearchGoal,
    ResearchState,
    RunLaunch,
    RunReceipt,
    Unknown,
)
from epipilot.research.scout_core import frontier_fingerprint
from epipilot.research.scout_models import (
    ScoutCandidateStatus,
    ScoutOperation,
    ScoutStatus,
    ScoutSubmission,
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
    elif kind == "exploration_enabled":
        config = ExplorationConfig.model_validate(p)
        if legacy or state.goal is None or not config.enabled:
            raise ValueError("exploration enable requires a v2 goal and enabled config")
        if config.stage is not ExplorationStage.EXPLORATION:
            raise ValueError("exploration enable must begin with the discovery stage")
        if state.exploration is not None and state.exploration.enabled:
            raise ValueError("exploration mode is already enabled")
        if pending_research(state):
            raise ValueError("finish in-flight research before changing exploration mode")
        state.exploration = config
    elif kind == "exploration_disabled":
        reason = str(p.get("reason", "")).strip()
        if state.exploration is None or not state.exploration.enabled or len(reason) < 8:
            raise ValueError("exploration disable requires active mode and a reason")
        if pending_research(state):
            raise ValueError("finish in-flight research before disabling exploration")
        state.exploration = state.exploration.model_copy(
            update={"enabled": False, "reason": reason}
        )
    elif kind == "exploration_advanced":
        config = state.exploration
        reason = str(p.get("reason", "")).strip()
        override = p.get("override") is True
        if config is None or not config.enabled or len(reason) < 8:
            raise ValueError("exploration advance requires active mode and a reason")
        if pending_research(state):
            raise ValueError("finish in-flight research before leaving exploration")
        if not readiness(state).get("ready") and not override:
            raise ValueError("exploration is not ready; explicit --override is required")
        reopened = sorted(
            {eid for unknown in state.unknowns.values() for eid in unknown.reopening_evidence_ids}
        )
        state.exploration = config.model_copy(
            update={
                "stage": ExplorationStage.OPTIMIZATION,
                "reason": reason,
                "acknowledged_reopenings": reopened,
            }
        )
    elif kind == "exploration_reopened":
        config = state.exploration
        reason = str(p.get("reason", "")).strip()
        if (
            config is None
            or not config.enabled
            or config.stage is not ExplorationStage.OPTIMIZATION
            or len(reason) < 8
        ):
            raise ValueError("reopening requires optimization stage and a reason")
        if pending_research(state):
            raise ValueError("finish in-flight research before reopening exploration")
        state.exploration = config.model_copy(
            update={"stage": ExplorationStage.EXPLORATION, "reason": reason}
        )
    elif kind == "problem_insight_recorded":
        insight = ProblemInsight.model_validate(p)
        if legacy or state.exploration is None or not state.exploration.enabled:
            raise ValueError("problem guidance requires active exploration mode")
        if insight.id in state.problem_insights:
            raise ValueError("duplicate problem insight id")
        sources: set[str] = set()
        for claim_id, status in insight.claim_statuses.items():
            claim = state.claims.get(claim_id)
            if (
                claim is None
                or claim.status is not status
                or status not in {ClaimStatus.SUPPORTED, ClaimStatus.REFUTED}
            ):
                raise ValueError("problem guidance requires conclusive, reviewed claims")
            sources.update(claim.supporting_evidence_ids + claim.contradicting_evidence_ids)
        if set(insight.evidence_ids) != sources or len(insight.evidence_ids) != len(sources):
            raise ValueError("problem guidance must use exactly the reviewed claim evidence")
        if not sources or any(
            eid not in state.evidence or not state.evidence[eid].measurement_validated
            for eid in sources
        ):
            raise ValueError("problem guidance requires qualified experimental evidence")
        if any(
            existing.title.casefold() == insight.title.casefold()
            and existing.dimension is insight.dimension
            and existing.claim_statuses == insight.claim_statuses
            for existing in state.problem_insights.values()
        ):
            raise ValueError("duplicate problem guidance")
        state.problem_insights[insight.id] = insight
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
    elif kind == "scout_started":
        if legacy or state.goal is None:
            raise ValueError("Scout requires a v2 research goal")
        operation = ScoutOperation.model_validate(p)
        if (
            operation.id in state.scout_runs
            or operation.based_on_version != event.sequence - 1
            or operation.status is not ScoutStatus.RUNNING
            or operation.actual_cost != 0
            or operation.candidate_ids
        ):
            raise ValueError("Scout requires a fresh reserved operation and exact state version")
        if operation.epoch != frontier_fingerprint(state):
            raise ValueError("Scout research epoch differs from the current scientific frontier")
        if any(run.status is ScoutStatus.RUNNING for run in state.scout_runs.values()):
            raise ValueError("finish the prior Scout before starting another")
        if any(
            run.epoch == operation.epoch and run.git_revision == operation.git_revision
            for run in state.scout_runs.values()
        ):
            raise ValueError("unchanged Scout frontier already explored; review evidence first")
        if operation.reserved_cost > state.remaining + 1e-9:
            raise ValueError("Scout reservation exceeds remaining experiment budget")
        if (
            state.exploration is not None
            and state.exploration.enabled
            and operation.reserved_cost > state.exploration_remaining + 1e-9
        ):
            raise ValueError("Scout reservation exceeds exploration budget cap")
        state.scout_runs[operation.id] = operation
    elif kind == "scout_submitted":
        if legacy:
            raise ValueError("Scout submissions require v2 events")
        submission = ScoutSubmission.model_validate(p)
        submitted_scout = state.scout_runs.get(submission.operation_id)
        if submitted_scout is None or submitted_scout.status is not ScoutStatus.RUNNING:
            raise ValueError("Scout submission requires a running reserved submitted_scout")
        if len(submission.candidates) > submitted_scout.max_candidates:
            raise ValueError("Scout exceeded its frozen candidate limit")
        paths = {candidate.source.path for candidate in submission.candidates}
        if len(paths) > submitted_scout.max_sources:
            raise ValueError("Scout exceeded its frozen source limit")
        ids = [candidate.id for candidate in submission.candidates]
        if len(ids) != len(set(ids)) or any(cid in state.scout_candidates for cid in ids):
            raise ValueError("Scout candidate IDs must be unique across research history")
        for candidate in submission.candidates:
            state.scout_candidates[candidate.id] = candidate
        next_status = (
            ScoutStatus.COMPLETED if submission.status == "completed" else ScoutStatus.FAILED
        )
        state.scout_runs[submitted_scout.id] = submitted_scout.model_copy(
            update={
                "status": next_status,
                "actual_cost": submission.actual_cost,
                "candidate_ids": ids,
                "summary": submission.summary,
            }
        )
    elif kind == "scout_candidate_reviewed":
        if legacy:
            raise ValueError("Scout review requires a v2 event")
        reviewed_lead = state.scout_candidates.get(str(p["candidate_id"]))
        decision = ScoutCandidateStatus(str(p["decision"]))
        rationale = str(p.get("rationale", "")).strip()
        if (
            reviewed_lead is None
            or reviewed_lead.status is not ScoutCandidateStatus.PENDING
            or decision is ScoutCandidateStatus.PENDING
            or len(rationale) < 8
        ):
            raise ValueError("Scout review requires a pending reviewed_lead and explicit reasoning")
        if decision is ScoutCandidateStatus.ACCEPTED:
            if any(
                existing.question.casefold() == reviewed_lead.question.casefold()
                for existing in state.unknowns.values()
            ):
                raise ValueError("Scout question duplicates an existing research unknown")
            if any(
                existing.statement.casefold() == reviewed_lead.hypothesis.casefold()
                and existing.scope.casefold() == reviewed_lead.scope.casefold()
                for existing in state.claims.values()
            ):
                raise ValueError("Scout hypothesis duplicates an existing scoped claim")
            if any(
                existing.title.casefold() == reviewed_lead.title.casefold()
                and existing.status != "aborted"
                for existing in state.proposals.values()
            ):
                raise ValueError("Scout proposal duplicates an existing experiment")
            unknown_id, claim_id, proposal_id = (
                f"UNK-{reviewed_lead.id}",
                f"CLM-{reviewed_lead.id}",
                f"ACT-{reviewed_lead.id}",
            )
            if (
                unknown_id in state.unknowns
                or claim_id in state.claims
                or proposal_id in state.proposals
            ):
                raise ValueError("Scout-generated stable IDs collide with existing research IDs")
            source_operation = state.scout_runs[reviewed_lead.operation_id]
            source = reviewed_lead.source
            anchor = (
                f"scout_source=git:{source_operation.git_revision}:{source.path}:{source.line}"
                f";sha256={source.sha256};source_text_is_not_scientific_evidence"
            )
            state.unknowns[unknown_id] = Unknown(
                id=unknown_id,
                question=reviewed_lead.question,
                importance=reviewed_lead.importance,
                decision_sensitivity=reviewed_lead.decision_sensitivity,
            )
            state.claims[claim_id] = Claim(
                id=claim_id,
                statement=reviewed_lead.hypothesis,
                scope=reviewed_lead.scope,
                assumptions=[anchor],
            )
            state.proposals[proposal_id] = Proposal(
                id=proposal_id,
                title=reviewed_lead.title,
                kind=ProposalKind(reviewed_lead.kind),
                rationale=reviewed_lead.rationale,
                acceptance=reviewed_lead.acceptance,
                unknown_ids=[unknown_id],
                claim_ids=[claim_id],
                information_gain=reviewed_lead.information_gain,
                expected_improvement=reviewed_lead.expected_improvement,
                estimated_cost=reviewed_lead.estimated_cost,
                risk=reviewed_lead.risk,
            )
        state.scout_candidates[reviewed_lead.id] = reviewed_lead.model_copy(
            update={"status": decision, "review_rationale": rationale}
        )
    elif kind == "experiment_preregistered":
        registration = ExperimentContract.model_validate(p)
        if state.goal is None:
            raise ValueError("research goal required before pre-registration")
        if registration.id in state.contracts:
            raise ValueError("duplicate experiment contract")
        proposal = state.proposals.get(registration.proposal_id)
        if proposal is None or proposal.status != "proposed":
            raise ValueError("pre-registration requires an available proposal")
        if not legacy:
            ensure_action_allowed(state, proposal)
            if (
                state.exploration is not None
                and state.exploration.enabled
                and proposal.kind is ProposalKind.EXPLORE
                and registration.max_cost > state.exploration_remaining + 1e-9
            ):
                raise ValueError("experiment pre-registration exceeds exploration budget cap")
        if any(state.proposals[parent].status != "completed" for parent in proposal.depends_on):
            raise ValueError("pre-registration dependencies not completed")
        if not proposal.unknown_ids and not proposal.claim_ids:
            raise ValueError("pre-registration must link a claim or a research unknown")
        if registration.max_cost > state.remaining + 1e-9:
            raise ValueError("pre-registered experiment exceeds remaining budget")
        registered_estimate = state.decision_estimates.get(proposal.id)
        if registered_estimate is not None:
            frozen_score = rank_decision(state, registered_estimate)
            state.decision_forecasts[registration.id] = DecisionForecast(
                contract_id=registration.id,
                git_revision=registration.git_revision,
                recorded_at=event.created_at,
                estimate=registered_estimate,
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
        proposal = state.proposals[started_contract.proposal_id]
        if not legacy:
            ensure_action_allowed(state, proposal)
            if (
                state.exploration is not None
                and state.exploration.enabled
                and proposal.kind is ProposalKind.EXPLORE
                and started_contract.max_cost > state.exploration_remaining + 1e-9
            ):
                raise ValueError("insufficient exploration budget to start experiment")
        state.contracts[started_contract.id] = started_contract.model_copy(
            update={"status": ContractStatus.RUNNING}
        )
        proposal = state.proposals[started_contract.proposal_id]
        state.proposals[proposal.id] = proposal.model_copy(update={"status": "running"})
    elif kind == "runner_started":
        if legacy:
            raise ValueError("controlled runs require v2")
        launch = RunLaunch.model_validate(p)
        contract = state.contracts.get(launch.contract_id)
        if (
            contract is None
            or contract.status is not ContractStatus.RUNNING
            or not contract.runner_argv
            or launch.contract_id in state.run_launches
        ):
            raise ValueError("controlled runner requires an unlaunched, running contract")
        state.run_launches[launch.contract_id] = launch
    elif kind == "runner_finished":
        if legacy:
            raise ValueError("controlled runs require v2")
        finished_receipt = RunReceipt.model_validate(p)
        contract = state.contracts.get(finished_receipt.contract_id)
        finished_launch = state.run_launches.get(finished_receipt.contract_id)
        if (
            contract is None
            or contract.status is not ContractStatus.RUNNING
            or finished_launch is None
            or finished_launch.id != finished_receipt.id
            or finished_launch.started_at != finished_receipt.started_at
            or finished_receipt.contract_id in state.run_receipts
            or finished_receipt.argv != contract.runner_argv
            or finished_receipt.source_revision != contract.git_revision
        ):
            raise ValueError("runner receipt must match one predeclared, started command")
        state.run_receipts[finished_receipt.contract_id] = finished_receipt
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
            if contract.runner_argv:
                recorded_run = state.run_receipts.get(contract.id)
                if recorded_run is None or recorded_run.id != exp.run_receipt_id:
                    raise ValueError("controlled experiment needs its matching run receipt")
                if exp.outcome != "infrastructure_failure" and (
                    recorded_run.exit_code != 0 or recorded_run.artifact_sha256 is None
                ):
                    raise ValueError("successful controlled experiment needs a successful run")
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
            if contract.runner_argv:
                verified_run = state.run_receipts.get(contract.id)
                if verified_run is None or verified_run.artifact_sha256 != p["artifact_sha256"]:
                    raise ValueError("artifact differs from the controlled run receipt")
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
        claim_status = verdict
        if support and contradict:
            claim_status = ClaimStatus.INCONCLUSIVE
        state.claims[claim.id] = claim.model_copy(
            update={
                "status": claim_status,
                "supporting_evidence_ids": support,
                "contradicting_evidence_ids": contradict,
                "inconclusive_evidence_ids": inconclusive,
            }
        )
        # A later counterexample must invalidate conclusions that relied on
        # this claim. Keep original resolution provenance and record which
        # newly reviewed evidence forced the Unknown back into the frontier.
        # Do not retroactively reinterpret schema-v1 historical streams.
        if not legacy and claim_status is ClaimStatus.INCONCLUSIVE:
            for unknown_id, resolved_unknown in state.unknowns.items():
                if resolved_unknown.status != "resolved":
                    continue
                for resolution_id in resolved_unknown.resolution_evidence_ids:
                    old_evidence = state.evidence.get(resolution_id)
                    if old_evidence is None:
                        continue
                    old_experiment = state.experiments.get(old_evidence.experiment_id)
                    if old_experiment is None:
                        continue
                    old_proposal = state.proposals.get(old_experiment.proposal_id)
                    if old_proposal is None or claim.id not in old_proposal.claim_ids:
                        continue
                    state.unknowns[unknown_id] = resolved_unknown.model_copy(
                        update={
                            "status": "open",
                            "reopening_evidence_ids": [
                                *resolved_unknown.reopening_evidence_ids,
                                evidence_id,
                            ],
                        }
                    )
                    break
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
        record: ResearchGoal
        | Unknown
        | Claim
        | Proposal
        | OnboardingSnapshot
        | ExperimentContract
        | ScoutOperation
        | RunLaunch
        | RunReceipt,
    ) -> ResearchEvent:
        return self.append(kind, record.model_dump(mode="json"))
