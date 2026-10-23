"""Scoped Scout discovery: paid reservation, provenance, explicit review, no false facts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from epipilot.research.cli import main
from epipilot.research.models import ClaimStatus, ResearchGoal
from epipilot.research.scout_core import frontier_fingerprint
from epipilot.research.scout_models import ScoutCandidateStatus, ScoutStatus
from epipilot.research.store import ResearchStore

QUOTE = "Matched seeds suggest the ranking stage is an unresolved failure mechanism."
DOC = "# Prior experimental findings\n" + QUOTE + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "example"
    root.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.name", "EpiPilot Tests"],
        ["config", "user.email", "epipilot@example.invalid"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "docs").mkdir()
    (root / "docs" / "results.md").write_text(DOC, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "pinned source"], cwd=root, check=True, capture_output=True
    )
    ResearchStore(root / ".research").add(
        "goal_set",
        ResearchGoal(statement="Identify and test the dominant failure mechanism", budget=2),
    )
    return root


def source() -> dict[str, object]:
    return {
        "path": "docs/results.md",
        "line": 2,
        "exact_quote": QUOTE,
        "sha256": hashlib.sha256(DOC.encode("utf-8")).hexdigest(),
    }


def candidate(operation: str, *, anchor: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "id": "ranking-mechanism",
        "operation_id": operation,
        "question": "Does ranking cause the observed task failures?",
        "hypothesis": "Ranking is responsible for the observed failures.",
        "scope": "held-out matched-seed evaluation",
        "title": "Matched ranking counterfactual ablation",
        "rationale": "Isolate ranking quality from controller and task complexity.",
        "acceptance": "Frozen matched benchmark shows a reproducible success difference",
        "kind": "explore",
        "source": anchor or source(),
        "information_gain": 0.8,
        "expected_improvement": 0.2,
        "estimated_cost": 0.6,
        "risk": 0.1,
    }


def put_submission(
    root: Path,
    operation: str,
    *,
    leads: list[dict[str, object]] | None = None,
    cost: float = 0.3,
    status: str = "completed",
) -> Path:
    path = root / "scout-submission.json"
    path.write_text(
        json.dumps(
            {
                "operation_id": operation,
                "actual_cost": cost,
                "status": status,
                "summary": "Reviewed pinned source and independently considered alternatives",
                "candidates": leads if leads is not None else [candidate(operation)],
            }
        ),
        encoding="utf-8",
    )
    return path


def cmd(root: Path, *args: str) -> int:
    return main(["--root", str(root), "scout", *args])


def test_scout_lifecycle_is_budgeted_and_review_is_not_evidence(repo: Path) -> None:
    store = ResearchStore(repo / ".research")
    _, version = store.load()
    assert cmd(repo, "prepare") == 0
    assert store.load()[1] == version
    assert cmd(repo, "start", "--operation", "S1", "--reserve", "0.5") == 0
    state, _ = store.load()
    assert state.remaining == pytest.approx(1.5)
    assert state.reserved == pytest.approx(0.5)
    submission = put_submission(repo, "S1")
    assert cmd(repo, "submit", "--file", str(submission)) == 0
    state, _ = store.load()
    assert state.reserved == 0
    assert state.spent == pytest.approx(0.3)
    assert state.scout_runs["S1"].status is ScoutStatus.COMPLETED
    assert state.scout_candidates["ranking-mechanism"].status is ScoutCandidateStatus.PENDING
    assert not state.claims and not state.evidence
    assert (
        cmd(
            repo,
            "review",
            "--candidate",
            "ranking-mechanism",
            "--decision",
            "accepted",
            "--reason",
            "Novel and cheaply testable ranking mechanism",
        )
        == 0
    )
    state, last = store.load()
    assert last == 4
    assert state.claims["CLM-ranking-mechanism"].status is ClaimStatus.HYPOTHESIS
    assert (
        "source_text_is_not_scientific_evidence"
        in (state.claims["CLM-ranking-mechanism"].assumptions[0])
    )
    assert state.proposals["ACT-ranking-mechanism"].status == "proposed"
    assert not state.evidence
    assert ResearchStore(store.root).load()[0] == state
    assert state.remaining == pytest.approx(1.7)


def test_forged_source_is_rejected_without_consuming_reserved_budget(repo: Path) -> None:
    store = ResearchStore(repo / ".research")
    assert cmd(repo, "start", "--operation", "S1", "--reserve", "0.5") == 0
    invalid = candidate("S1", anchor={**source(), "exact_quote": "Invented causal conclusion"})
    bad = put_submission(repo, "S1", leads=[invalid])
    version = store.load()[1]
    assert cmd(repo, "submit", "--file", str(bad)) == 2
    assert store.load()[1] == version
    assert store.load()[0].scout_runs["S1"].status is ScoutStatus.RUNNING
    put_submission(repo, "S1", leads=[], cost=0.25, status="failed")
    assert cmd(repo, "submit", "--file", str(bad)) == 0
    assert store.load()[0].spent == pytest.approx(0.25)
    assert store.load()[0].reserved == 0


def test_noop_epoch_cannot_repeat_without_new_research_information(repo: Path) -> None:
    store = ResearchStore(repo / ".research")
    initial_epoch = frontier_fingerprint(store.load()[0])
    assert cmd(repo, "start", "--operation", "S1", "--reserve", "0.5") == 0
    put_submission(repo, "S1", leads=[], cost=0.2)
    assert cmd(repo, "submit", "--file", "scout-submission.json") == 0
    state, version = store.load()
    assert frontier_fingerprint(state) == initial_epoch
    assert cmd(repo, "start", "--operation", "S2", "--reserve", "0.5") == 2
    assert store.load()[1] == version
    assert state.spent == pytest.approx(0.2)


def test_scout_overrun_is_accounted_and_blocks_new_reservations(repo: Path) -> None:
    store = ResearchStore(repo / ".research")
    assert cmd(repo, "start", "--operation", "S1", "--reserve", "0.5") == 0
    put_submission(repo, "S1", leads=[], cost=2.3, status="failed")
    assert cmd(repo, "submit", "--file", "scout-submission.json") == 0
    state, _ = store.load()
    assert state.overrun == pytest.approx(0.3)
    assert state.remaining == 0
    assert cmd(repo, "start", "--operation", "S2", "--reserve", "0.1") == 2


def test_duplicate_review_cannot_mutate_claims_twice(repo: Path) -> None:
    store = ResearchStore(repo / ".research")
    assert cmd(repo, "start", "--operation", "S1", "--reserve", "0.5") == 0
    put_submission(repo, "S1")
    assert cmd(repo, "submit", "--file", "scout-submission.json") == 0
    assert (
        cmd(
            repo,
            "review",
            "--candidate",
            "ranking-mechanism",
            "--decision",
            "accepted",
            "--reason",
            "Explicit human acceptance",
        )
        == 0
    )
    version = store.load()[1]
    assert (
        cmd(
            repo,
            "review",
            "--candidate",
            "ranking-mechanism",
            "--decision",
            "accepted",
            "--reason",
            "Duplicate replayed acceptance",
        )
        == 2
    )
    assert store.load()[1] == version
    assert len(store.load()[0].claims) == 1


def test_reject_keeps_research_frontier_unchanged(repo: Path) -> None:
    store = ResearchStore(repo / ".research")
    before = frontier_fingerprint(store.load()[0])
    assert cmd(repo, "start", "--operation", "S1", "--reserve", "0.5") == 0
    put_submission(repo, "S1")
    assert cmd(repo, "submit", "--file", "scout-submission.json") == 0
    assert (
        cmd(
            repo,
            "review",
            "--candidate",
            "ranking-mechanism",
            "--decision",
            "rejected",
            "--reason",
            "Historical context is too weak",
        )
        == 0
    )
    assert store.load()[0].scout_candidates["ranking-mechanism"].status is (
        ScoutCandidateStatus.REJECTED
    )
    assert frontier_fingerprint(store.load()[0]) == before
    assert not store.load()[0].unknowns


def test_stale_operation_source_and_invalid_anchor_fail_closed(repo: Path) -> None:
    store = ResearchStore(repo / ".research")
    assert cmd(repo, "start", "--operation", "S1", "--reserve", "0.5") == 0
    doc = repo / "docs" / "results.md"
    doc.write_text("changed tracked source\n", encoding="utf-8")
    put_submission(repo, "S1")
    assert cmd(repo, "submit", "--file", "scout-submission.json") == 2
    assert store.load()[0].scout_runs["S1"].status is ScoutStatus.RUNNING
    # A tracked checkout change is not scientific evidence. Restore the pinned source.
    subprocess.run(
        ["git", "checkout", "--", "docs/results.md"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    forged = candidate("S1", anchor={**source(), "path": "../outside.md"})
    put_submission(repo, "S1", leads=[forged])
    assert cmd(repo, "submit", "--file", "scout-submission.json") == 2
    assert store.load()[0].scout_runs["S1"].status is ScoutStatus.RUNNING
