"""Development CLI used by the Codex EpiPilot plugin skill."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from epipilot.core.models import EvidenceId, EvidenceKind, HypothesisId, Provenance
from epipilot.epistemics.models import (
    HypothesisStatus,
    ResolutionMode,
    UnknownId,
    UnknownImpact,
)
from epipilot.integrations.codex.bridge import CodexResearchBridge
from epipilot.integrations.codex.verifier import CommandProbeVerifier
from epipilot.requirements.models import DecisionAuthority
from epipilot.runtime.sqlite_event_store import SqliteEventStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="epipilot-codex",
        description="Durable evidence-driven research bridge for the EpiPilot Codex plugin.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(".epipilot/events.sqlite3"),
        help="SQLite event-store path (default: .epipilot/events.sqlite3).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a canonical research project.")
    _add_project_id(init)
    init.add_argument("--goal", required=True)
    init.add_argument("--success", action="append", default=[])
    init.add_argument("--constraint", action="append", default=[])
    init.add_argument("--budget", action="append", default=[])
    init.add_argument("--forbidden", action="append", default=[])

    status = subparsers.add_parser("status", help="Show the canonical epistemic state.")
    _add_project_id(status)

    unknown = subparsers.add_parser("unknown", help="Register a decision-relevant unknown.")
    _add_project_id(unknown)
    unknown.add_argument("--question", required=True)
    unknown.add_argument(
        "--impact",
        choices=[item.value for item in UnknownImpact],
        default="medium",
    )
    unknown.add_argument(
        "--mode",
        choices=[item.value for item in ResolutionMode],
        default="experiment",
    )
    unknown.add_argument("--voi", type=float, default=1.0)
    unknown.add_argument("--decision-sensitivity", type=float, default=1.0)

    hypothesis = subparsers.add_parser(
        "hypothesis",
        help="Preregister an active falsifiable hypothesis.",
    )
    _add_project_id(hypothesis)
    hypothesis.add_argument("--statement", required=True)
    hypothesis.add_argument("--prediction", action="append", required=True)
    hypothesis.add_argument("--falsification", action="append", required=True)
    hypothesis.add_argument("--confidence", type=float, default=0.5)

    observe = subparsers.add_parser(
        "observe",
        help="Record a non-authoritative executor observation.",
    )
    _add_project_id(observe)
    observe.add_argument("--kind", choices=[item.value for item in EvidenceKind], required=True)
    observe.add_argument("--summary", required=True)
    observe.add_argument("--source", required=True)
    observe.add_argument("--scope", required=True)

    verify = subparsers.add_parser(
        "verify-command",
        help="Run a shell-free deterministic verifier and record its derived evidence.",
    )
    _add_project_id(verify)
    verify.add_argument("--name", required=True)
    verify.add_argument("--scope", required=True)
    verify.add_argument("--cwd", type=Path, default=Path())
    verify.add_argument("--timeout", type=float, default=300.0)
    verify.add_argument("argv", nargs=argparse.REMAINDER)

    update = subparsers.add_parser(
        "hypothesis-update",
        help="Update a hypothesis from already-recorded evidence.",
    )
    _add_project_id(update)
    update.add_argument("--hypothesis-id", required=True)
    update.add_argument(
        "--status",
        choices=[item.value for item in HypothesisStatus],
        required=True,
    )
    update.add_argument("--confidence", type=float, required=True)
    update.add_argument("--supporting-evidence", action="append", default=[])
    update.add_argument("--contradicting-evidence", action="append", default=[])
    update.add_argument("--superseded-by")

    resolve = subparsers.add_parser(
        "resolve",
        help="Resolve an unknown using independently verified evidence.",
    )
    _add_project_id(resolve)
    resolve.add_argument("--unknown-id", required=True)
    resolve.add_argument("--evidence-id", action="append", default=[])
    resolve.add_argument("--decision-id", action="append", default=[])

    decision = subparsers.add_parser("decision", help="Record an authorized project decision.")
    _add_project_id(decision)
    decision.add_argument("--question", required=True)
    decision.add_argument("--choice", required=True)
    decision.add_argument("--rationale", required=True)
    decision.add_argument(
        "--authority",
        choices=[item.value for item in DecisionAuthority],
        default="user",
    )
    decision.add_argument("--basis-ref", action="append", default=[])
    decision.add_argument("--irreversible", action="store_true")

    next_step = subparsers.add_parser(
        "next",
        help="Choose the next bounded action from canonical state.",
    )
    _add_project_id(next_step)

    return parser


def _add_project_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", required=True)


def _bridge(db: Path) -> CodexResearchBridge:
    return CodexResearchBridge(SqliteEventStore(db))


def _state_payload(bridge: CodexResearchBridge, project_id: str) -> dict[str, object]:
    state = bridge.state(project_id)
    return {
        "project_id": state.project_id,
        "event_version": state.event_version,
        "requirements": [
            {"id": str(item.id), "kind": item.kind.value, "statement": item.statement}
            for item in state.requirements
        ],
        "decisions": [
            {
                "id": str(item.id),
                "question": item.question,
                "choice": item.choice,
                "authority": item.authority.value,
            }
            for item in state.decisions
        ],
        "unknowns": [
            {
                "id": str(item.id),
                "question": item.question,
                "impact": item.impact.value,
                "resolution_mode": item.resolution_mode.value,
                "status": item.status.value,
                "resolution_evidence": [str(value) for value in item.resolution_evidence],
                "resolution_decisions": list(item.resolution_decisions),
            }
            for item in state.unknowns
        ],
        "hypotheses": [
            {
                "id": str(item.id),
                "statement": item.statement,
                "status": item.status.value,
                "confidence": item.confidence,
                "predictions": list(item.predictions),
                "falsification_conditions": list(item.falsification_conditions),
                "supporting_evidence": [str(value) for value in item.supporting_evidence],
                "contradicting_evidence": [str(value) for value in item.contradicting_evidence],
            }
            for item in state.hypotheses
        ],
        "evidence": [
            {
                "id": str(item.id),
                "kind": item.kind.value,
                "summary": item.summary,
                "source": item.provenance.source,
                "scope": item.provenance.scope,
                "independently_verified": item.independently_verified,
            }
            for item in state.evidence
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    bridge = _bridge(args.db)

    if args.command == "init":
        state = bridge.start_project(
            project_id=args.project_id,
            goal=args.goal,
            success_criteria=tuple(args.success),
            hard_constraints=tuple(args.constraint),
            budgets=tuple(args.budget),
            forbidden_actions=tuple(args.forbidden),
        )
        payload: object = {"project_id": state.project_id, "event_version": state.event_version}

    elif args.command == "status":
        payload = _state_payload(bridge, args.project_id)

    elif args.command == "unknown":
        unknown_id = bridge.register_unknown(
            project_id=args.project_id,
            question=args.question,
            impact=UnknownImpact(args.impact),
            resolution_mode=ResolutionMode(args.mode),
            value_of_information=args.voi,
            decision_sensitivity=args.decision_sensitivity,
        )
        payload = {"unknown_id": str(unknown_id)}

    elif args.command == "hypothesis":
        hypothesis_id = bridge.create_hypothesis(
            project_id=args.project_id,
            statement=args.statement,
            predictions=tuple(args.prediction),
            falsification_conditions=tuple(args.falsification),
            confidence=args.confidence,
        )
        payload = {"hypothesis_id": str(hypothesis_id)}

    elif args.command == "observe":
        evidence_id = bridge.record_observation(
            project_id=args.project_id,
            kind=EvidenceKind(args.kind),
            summary=args.summary,
            provenance=Provenance(source=args.source, scope=args.scope),
        )
        payload = {"evidence_id": str(evidence_id), "independently_verified": False}

    elif args.command == "verify-command":
        result = CommandProbeVerifier(bridge).run(
            project_id=args.project_id,
            name=args.name,
            argv=tuple(args.argv),
            cwd=args.cwd,
            scope=args.scope,
            timeout_seconds=args.timeout,
        )
        payload = {
            "evidence_id": str(result.evidence_id),
            "independently_verified": True,
            "passed": result.passed,
            "return_code": result.return_code,
            "timed_out": result.timed_out,
        }

    elif args.command == "hypothesis-update":
        bridge.update_hypothesis(
            project_id=args.project_id,
            hypothesis_id=HypothesisId(UUID(args.hypothesis_id)),
            status=HypothesisStatus(args.status),
            confidence=args.confidence,
            supporting_evidence=tuple(
                EvidenceId(UUID(value)) for value in args.supporting_evidence
            ),
            contradicting_evidence=tuple(
                EvidenceId(UUID(value)) for value in args.contradicting_evidence
            ),
            superseded_by=(HypothesisId(UUID(args.superseded_by)) if args.superseded_by else None),
        )
        payload = {"updated": args.hypothesis_id, "status": args.status}

    elif args.command == "resolve":
        bridge.resolve_unknown(
            project_id=args.project_id,
            unknown_id=UnknownId(UUID(args.unknown_id)),
            evidence_ids=tuple(EvidenceId(UUID(value)) for value in args.evidence_id),
            decision_ids=tuple(args.decision_id),
        )
        payload = {"resolved": args.unknown_id}

    elif args.command == "decision":
        decision_id = bridge.record_decision(
            project_id=args.project_id,
            question=args.question,
            choice=args.choice,
            rationale=args.rationale,
            authority=DecisionAuthority(args.authority),
            basis_refs=tuple(args.basis_ref),
            reversible=not args.irreversible,
        )
        payload = {"decision_id": decision_id}

    else:
        directive = bridge.next_directive(args.project_id)
        payload = {
            "kind": directive.kind.value,
            "reason": directive.reason,
            "canonical_event_version": directive.canonical_event_version,
            "questions": list(directive.questions),
            "unknown_id": str(directive.unknown_id) if directive.unknown_id else None,
            "task_id": str(directive.task_id) if directive.task_id else None,
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
