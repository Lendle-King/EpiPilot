"""Validate agent semantic proposals, then atomically add canonical research entities."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path, PurePosixPath
from uuid import UUID

from pydantic import Field

from epipilot.core.events import EventType
from epipilot.core.models import EvidenceKind, TaskId, TaskStatus, utc_now
from epipilot.epistemics.models import HypothesisStatus
from epipilot.events.payloads import (
    EventPayload,
    EvidenceRecordedPayload,
    HypothesisCreatedPayload,
    PlanBasisPayload,
    PlanDependencyPayload,
    PlanVersionCreatedPayload,
    TaskCreatedPayload,
    TaskStatusChangedPayload,
    UnknownRegisteredPayload,
)
from epipilot.planning.graph import PlanBasisKind
from epipilot.research.engine import ResearchEngine, stable_id
from epipilot.research.events import ProposalAdded, ResearchNodeAdded
from epipilot.research.frontier import (
    DiscoveryAudit,
    Facet,
    FrontierDiscovered,
    FrontierInitialized,
    FrontierValue,
    MeasurementContract,
    Question,
)
from epipilot.research.models import ExperimentProposal, ResearchNode
from epipilot.research.policy import validate_graph
from epipilot.research.reducer import canonical_basis, task_id


class SourceAnchor(FrontierValue):
    revision: str = Field(pattern="^[0-9a-f]{40}$")
    path: str
    line: int = Field(ge=1)
    quote: str = Field(min_length=1)


class Finding(FrontierValue):
    topic_key: str
    facet: Facet
    hypothesis: HypothesisCreatedPayload
    node: ResearchNode
    unknown: UnknownRegisteredPayload
    question: Question
    alternatives: tuple[str, ...] = Field(min_length=2)


class AgentProposal(FrontierValue):
    proposal: ExperimentProposal
    contract: MeasurementContract


class DiscoveryBatch(FrontierValue):
    id: UUID
    based_on_version: int = Field(ge=0)
    basis_ref: str
    summary: str = Field(min_length=1)
    sources: tuple[SourceAnchor, ...] = ()
    findings: tuple[Finding, ...] = ()
    proposals: tuple[AgentProposal, ...] = ()
    no_new_findings_reason: str = ""


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def safe_input(root: Path, path: str) -> Path:
    relative = PurePosixPath(path)
    if (
        path != relative.as_posix()
        or relative.is_absolute()
        or ".." in relative.parts
        or not relative.parts
        or any(
            part.startswith(".env")
            or part in {".git", ".research"}
            or part.endswith((".pem", ".key"))
            for part in relative.parts
        )
    ):
        raise ValueError("input path escapes research scope or references secrets")
    resolved = (root / path).resolve()
    if root.resolve() not in resolved.parents or not resolved.is_file():
        raise ValueError("input missing or outside research repository")
    return resolved


def validate_contract(root: Path, c: MeasurementContract) -> None:
    git(root, "rev-parse", "--verify", "--end-of-options", f"{c.git_revision}^{{commit}}")
    if len({x.path for x in c.inputs}) != len(c.inputs) or len({x.sha256 for x in c.inputs}) != len(
        c.inputs
    ):
        raise ValueError("duplicate input file")
    identities: set[tuple[int, int]] = set()
    for item in c.inputs:
        source = safe_input(root, item.path)
        stat = source.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity in identities:
            raise ValueError("duplicate input file identity; aliases cannot inflate samples")
        identities.add(identity)
        with source.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != item.sha256:
            raise ValueError("input changed; new discovery/strategy required")


def enable_frontier(engine: ResearchEngine, root: Path, exploration_fraction: float = 0.2) -> None:
    r = engine.state.research
    if r is None:
        raise ValueError("initialize an explicitly authorized research goal first")
    if r.frontier is not None:
        if Path(r.frontier.repository).resolve() != root.resolve():
            raise ValueError("project frontier belongs to another repository")
        return
    git(root, "rev-parse", "--verify", "HEAD")
    engine.append(
        EventType.RESEARCH_FRONTIER_INITIALIZED,
        FrontierInitialized(
            repository=str(root.resolve()),
            basis_ref=str(engine.state.decisions[0].id),
            exploration_fraction=exploration_fraction,
        ),
    )


def apply_discovery(engine: ResearchEngine, root: Path, batch: DiscoveryBatch) -> None:
    r = engine.state.research
    if r is None or r.frontier is None or Path(r.frontier.repository).resolve() != root.resolve():
        raise ValueError("agent frontier/root not configured")
    if batch.based_on_version != engine.state.event_version:
        raise ValueError("stale discovery packet; inspect current evidence and rebase")
    if batch.basis_ref not in canonical_basis(engine.state):
        raise ValueError("discovery requires canonical user decision or evidence basis")
    if not batch.findings and not batch.no_new_findings_reason.strip():
        raise ValueError("discovery must report findings or justify no new findings")
    if batch.findings and not batch.sources:
        raise ValueError("new findings require exact source anchors")
    if (
        not batch.findings
        and not batch.proposals
        and r.frontier.discoveries
        and r.frontier.discoveries[-1].review_count == len(r.reviews)
    ):
        raise ValueError("unchanged discovery epoch; new evidence or strategy required")
    validate_graph((*r.nodes, *(f.node for f in batch.findings)))
    entries: list[tuple[EventType, EventPayload]] = []
    source_ids = []
    for index, source in enumerate(batch.sources):
        safe_input(root, source.path)
        raw = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "show",
                "--end-of-options",
                f"{source.revision}:{source.path}",
            ],
            check=True,
            capture_output=True,
        ).stdout
        lines = raw.splitlines()
        if source.line > len(lines) or lines[source.line - 1] != source.quote.encode("utf8"):
            raise ValueError("source quote does not byte-match pinned Git line")
        eid = stable_id(engine.project_id, f"{batch.id}:source:{index}")
        source_ids.append(eid)
        entries.append(
            (
                EventType.EVIDENCE_RECORDED,
                EvidenceRecordedPayload(
                    evidence_id=eid,
                    kind=EvidenceKind.STATIC_INSPECTION,
                    summary=source.quote,
                    provenance_source=f"git:{source.revision}:{source.path}:{source.line}",
                    provenance_scope="document observation only; not scientific validation",
                    provenance_created_at=utc_now(),
                    independently_verified=True,
                ),
            )
        )
    for f in batch.findings:
        h = f.hypothesis
        if (
            len({a.strip() for a in f.alternatives if a.strip()}) < 2
            or h.status is not HypothesisStatus.PROPOSED
            or h.supporting_evidence
            or h.contradicting_evidence
            or not h.predictions
            or not h.falsification_conditions
            or h.superseded_by is not None
            or h.hypothesis_id != f.node.hypothesis_id
            or h.statement != f.node.label
            or f.node.basis != batch.basis_ref
            or f.unknown.unknown_id != f.question.unknown_id
            or f.question.topic_key != f.topic_key
            or f.question.facet != f.facet
            or h.hypothesis_id not in f.question.hypothesis_ids
        ):
            raise ValueError("new claims/unknowns must be scoped falsifiable unverified proposals")
        entries.extend(
            ((EventType.HYPOTHESIS_CREATED, h), (EventType.UNKNOWN_REGISTERED, f.unknown))
        )
    if batch.findings:
        entries.append(
            (
                EventType.RESEARCH_NODE_ADDED,
                ResearchNodeAdded(
                    node=batch.findings[0].node,
                    companion_nodes=tuple(f.node for f in batch.findings[1:]),
                ),
            )
        )
    tasks: list[TaskId] = [t.id for t in engine.state.tasks]
    for experiment in batch.proposals:
        p = experiment.proposal
        c = experiment.contract
        validate_contract(root, c)
        if p.id != c.proposal_id or p.input_revision != c.fingerprint():
            raise ValueError("proposal/measurement input binding mismatch")
        tid = task_id(engine.project_id, p.id)
        tasks.append(tid)
        entries.extend(
            (
                (EventType.TASK_CREATED, TaskCreatedPayload(task_id=tid, objective=p.title)),
                (
                    EventType.TASK_STATUS_CHANGED,
                    TaskStatusChangedPayload(task_id=tid, status=TaskStatus.READY),
                ),
                (EventType.PROPOSAL_ADDED, ProposalAdded(proposal=p, basis_ref=batch.basis_ref)),
            )
        )
    if batch.proposals:
        previous = engine.state.current_plan
        kind = (
            PlanBasisKind.DECISION
            if batch.basis_ref in {str(d.id) for d in engine.state.decisions}
            else PlanBasisKind.EVIDENCE
            if batch.basis_ref in {str(e.id) for e in engine.state.evidence}
            else PlanBasisKind.REQUIREMENT
        )
        entries.append(
            (
                EventType.PLAN_VERSION_CREATED,
                PlanVersionCreatedPayload(
                    version=len(engine.state.plans) + 1,
                    task_ids=tuple(tasks),
                    dependencies=tuple(
                        PlanDependencyPayload(predecessor=d.predecessor, successor=d.successor)
                        for d in previous.dependencies
                    )
                    if previous
                    else (),
                    basis=(PlanBasisPayload(kind=kind, reference_id=batch.basis_ref),),
                ),
            )
        )
    entries.append(
        (
            EventType.RESEARCH_FRONTIER_DISCOVERED,
            FrontierDiscovered(
                audit=DiscoveryAudit(
                    id=batch.id,
                    based_on_version=batch.based_on_version,
                    review_count=len(r.reviews),
                    summary=batch.summary,
                    source_evidence_ids=tuple(source_ids),
                ),
                questions=tuple(
                    f.question.model_copy(update={"alternatives": f.alternatives})
                    for f in batch.findings
                ),
                contracts=tuple(p.contract for p in batch.proposals),
            ),
        )
    )
    engine.append_batch(tuple(entries))
