"""Lightweight CLI used directly by Codex, Kimi Code, and other coding agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from epipilot.research.models import (
    Claim,
    ClaimStatus,
    EvidenceRecord,
    Experiment,
    Proposal,
    ProposalKind,
    ResearchGoal,
    ResearchState,
    Unknown,
    research_id,
)
from epipilot.research.onboarding import git_revision, inspect_repository
from epipilot.research.planner import rank_proposals, research_phase
from epipilot.research.store import ResearchStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="epi", description="Agent-native, evidence-grounded research plugin"
    )
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="research repository root (default cwd)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="record user-approved research goal and budget")
    init.add_argument("--goal", required=True)
    init.add_argument("--budget", type=float, required=True, help="total experiment cost units")
    init.add_argument("--metric", action="append", default=[])
    init.add_argument("--constraint", action="append", default=[])

    sub.add_parser("onboard", help="inventory existing repository without claiming results")
    sub.add_parser("status", help="show budget and knowledge state")
    sub.add_parser("review", help="show concise research memory and frontier")

    unknown = sub.add_parser("unknown", help="add or resolve a research unknown")
    unknown_sub = unknown.add_subparsers(dest="action", required=True)
    unknown_add = unknown_sub.add_parser("add")
    unknown_add.add_argument("--id")
    unknown_add.add_argument("--question", required=True)
    unknown_add.add_argument("--importance", type=float, default=0.5)
    unknown_add.add_argument("--sensitivity", type=float, default=0.5)
    unknown_resolve = unknown_sub.add_parser("resolve")
    unknown_resolve.add_argument("--id", required=True)
    unknown_resolve.add_argument("--evidence", required=True)

    claim = sub.add_parser("claim", help="add a falsifiable claim")
    claim.add_argument("--id")
    claim.add_argument("--statement", required=True)
    claim.add_argument("--scope", required=True)
    claim.add_argument("--assumption", action="append", default=[])

    propose = sub.add_parser("propose", help="register a scored research action")
    propose.add_argument("--id")
    propose.add_argument("--title", required=True)
    propose.add_argument("--kind", choices=[k.value for k in ProposalKind], required=True)
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

    next_cmd = sub.add_parser("next", help="rank actionable proposals under remaining budget")
    next_cmd.add_argument("--limit", type=int, default=5)
    plan = sub.add_parser("plan", help="compile executable research task context")
    plan.add_argument("--id", required=True)
    sub.add_parser("iterate", help="emit one agent execution/verification iteration")

    record = sub.add_parser("record", help="record UNVERIFIED experiment result")
    record.add_argument("--proposal", required=True)
    record.add_argument("--summary", required=True)
    record.add_argument(
        "--outcome",
        choices=["improved", "no_gain", "inconclusive", "infrastructure_failure"],
        required=True,
    )
    record.add_argument("--cost", type=float, required=True)
    record.add_argument("--revision", help="exact Git revision; default current HEAD")
    record.add_argument("--config", required=True, help="experiment configuration reference")
    record.add_argument("--dataset", required=True, help="dataset/split/version reference")
    record.add_argument("--seed", type=int, action="append", default=[])
    record.add_argument("--metric", action="append", default=[], help="name=numeric_value")
    record.add_argument("--artifact", action="append", required=True)
    record.add_argument("--run-command", default="")

    verify = sub.add_parser("verify", help="run independent argv check (never through a shell)")
    verify.add_argument("--experiment", required=True)
    verify.add_argument("--timeout", type=int, default=120)
    verify.add_argument("--check", nargs=argparse.REMAINDER, required=True)

    reflect = sub.add_parser("reflect", help="review claim using independently checked evidence")
    reflect.add_argument("--claim", required=True)
    reflect.add_argument(
        "--verdict", choices=["supported", "refuted", "inconclusive"], required=True
    )
    reflect.add_argument("--evidence", required=True)

    return parser


def _emit(result: object) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _status(state: ResearchState) -> dict[str, object]:
    return {
        "goal": None if state.goal is None else state.goal.statement,
        "budget": None if state.goal is None else state.goal.budget,
        "spent": state.spent,
        "remaining": state.remaining,
        "phase": research_phase(state),
        "unknowns_open": sum(x.status == "open" for x in state.unknowns.values()),
        "claims": len(state.claims),
        "proposals": len(state.proposals),
        "experiments": len(state.experiments),
        "independently_checked_evidence": sum(
            e.independently_verified for e in state.evidence.values()
        ),
    }


def _plan(state: ResearchState, proposal_id: str) -> dict[str, object]:
    proposal = state.proposals.get(proposal_id)
    if proposal is None:
        raise ValueError(f"unknown proposal: {proposal_id}")
    if proposal.status != "proposed":
        raise ValueError("proposal is not available for execution")
    if proposal.estimated_cost > state.remaining:
        raise ValueError("proposal exceeds remaining budget")
    if any(state.proposals[parent].status != "completed" for parent in proposal.depends_on):
        raise ValueError("proposal dependencies are not yet verified")
    return {
        "proposal": proposal.model_dump(mode="json"),
        "goal": None if state.goal is None else state.goal.model_dump(mode="json"),
        "related_unknowns": [
            state.unknowns[uid].model_dump(mode="json") for uid in proposal.unknown_ids
        ],
        "related_claims": [state.claims[cid].model_dump(mode="json") for cid in proposal.claim_ids],
        "budget_remaining": state.remaining,
        "agent_instructions": [
            "Inspect relevant repository code, existing evidence, and competing explanations.",
            "Choose the cheapest discriminative experiment, documenting baseline and controls.",
            "Preserve artifacts and record revision, config, dataset, seeds, and costs.",
            "Execute experiment with the coding agent; never invent measurements.",
            "Record an unverified observation, then run a separate verification check.",
            "Only after scientific review use epi reflect or epi unknown resolve.",
            "Run epi next again; do not blindly repeat an unchanged failed attempt.",
        ],
    }


def _parse_metrics(items: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key or key in result:
            raise ValueError("metrics must be unique name=value pairs")
        number = float(value)
        if not -float("inf") < number < float("inf"):
            raise ValueError("metric values must be finite")
        result[key] = number
    return result


def _verify_command(root: Path, argv: list[str], timeout: int) -> tuple[bool, str, int]:
    """An explicit user/agent command, never eval or shell interpolation."""
    command = list(argv)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise ValueError("provide a real argv command after --check")
    if timeout < 1 or timeout > 3600:
        raise ValueError("timeout must be between 1 and 3600 seconds")
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        content = completed.stdout
        return completed.returncode == 0, hashlib.sha256(content).hexdigest(), completed.returncode
    except subprocess.TimeoutExpired as exc:
        content = (exc.stdout or b"") + b"\nCHECK TIMEOUT"
        return False, hashlib.sha256(content).hexdigest(), 124


def _dispatch(args: argparse.Namespace, store: ResearchStore) -> object:
    state, _ = store.load()
    cmd: str = args.command
    if cmd == "init":
        goal = ResearchGoal(
            statement=args.goal,
            budget=args.budget,
            success_metrics=args.metric,
            constraints=args.constraint,
        )
        store.add("goal_set", goal)
        return {"initialized": True, "goal": goal.model_dump(mode="json")}
    if cmd == "onboard":
        snapshot = inspect_repository(args.root)
        store.add("onboarding_recorded", snapshot)
        return {
            "onboarding": snapshot.model_dump(mode="json"),
            "warning": "Inventory is not evidence; inspect experiments before adding claims.",
        }
    if cmd == "status":
        return _status(state)
    if cmd == "review":
        return {
            **_status(state),
            "open_unknowns": [
                x.model_dump(mode="json") for x in state.unknowns.values() if x.status == "open"
            ],
            "claims": [x.model_dump(mode="json") for x in state.claims.values()],
            "top_proposals": [
                {
                    "id": x.proposal.id,
                    "title": x.proposal.title,
                    "score": x.score,
                    "rationale": x.rationale,
                }
                for x in rank_proposals(state, limit=5)
            ],
        }
    if cmd == "unknown":
        if args.action == "add":
            unknown_item = Unknown(
                id=args.id or research_id("UNK"),
                question=args.question,
                importance=args.importance,
                decision_sensitivity=args.sensitivity,
            )
            store.add("unknown_added", unknown_item)
            return unknown_item.model_dump(mode="json")
        store.append("unknown_resolved", {"unknown_id": args.id, "evidence_id": args.evidence})
        return {"resolved": args.id, "evidence_id": args.evidence}
    if cmd == "claim":
        claim_item = Claim(
            id=args.id or research_id("CLM"),
            statement=args.statement,
            scope=args.scope,
            assumptions=args.assumption,
        )
        store.add("claim_added", claim_item)
        return claim_item.model_dump(mode="json")
    if cmd == "propose":
        proposal_item = Proposal(
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
        store.add("proposal_added", proposal_item)
        return proposal_item.model_dump(mode="json")
    if cmd == "next":
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
    if cmd == "plan":
        return _plan(state, args.id)
    if cmd == "iterate":
        ranked = rank_proposals(state, limit=1)
        if not ranked:
            return {
                "status": "no_runnable_proposal",
                "guidance": "Review budget and pending experiments, or add new proposals.",
                **_status(state),
            }
        return {
            "status": "ready_for_agent",
            "task": _plan(state, ranked[0].proposal.id),
            "continuation": "After verifying evidence, reflect and call epi iterate again.",
        }
    if cmd == "record":
        revision = args.revision or git_revision(args.root)
        if revision == "unversioned":
            raise ValueError("a pinned Git revision is mandatory for research experiments")
        experiment_id, evidence_id = research_id("EXP"), research_id("EV")
        new_experiment = Experiment(
            id=experiment_id,
            proposal_id=args.proposal,
            summary=args.summary,
            outcome=args.outcome,
            actual_cost=args.cost,
            git_revision=revision,
            config_ref=args.config,
            dataset_ref=args.dataset,
            seeds=args.seed,
            metrics=_parse_metrics(args.metric),
            artifact_refs=args.artifact,
            run_command=args.run_command,
            evidence_id=evidence_id,
        )
        evidence = EvidenceRecord(
            id=evidence_id,
            experiment_id=experiment_id,
            summary=args.summary,
            artifact_refs=args.artifact,
        )
        store.append(
            "experiment_recorded",
            {
                "experiment": new_experiment.model_dump(mode="json"),
                "evidence": evidence.model_dump(mode="json"),
            },
        )
        return {
            "experiment_id": experiment_id,
            "evidence_id": evidence_id,
            "verified": False,
            "next": "epi verify --new_experiment ID --check COMMAND ARGS...",
        }
    if cmd == "verify":
        experiment = state.experiments.get(args.experiment)
        if experiment is None:
            raise ValueError("unknown experiment")
        evidence = state.evidence[experiment.evidence_id]
        if evidence.independently_verified:
            raise ValueError("experiment evidence has already been verified")
        passed, digest, code = _verify_command(args.root, args.check, args.timeout)
        check = list(args.check)
        if check and check[0] == "--":
            check.pop(0)
        payload = {
            "evidence_id": evidence.id,
            "check_argv": check,
            "check_output_sha256": digest,
            "exit_code": code,
        }
        store.append("verification_recorded" if passed else "verification_failed", payload)
        return {
            "check_passed": passed,
            "exit_code": code,
            "output_sha256": digest,
            "scientific_interpretation": "requires explicit epi reflect and scope review",
        }
    if cmd == "reflect":
        store.append(
            "claim_reviewed",
            {
                "claim_id": args.claim,
                "status": ClaimStatus(args.verdict).value,
                "evidence_id": args.evidence,
            },
        )
        return {
            "claim": args.claim,
            "status": args.verdict,
            "note": "Provisional claim support, not canonical scientific fact.",
        }
    raise ValueError(f"unknown command: {cmd}")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point returning a shell-compatible exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    store = ResearchStore(root / ".research")
    try:
        result = _dispatch(args, store)
        _emit(result)
        if args.command == "verify" and isinstance(result, dict) and not result["check_passed"]:
            return 2
    except (ValueError, RuntimeError, OSError, ValidationError, KeyError) as exc:
        print(f"epi: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
