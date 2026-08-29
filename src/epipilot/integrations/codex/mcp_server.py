"""MCP stdio server exposing EpiPilot's bounded research control surface."""

from __future__ import annotations

import argparse
import os
from importlib.metadata import version
from pathlib import Path
from uuid import UUID, uuid4

from mcp.server.mcpserver import MCPServer

from epipilot.core.models import EvidenceId, EvidenceKind, HypothesisId, Provenance
from epipilot.epistemics.models import HypothesisStatus, ResolutionMode, UnknownId, UnknownImpact
from epipilot.integrations.codex.bridge import CodexResearchBridge
from epipilot.integrations.codex.serialization import directive_payload, state_payload
from epipilot.requirements.models import DecisionAuthority
from epipilot.research.contracts import (
    ExperimentContract,
    ExperimentId,
    ExperimentPrediction,
    ExperimentStatus,
)
from epipilot.runtime.sqlite_event_store import SqliteEventStore


def default_event_store_path() -> Path:
    """Resolve durable plugin state without relying on a conversation transcript."""
    explicit = os.environ.get("EPIPILOT_DB")
    if explicit:
        return Path(explicit).expanduser().resolve()
    plugin_data = os.environ.get("PLUGIN_DATA")
    if plugin_data:
        return (Path(plugin_data) / "events.sqlite3").resolve()
    return (Path.cwd() / ".epipilot" / "events.sqlite3").resolve()


def _bridge(db_path: Path | None = None) -> CodexResearchBridge:
    return CodexResearchBridge(SqliteEventStore(db_path or default_event_store_path()))


def _new_project_id(goal: str) -> str:
    slug = "-".join(goal.lower().split())[:40].strip("-") or "research"
    safe = "".join(char for char in slug if char.isalnum() or char in "-_")
    return f"{safe or 'research'}-{uuid4().hex[:8]}"


