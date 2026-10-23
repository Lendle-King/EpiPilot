"""Agent-native EpiPilot CLI: bounded research iteration with validated evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from epipilot.research.models import (
    Claim,
    ClaimStatus,
    ContractStatus,
    EvidenceRecord,
    Experiment,
    ExperimentContract,
    MetricDirection,
    MetricRule,
    Proposal,
    ProposalKind,
    ResearchGoal,
    ResearchState,
    Unknown,
    research_id,
)
from epipilot.research.onboarding import inspect_repository
from epipilot.research.planner import rank_proposals, research_phase
from epipilot.research.store import ResearchStore
from epipilot.research.validation import (
    artifact_metrics,
    check_fingerprints,
    optional_fingerprint,
    require_clean_revision,
    run_frozen_verifier,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="epi", description="Evidence-gated, cost-aware research for coding agents"
    )
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="target Git research repository"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="record an explicitly user-approved goal and budget")
    init.add_argument("--goal", required=True)
    init.add_argument("--budget", type=float, required=True)
    init.add_argument("--metric", action="append", default=[])
    init.add_argument("--constraint", action="append", default=[])

    sub.add_parser("onboard", help="inventory existing repository and experiment reports")
    sub.add_parser("status", help="show goal, reserved and spent budget, and research frontier")
    sub.add_parser("review", help="review all claims and research frontier")
    sub.add_parser("doctor", help="replay the event stream and diagnose pending work")

    unknown = sub.add_parser("unknown", help="add or resolve a research unknown")
    unknown_sub = unknown.add_subparsers(dest="action", required=True)
    add_unknown = unknown_sub.add_parser("add")
    add_unknown.add_argument("--id")
    add_unknown.add_argument("--question", required=True)
    add_unknown.add_argument("--importance", type=float, default=0.5)
    add_unknown.add_argument("--sensitivity", type=float, default=0.5)
    resolve_unknown = unknown_sub.add_parser("resolve")
    resolve_unknown.add_argument("--id", required=True)
    resolve_unknown.add_argument("--evidence", required=True)
    resolve_unknown.add_argument("--rationale", required=True)

    claim = sub.add_parser("claim", help="register a scoped, falsifiable claim")
    claim.add_argument("--id")
    claim.add_argument("--statement", required=True)
    claim.add_argument("--scope", required=True)
    claim.add_argument("--assumption", action="append", default=[])

    propose = sub.add_parser("propose", help="register a candidate research action")
    propose.add_argument("--id")
    propose.add_argument("--title", required=True)
    propose.add_argument("--kind", choices=[item.value for item in ProposalKind], required=True)
    propose.add_argument("--rationale", required=True)
    propose.add_argument("--acceptance", required=True)
    propose.add_argument("--unknown", action="append", default=[])
    propose.add_argument("--claim", action="append", default=[])
    propose.add_argument("--depends-on", action="append", default=[])
    propose.add_argument("--ig", type=float, required=True)
    propose.add_argument("--gain", type=float, required=True)
    propose.add_argument("--unlock", type=float, default=0.0)
    propose.add_argument("--novelty", type=float, default=0.0)
    propose.add_argument("--cost", type=float, required=True)
    propose.add_argument("--risk", type=float, default=0.0)

    next_cmd = sub.add_parser("next", help="rank budget-feasible candidate experiments")
    next_cmd.add_argument("--limit", type=int, default=5)
    plan = sub.add_parser("plan", help="compile context for a proposal")
    plan.add_argument("--id", required=True)
    sub.add_parser("iterate", help="return the next blocked, pending or runnable action")

    prereg = sub.add_parser("preregister", help="freeze experiment protocol before execution")
    prereg.add_argument("--proposal", required=True)
    prereg.add_argument("--baseline", required=True)
    prereg.add_argument("--config", required=True)
    prereg.add_argument("--dataset", required=True)
    prereg.add_argument("--control", action="append", required=True)
    prereg.add_argument("--decision-rule", required=True)
    prereg.add_argument("--falsification", required=True)
    prereg.add_argument(
        "--metric-rule", action="append", required=True, help="name:at_least|at_most:threshold"
    )
    prereg.add_argument("--artifact", required=True, help="repository-relative JSON metric path")
    prereg.add_argument("--max-cost", type=float, required=True)
    prereg.add_argument(
        "--verifier",
        nargs=argparse.REMAINDER,
        required=True,
        help="frozen argv; use {artifact} to reference the JSON result",
    )

    start = sub.add_parser("start", help="reserve cost before running a frozen experiment")
    start.add_argument("--contract", required=True)

    record = sub.add_parser("record", help="record a real observation from a started experiment")
    record.add_argument("--contract", required=True)
    record.add_argument("--summary", required=True)
    record.add_argument(
        "--outcome",
        choices=["improved", "no_gain", "inconclusive", "infrastructure_failure"],
        required=True,
    )
    record.add_argument("--cost", type=float, required=True)
    record.add_argument("--seed", type=int, action="append", default=[])
    record.add_argument("--metric", action="append", default=[], help="name=numeric_value")
    record.add_argument("--artifact", action="append", default=[])
    record.add_argument("--run-command", default="")

    verify = sub.add_parser("verify", help="run frozen checker and validate metric artifact")
    verify.add_argument("--experiment", required=True)
    verify.add_argument("--timeout", type=int, default=120)
    verify.add_argument(
        "--check",
        nargs=argparse.REMAINDER,
        help="optional: must match the preregistered verifier exactly",
    )

    retry = sub.add_parser("retry", help="resume failed validation after an explicit correction")
    retry.add_argument("--contract", required=True)
    retry.add_argument("--reason", required=True)

    abort = sub.add_parser("abort", help="stop running experiment, recording incurred cost")
    abort.add_argument("--contract", required=True)
    abort.add_argument("--reason", required=True)
    abort.add_argument("--cost", type=float, required=True)

    reflect = sub.add_parser("reflect", help="apply a linked scientific verdict after validation")
    reflect.add_argument("--claim", required=True)
    reflect.add_argument(
        "--verdict", choices=["supported", "refuted", "inconclusive"], required=True
    )
    reflect.add_argument("--evidence", required=True)
    reflect.add_argument("--rationale", required=True)
    return parser


def _emit(result: object) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _status(state: ResearchState) -> dict[str, object]:
    return {
        "goal": None if state.goal is None else state.goal.statement,
        "budget": None if state.goal is None else state.goal.budget,
        "spent": state.spent,
        "reserved": state.reserved,
        "remaining": state.remaining,
        "overrun": state.overrun,
        "phase": research_phase(state),
        "unknowns_open": sum(item.status == "open" for item in state.unknowns.values()),
        "claims": len(state.claims),
        "proposals": len(state.proposals),
        "contracts": len(state.contracts),
        "experiments": len(state.experiments),
        "validated_evidence": sum(item.measurement_validated for item in state.evidence.values()),
        "pending": {
            key: item.status.value
            for key, item in state.contracts.items()
            if item.status not in {ContractStatus.COMPLETED, ContractStatus.ABORTED}
        },
    }


def _plan(state: ResearchState, proposal_id: str) -> dict[str, object]:
    proposal = state.proposals.get(proposal_id)
    if proposal is None or proposal.status != "proposed":
        raise ValueError("proposal is not currently available")
    if proposal.estimated_cost > state.remaining:
        raise ValueError("proposal exceeds remaining budget")
    if any(state.proposals[parent].status != "completed" for parent in proposal.depends_on):
        raise ValueError("proposal dependencies are not independently checked")
    return {
        "proposal": proposal.model_dump(mode="json"),
        "goal": None if state.goal is None else state.goal.model_dump(mode="json"),
        "related_unknowns": [
            state.unknowns[key].model_dump(mode="json") for key in proposal.unknown_ids
        ],
        "related_claims": [state.claims[key].model_dump(mode="json") for key in proposal.claim_ids],
        "budget_remaining": state.remaining,
        "agent_instructions": [
            "Inspect code, past experiment data and contradictory claims before starting.",
            "Freeze revision, data, controls, metric rule and verifier with epi preregister.",
            "Reserve experiment budget with epi start before using resources.",
            "Run the experiment using the coding agent's normal tool/approval boundary.",
            "Record actual cost, measured metrics, seeds and artifact paths with epi record.",
            "Run epi verify; its frozen checker cannot be replaced after observing metrics.",
            "Use epi reflect/unknown resolve only with linked conclusive validated evidence.",
            "After failures, record corrective action via epi retry or create a new proposal.",
        ],
    }


def _parse_metrics(items: list[str]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for item in items:
        name, sep, val = item.partition("=")
        if not sep or not name or name in parsed:
            raise ValueError("metrics must be distinct name=value pairs")
        try:
            number = float(val)
        except ValueError as exc:
            raise ValueError("metrics must have numeric values") from exc
        if number == float("inf") or number == -float("inf") or number != number:
            raise ValueError("metric values must be finite")
        parsed[name] = number
    return parsed


def _metric_rules(items: list[str]) -> list[MetricRule]:
    rules: list[MetricRule] = []
    for item in items:
        parts = item.rsplit(":", 2)
        if len(parts) != 3:
            raise ValueError("metric-rule must be name:at_least|at_most:threshold")
        name, direction, threshold = parts
        rules.append(
            MetricRule(name=name, direction=MetricDirection(direction), threshold=float(threshold))
        )
    return rules


def _safe_verifier(parts: list[str]) -> list[str]:
    argv = list(parts)
    if argv and argv[0] == "--":
        argv.pop(0)
    if not argv:
        raise ValueError("a real verifier command must be frozen before experiment execution")
    executable = Path(argv[0]).name.lower()
    if executable in {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"}:
        raise ValueError("shell wrappers are not accepted as independent check commands")
    if executable.startswith("python") and "-c" in argv:
        raise ValueError("inline python -c is not an auditable independent verifier")
    if "{artifact}" not in " ".join(argv):
        raise ValueError("verifier must receive the frozen {artifact} path")
    return argv


def _next_action(state: ResearchState) -> dict[str, object]:
    """Prioritize resuming one in-flight run over launching additional experiments."""
    for status, label, guidance in (
        (ContractStatus.RUNNING, "running", "Resume or abort the in-flight experiment."),
        (ContractStatus.AWAITING_VERIFICATION, "verify", "Run epi verify on its observation."),
        (ContractStatus.PREREGISTERED, "start", "Reserve cost using epi start."),
    ):
        for item in state.contracts.values():
            if item.status is status:
                return {
                    "status": label,
                    "contract": item.model_dump(mode="json"),
                    "guidance": guidance,
                }
    ranked = rank_proposals(state, limit=1)
    if ranked:
        return {
            "status": "ready_for_agent",
            "task": _plan(state, ranked[0].proposal.id),
            "continuation": "Preregister before execution; update memory after verification.",
        }
    blocked = [
        item.id for item in state.contracts.values() if item.status is ContractStatus.BLOCKED
    ]
    if blocked:
        return {
            "status": "blocked",
            "contracts": blocked,
            "guidance": "Diagnose failure; epi retry --reason for changed validation strategy "
            "or register a new scientifically different proposal.",
        }
    return {
        "status": "no_runnable_proposal",
        "guidance": "Review cost, unresolved unknowns and concluded evidence; "
        "stop when no decision-relevant improvement justifies expense.",
        **_status(state),
    }


def _verify(
    args: argparse.Namespace, store: ResearchStore, root: Path, state: ResearchState
) -> dict[str, object]:
    exp = state.experiments.get(args.experiment)
    if exp is None or exp.contract_id is None:
        raise ValueError("verification requires a recorded, pre-registered v2 experiment")
    contract = state.contracts[exp.contract_id]
    if contract.status is not ContractStatus.AWAITING_VERIFICATION:
        raise ValueError("experiment is not awaiting verification; use epi retry if blocked")
    evidence = state.evidence[exp.evidence_id]
    if evidence.independently_verified:
        raise ValueError("evidence has already been validated")
    expected_argv = [
        item.replace("{artifact}", contract.artifact_ref) for item in contract.verifier_argv
    ]
    if args.check is not None:
        requested = list(args.check)
        if requested and requested[0] == "--":
            requested.pop(0)
        if requested != expected_argv:
            raise ValueError("cannot replace preregistered verifier after observing results")
    try:
        require_clean_revision(root, contract.git_revision)
        check_fingerprints(root, contract)
        metrics, artifact_sha = artifact_metrics(root, contract)
        if set(metrics) != set(exp.metrics) or any(
            abs(metrics[key] - exp.metrics[key]) > 1e-9 for key in metrics
        ):
            raise ValueError("the recorded observations differ from metric artifact")
        passed, digest, exit_code, argv = run_frozen_verifier(root, contract, args.timeout)
        if not passed:
            raise ValueError(f"frozen verifier exited with code {exit_code}")
        result = store.append(
            "verification_recorded",
            {
                "evidence_id": evidence.id,
                "check_argv": argv,
                "check_output_sha256": digest,
                "artifact_sha256": artifact_sha,
                "validated_metrics": metrics,
                "exit_code": exit_code,
            },
        )
        new_state, _ = store.load()
        checked = new_state.evidence[evidence.id]
        return {
            "check_passed": True,
            "event_sequence": result.sequence,
            "evidence_id": evidence.id,
            "scientific_verdict": checked.scientific_verdict,
            "validated_metrics": checked.validated_metrics,
            "artifact_sha256": artifact_sha,
            "warning": "Metric thresholds are protocol checks, not proof of causality.",
        }
    except (ValueError, OSError) as exc:
        store.append("verification_failed", {"evidence_id": evidence.id, "reason": str(exc)})
        return {
            "check_passed": False,
            "evidence_id": evidence.id,
            "reason": str(exc),
            "next": f"epi retry --contract {contract.id} --reason CORRECTIVE_ACTION",
        }


def _dispatch(args: argparse.Namespace, store: ResearchStore, root: Path) -> object:
    state, sequence = store.load()
    command: str = args.command
    if command == "init":
        goal = ResearchGoal(
            statement=args.goal,
            budget=args.budget,
            success_metrics=args.metric,
            constraints=args.constraint,
        )
        store.add("goal_set", goal)
        return {"initialized": True, "goal": goal.model_dump(mode="json")}
    if command == "onboard":
        snapshot = inspect_repository(root)
        store.add("onboarding_recorded", snapshot)
        return {
            "onboarding": snapshot.model_dump(mode="json"),
            "warning": "File inventory is not evidence; inspect historical experiments.",
        }
    if command == "status":
        return _status(state)
    if command == "doctor":
        return {
            "event_count": sequence,
            "stream_valid": True,
            "legacy_evidence": sum(
                item.independently_verified and not item.measurement_validated
                for item in state.evidence.values()
            ),
            "write_lock_present": store.lock_path.exists(),
            **_status(state),
        }
    if command == "review":
        return {
            **_status(state),
            "unknowns": [x.model_dump(mode="json") for x in state.unknowns.values()],
            "claims": [x.model_dump(mode="json") for x in state.claims.values()],
            "recent_evidence": [
                x.model_dump(mode="json") for x in list(state.evidence.values())[-10:]
            ],
            "top_proposals": [
                {"id": x.proposal.id, "score": x.score, "reason": x.rationale}
                for x in rank_proposals(state, limit=5)
            ],
        }
    if command == "unknown":
        if args.action == "add":
            new_unknown = Unknown(
                id=args.id or research_id("UNK"),
                question=args.question,
                importance=args.importance,
                decision_sensitivity=args.sensitivity,
            )
            store.add("unknown_added", new_unknown)
            return new_unknown.model_dump(mode="json")
        store.append(
            "unknown_resolved",
            {"unknown_id": args.id, "evidence_id": args.evidence, "rationale": args.rationale},
        )
        return {"resolved": args.id, "evidence_id": args.evidence}
    if command == "claim":
        new_claim = Claim(
            id=args.id or research_id("CLM"),
            statement=args.statement,
            scope=args.scope,
            assumptions=args.assumption,
        )
        store.add("claim_added", new_claim)
        return new_claim.model_dump(mode="json")
    if command == "propose":
        new_proposal = Proposal(
            id=args.id or research_id("ACT"),
            title=args.title,
            kind=ProposalKind(args.kind),
            rationale=args.rationale,
            acceptance=args.acceptance,
            unknown_ids=args.unknown,
            claim_ids=args.claim,
            depends_on=args.depends_on,
            information_gain=args.ig,
            expected_improvement=args.gain,
            unlock_value=args.unlock,
            novelty=args.novelty,
            estimated_cost=args.cost,
            risk=args.risk,
        )
        store.add("proposal_added", new_proposal)
        return new_proposal.model_dump(mode="json")
    if command == "next":
        return [
            {
                "proposal_id": item.proposal.id,
                "title": item.proposal.title,
                "score": item.score,
                "phase": item.phase,
                "reason": item.rationale,
                "cost": item.proposal.estimated_cost,
            }
            for item in rank_proposals(state, limit=args.limit)
        ]
    if command == "plan":
        return _plan(state, args.id)
    if command == "iterate":
        return _next_action(state)
    if command == "preregister":
        revision = require_clean_revision(root)
        verifier = _safe_verifier(args.verifier)
        proposal = state.proposals.get(args.proposal)
        if proposal is None:
            raise ValueError("unknown proposal")
        if args.max_cost < proposal.estimated_cost:
            raise ValueError("max-cost must cover the proposal's estimated cost")
        registration = ExperimentContract(
            id=research_id("PRE"),
            proposal_id=args.proposal,
            git_revision=revision,
            config_ref=args.config,
            dataset_ref=args.dataset,
            config_sha256=optional_fingerprint(root, args.config),
            dataset_sha256=optional_fingerprint(root, args.dataset),
            baseline_ref=args.baseline,
            controls=args.control,
            decision_rule=args.decision_rule,
            falsification_rule=args.falsification,
            artifact_ref=args.artifact,
            metric_rules=_metric_rules(args.metric_rule),
            verifier_argv=verifier,
            max_cost=args.max_cost,
        )
        store.add("experiment_preregistered", registration)
        return {
            "preregistered": True,
            "registration": registration.model_dump(mode="json"),
            "next": f"epi start --registration {registration.id}",
        }
    if command == "start":
        contract = state.contracts.get(args.contract)
        if contract is None:
            raise ValueError("unknown experiment contract")
        require_clean_revision(root, contract.git_revision)
        check_fingerprints(root, contract)
        store.append("experiment_started", {"contract_id": args.contract})
        return {
            "started": args.contract,
            "reserved": contract.max_cost,
            "next": f"epi record --contract {args.contract} --summary ... --outcome ... "
            "--cost ... --metric name=value",
        }
    if command == "record":
        contract = state.contracts.get(args.contract)
        if contract is None:
            raise ValueError("unknown experiment contract")
        require_clean_revision(root, contract.git_revision)
        check_fingerprints(root, contract)
        metrics = _parse_metrics(args.metric)
        if args.outcome != "infrastructure_failure" and not metrics:
            metrics, _ = artifact_metrics(root, contract)
        experiment_id, evidence_id = research_id("EXP"), research_id("EV")
        exp = Experiment(
            id=experiment_id,
            proposal_id=contract.proposal_id,
            contract_id=contract.id,
            summary=args.summary,
            outcome=args.outcome,
            actual_cost=args.cost,
            git_revision=contract.git_revision,
            config_ref=contract.config_ref,
            dataset_ref=contract.dataset_ref,
            seeds=args.seed,
            metrics=metrics,
            run_command=args.run_command,
            artifact_refs=[contract.artifact_ref, *args.artifact],
            evidence_id=evidence_id,
        )
        evidence = EvidenceRecord(
            id=evidence_id,
            experiment_id=experiment_id,
            summary=exp.summary,
            artifact_refs=exp.artifact_refs,
        )
        store.append(
            "experiment_recorded",
            {
                "experiment": exp.model_dump(mode="json"),
                "evidence": evidence.model_dump(mode="json"),
            },
        )
        return {
            "experiment_id": experiment_id,
            "evidence_id": evidence_id,
            "verified": False,
            "spent": state.spent + exp.actual_cost,
            "overrun": max(
                0.0, state.spent + exp.actual_cost - (state.goal.budget if state.goal else 0.0)
            ),
            "next": (
                "Investigate infrastructure failure and preregister a new experiment"
                if args.outcome == "infrastructure_failure"
                else f"epi verify --experiment {experiment_id}"
            ),
        }
    if command == "verify":
        return _verify(args, store, root, state)
    if command == "retry":
        store.append(
            "verification_retried",
            {
                "contract_id": args.contract,
                "reason": args.reason,
            },
        )
        return {"resumed": args.contract, "next": "epi verify --experiment EXPERIMENT_ID"}
    if command == "abort":
        store.append(
            "experiment_aborted",
            {
                "contract_id": args.contract,
                "actual_cost": args.cost,
                "reason": args.reason,
            },
        )
        return {"aborted": args.contract, "actual_cost": args.cost}
    if command == "reflect":
        store.append(
            "claim_reviewed",
            {
                "claim_id": args.claim,
                "status": ClaimStatus(args.verdict).value,
                "evidence_id": args.evidence,
                "rationale": args.rationale,
            },
        )
        return {
            "claim": args.claim,
            "verdict": args.verdict,
            "note": "Provisional scope-bound claim; not a universal scientific fact",
        }
    raise ValueError(f"unknown command: {command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    store = ResearchStore(root / ".research")
    try:
        result = _dispatch(args, store, root)
        _emit(result)
        if args.command == "verify" and isinstance(result, dict) and not result["check_passed"]:
            return 2
    except (ValueError, RuntimeError, OSError, KeyError, ValidationError) as exc:
        print(f"epi: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