def build_server(db_path: Path | None = None) -> MCPServer[None]:
    """Build the local EpiPilot MCP server against one durable event store."""
    bridge = _bridge(db_path)
    server: MCPServer[None] = MCPServer(
        "epipilot",
        title="EpiPilot Epistemic Research",
        description="Durable evidence-driven research control for Codex.",
        version=version("epipilot"),
        instructions=(
            "Canonical state lives in EpiPilot, not in chat. Ask only user-owned decisions. "
            "Preregister technical hypotheses and experiments before execution. Executor "
            "observations are never independently verified. Use Codex shell plus the "
            "epipilot-codex verify-command CLI for decisive deterministic evidence."
        ),
    )

    @server.tool()
    def epipilot_info() -> dict[str, object]:
        """Return installation, persistence, and safety information for this EpiPilot server."""
        return {
            "version": version("epipilot"),
            "event_store_path": str(bridge.event_store.path),
            "plugin_root": os.environ.get("PLUGIN_ROOT"),
            "plugin_data": os.environ.get("PLUGIN_DATA"),
            "verification": (
                "Run epipilot-codex --db <event_store_path> verify-command through the "
                "Codex shell; no arbitrary command execution is exposed through MCP."
            ),
        }

    @server.tool()
    def epipilot_list_projects() -> dict[str, object]:
        """List durable EpiPilot project identifiers available to this installation."""
        return {"project_ids": list(bridge.project_ids())}

    @server.tool()
    def epipilot_start_project(
        goal: str,
        success_criteria: list[str],
        project_id: str | None = None,
        hard_constraints: list[str] | None = None,
        budgets: list[str] | None = None,
        forbidden_actions: list[str] | None = None,
    ) -> dict[str, object]:
        """Create a durable project contract after goal and success criteria are explicit."""
        identifier = project_id or _new_project_id(goal)
        state = bridge.start_project(
            project_id=identifier,
            goal=goal,
            success_criteria=tuple(success_criteria),
            hard_constraints=tuple(hard_constraints or ()),
            budgets=tuple(budgets or ()),
            forbidden_actions=tuple(forbidden_actions or ()),
        )
        return state_payload(state)

    @server.tool()
    def epipilot_get_state(project_id: str) -> dict[str, object]:
        """Return the canonical epistemic map reconstructed from the append-only event stream."""
        return state_payload(bridge.state(project_id))

    @server.tool()
    def epipilot_next(project_id: str) -> dict[str, object]:
        """Return the deterministic next research action from canonical state."""
        return directive_payload(bridge.next_directive(project_id))

    @server.tool()
    def epipilot_register_unknown(
        project_id: str,
        question: str,
        impact: str = "medium",
        resolution_mode: str = "experiment",
        value_of_information: float = 1.0,
        decision_sensitivity: float = 1.0,
    ) -> dict[str, object]:
        """Register a decision-relevant unknown instead of resolving it in chat memory."""
        identifier = bridge.register_unknown(
            project_id=project_id,
            question=question,
            impact=UnknownImpact(impact),
            resolution_mode=ResolutionMode(resolution_mode),
            value_of_information=value_of_information,
            decision_sensitivity=decision_sensitivity,
        )
        return {"unknown_id": str(identifier)}

    @server.tool()
    def epipilot_preregister_hypothesis(
        project_id: str,
        statement: str,
        predictions: list[str],
        falsification_conditions: list[str],
        confidence: float = 0.5,
    ) -> dict[str, object]:
        """Preregister one falsifiable technical hypothesis before testing it."""
        identifier = bridge.create_hypothesis(
            project_id=project_id,
            statement=statement,
            predictions=tuple(predictions),
            falsification_conditions=tuple(falsification_conditions),
            confidence=confidence,
        )
        return {"hypothesis_id": str(identifier)}

    @server.tool()
    def epipilot_preregister_experiment(
        project_id: str,
        unknown_id: str,
        objective: str,
        hypothesis_ids: list[str],
        controlled_variables: list[str],
        measurements: list[str],
        expected_observations: list[str],
        falsification_conditions: list[str],
        decision_rule: str,
        budget: str,
        resource_claims: list[str] | None = None,
    ) -> dict[str, object]:
        """Persist a bounded discriminative experiment before any experiment execution."""
        if not (len(hypothesis_ids) == len(expected_observations) == len(falsification_conditions)):
            raise ValueError(
                "hypothesis_ids, expected_observations, and falsification_conditions "
                "must have equal lengths"
            )
        hypotheses = tuple(HypothesisId(UUID(value)) for value in hypothesis_ids)
        predictions = tuple(
            ExperimentPrediction(
                hypothesis_id=hypothesis_id,
                expected_observation=expected,
                falsification_condition=falsification,
            )
            for hypothesis_id, expected, falsification in zip(
                hypotheses,
                expected_observations,
                falsification_conditions,
                strict=True,
            )
        )
        contract = ExperimentContract(
            unknown_id=UnknownId(UUID(unknown_id)),
            objective=objective,
            hypothesis_ids=hypotheses,
            controlled_variables=tuple(controlled_variables),
            measurements=tuple(measurements),
            predictions=predictions,
            decision_rule=decision_rule,
            budget=budget,
            resource_claims=tuple(resource_claims or ()),
        )
        identifier = bridge.preregister_experiment(project_id=project_id, contract=contract)
        return {"experiment_id": str(identifier)}

    @server.tool()
    def epipilot_record_decision(
        project_id: str,
        question: str,
        choice: str,
        rationale: str,
        authority: str = "user",
        reversible: bool = True,
        basis_refs: list[str] | None = None,
    ) -> dict[str, object]:
        """Record an explicit user decision or an authorized reversible system default."""
        identifier = bridge.record_decision(
            project_id=project_id,
            question=question,
            choice=choice,
            rationale=rationale,
            authority=DecisionAuthority(authority),
            basis_refs=tuple(basis_refs or ()),
            reversible=reversible,
        )
        return {"decision_id": identifier}

    @server.tool()
    def epipilot_record_observation(
        project_id: str,
        summary: str,
        source: str,
        scope: str,
        kind: str = "executor_report",
    ) -> dict[str, object]:
        """Record an executor observation; this tool can never create verified evidence."""
        identifier = bridge.record_observation(
            project_id=project_id,
            kind=EvidenceKind(kind),
            summary=summary,
            provenance=Provenance(source=source, scope=scope),
        )
        return {"evidence_id": str(identifier), "independently_verified": False}

    @server.tool()
    def epipilot_conclude_experiment(
        project_id: str,
        experiment_id: str,
        evidence_ids: list[str],
        conclusion: str,
        status: str = "concluded",
    ) -> dict[str, object]:
        """Conclude a preregistered experiment using only already verified evidence."""
        bridge.conclude_experiment(
            project_id=project_id,
            experiment_id=ExperimentId(UUID(experiment_id)),
            status=ExperimentStatus(status),
            evidence_ids=tuple(EvidenceId(UUID(value)) for value in evidence_ids),
            conclusion=conclusion,
        )
        return {"experiment_id": experiment_id, "status": status}

    @server.tool()
    def epipilot_update_hypothesis(
        project_id: str,
        hypothesis_id: str,
        status: str,
        confidence: float,
        supporting_evidence: list[str] | None = None,
        contradicting_evidence: list[str] | None = None,
        superseded_by: str | None = None,
    ) -> dict[str, object]:
        """Update belief state; supported/refuted states require independent evidence."""
        bridge.update_hypothesis(
            project_id=project_id,
            hypothesis_id=HypothesisId(UUID(hypothesis_id)),
            status=HypothesisStatus(status),
            confidence=confidence,
            supporting_evidence=tuple(
                EvidenceId(UUID(value)) for value in supporting_evidence or ()
            ),
            contradicting_evidence=tuple(
                EvidenceId(UUID(value)) for value in contradicting_evidence or ()
            ),
            superseded_by=HypothesisId(UUID(superseded_by)) if superseded_by else None,
        )
        return {"hypothesis_id": hypothesis_id, "status": status}

    @server.tool()
    def epipilot_resolve_unknown(
        project_id: str,
        unknown_id: str,
        evidence_ids: list[str] | None = None,
        decision_ids: list[str] | None = None,
    ) -> dict[str, object]:
        """Resolve an unknown only when its evidence/decision authority requirements hold."""
        bridge.resolve_unknown(
            project_id=project_id,
            unknown_id=UnknownId(UUID(unknown_id)),
            evidence_ids=tuple(EvidenceId(UUID(value)) for value in evidence_ids or ()),
            decision_ids=tuple(decision_ids or ()),
        )
        return {"unknown_id": unknown_id, "resolved": True}

    return server


def main() -> None:
    """Run the local MCP server or perform a bounded installation self-check."""
    parser = argparse.ArgumentParser(prog="epipilot-mcp")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        bridge = _bridge()
        print(f"epipilot-mcp {version('epipilot')} ok; event_store={bridge.event_store.path}")
        return
    build_server().run()


if __name__ == "__main__":
    main()
