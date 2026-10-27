"""End-to-end regression tests for opt-in, evidence-gated problem exploration."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_research_plugin import observe, reflect, register, setup_store, validate

from epipilot.research.cli import _next_action, main
from epipilot.research.exploration import readiness
from epipilot.research.models import ExplorationStage, Proposal, ProposalKind
from epipilot.research.problem_profile import problem_profile
from epipilot.research.store import ResearchStore


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "target"
    root.mkdir()
    for argv in (
        ("init", "-q"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "EpiPilot Tests"),
    ):
        subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)
    (root / "config.json").write_text('{"seed":42}', encoding="utf-8")
    (root / "data.json").write_text('{"split":"train"}', encoding="utf-8")
    (root / "README.md").write_text("Controlled synthetic probe", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True, capture_output=True)
    return root


def command(root: Path, *args: str) -> int:
    return main(["--root", str(root), *args])


def optimize_proposal(store: ResearchStore) -> None:
    store.add(
        "proposal_added",
        Proposal(
            id="OPT",
            title="Optimize learned algorithm",
            kind=ProposalKind.OPTIMIZE,
            rationale="Use diagnostics to select a learned algorithm",
            acceptance="Matched improvement on the fixed development evaluation",
            claim_ids=["C"],
            information_gain=0.2,
            expected_improvement=0.9,
            estimated_cost=1,
            risk=0.1,
        ),
    )


def test_mode_is_opt_in_and_gate_is_enforced_beyond_planner(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = setup_store(repo, budget=10)
    optimize_proposal(store)
    state, original = store.load()
    assert state.exploration is None
    assert state.exploration_remaining == 0
    assert command(repo, "explore", "status") == 0
    assert '"enabled": false' in capsys.readouterr().out
    assert command(repo, "explore", "enable", "--budget-share", "0.2") == 0
    state, _ = store.load()
    assert state.exploration is not None and state.exploration.stage is ExplorationStage.EXPLORATION
    assert state.exploration_remaining == pytest.approx(2)
    assert _next_action(state)["task"]["proposal"]["id"] == "P"
    assert command(repo, "plan", "--id", "OPT") == 2
    with pytest.raises(ValueError, match="exploration phase blocks"):
        register(store, repo, proposal_id="OPT")
    assert command(repo, "explore", "probes") == 0
    assert command(repo, "explore", "profile") == 0
    assert store.load()[1] == original + 1
    assert command(repo, "explore", "disable", "--reason", "Explicit opt out of diagnosis") == 0
    assert store.load()[0].exploration is not None
    assert _next_action(store.load()[0])["status"] == "ready_for_agent"
    assert command(repo, "plan", "--id", "OPT") == 0


def test_budget_cap_shared_by_scout_and_exploratory_experiments(
    repo: Path,
) -> None:
    store = setup_store(repo, budget=10)
    assert command(repo, "explore", "enable", "--budget-share", "0.2") == 0
    register(store, repo, max_cost=1.5)
    store.append("experiment_started", {"contract_id": "PRE-1"})
    assert store.load()[0].exploration_remaining == pytest.approx(0.5)
    version = store.load()[1]
    assert command(repo, "scout", "start", "--operation", "S1", "--reserve", "0.6") == 2
    assert store.load()[1] == version
    store.append(
        "experiment_aborted",
        {"contract_id": "PRE-1", "actual_cost": 0.5, "reason": "Probe was interrupted"},
    )
    assert command(repo, "scout", "start", "--operation", "S1", "--reserve", "1.4") == 0
    state, _ = store.load()
    assert state.exploration_spent == pytest.approx(0.5)
    assert state.exploration_reserved == pytest.approx(1.4)
    assert state.exploration_remaining == pytest.approx(0.1)
    (repo / "scout-submission.json").write_text(
        json.dumps(
            {
                "operation_id": "S1",
                "actual_cost": 1.9,
                "status": "failed",
                "summary": "Exploration interrupted after consuming more than its reservation",
                "candidates": [],
            }
        ),
        encoding="utf-8",
    )
    assert command(repo, "scout", "submit", "--file", "scout-submission.json") == 0
    state, _ = store.load()
    assert state.exploration_spent == pytest.approx(2.4)
    assert state.exploration_remaining == 0
    assert readiness(state)["status"] == "budget_limited"
    assert _next_action(state)["status"] == "exploration_budget_limited"
    assert command(repo, "explore", "advance", "--reason", "Budget cap reached") == 2
    assert (
        command(
            repo,
            "explore",
            "advance",
            "--override",
            "--reason",
            "Authorized optimization with incomplete diagnostics",
        )
        == 0
    )


def test_verified_problem_knowledge_guides_algorithms_and_counterevidence_stales_it(
    repo: Path,
) -> None:
    store = setup_store(repo, budget=10)
    optimize_proposal(store)
    assert command(repo, "explore", "enable", "--budget-share", "0.6") == 0
    assert (
        command(
            repo,
            "explore",
            "guide",
            "--title",
            "Conditional route preference",
            "--dimension",
            "conditional_dependence",
            "--scope",
            "v1",
            "--claim",
            "C",
            "--recommend",
            "Prefer a light conditional gate",
            "--reason",
            "Needs a validated paired test before giving guidance",
        )
        == 2
    )
    assert not store.load()[0].problem_insights
    register(store, repo)
    observe(store, repo, score=0.9, outcome="improved")
    assert validate(repo) == 0
    reflect(store)
    store.append(
        "unknown_resolved",
        {
            "unknown_id": "U",
            "evidence_id": "EV-1",
            "rationale": "Qualified evidence supports this scoped decision",
        },
    )
    assert readiness(store.load()[0])["ready"] is True
    assert _next_action(store.load()[0])["status"] == "exploration_ready"
    assert (
        command(
            repo,
            "explore",
            "guide",
            "--title",
            "Conditional route preference",
            "--dimension",
            "conditional_dependence",
            "--scope",
            "v1",
            "--claim",
            "C",
            "--recommend",
            "Prefer a light conditional gate",
            "--reason",
            "A validated matched experiment supports the bounded claim",
        )
        == 0
    )
    profile = problem_profile(store.load()[0])
    assert profile["dimensions"][0]["insights"][0]["current"] is True
    assert (
        command(
            repo,
            "explore",
            "guide",
            "--title",
            "Unsupported universal route preference",
            "--dimension",
            "conditional_dependence",
            "--scope",
            "all tasks",
            "--claim",
            "C",
            "--recommend",
            "Use this everywhere",
            "--reason",
            "Overbroad advice must be rejected by the canonical store",
        )
        == 2
    )
    assert command(repo, "explore", "advance", "--reason", "Critical scoped unknown resolved") == 0
    assert store.load()[0].exploration is not None
    assert store.load()[0].exploration.stage is ExplorationStage.OPTIMIZATION
    assert _next_action(store.load()[0])["task"]["proposal"]["id"] == "OPT"

    store.add(
        "proposal_added",
        Proposal(
            id="P2",
            title="Matched counterexample for conditional routing",
            kind=ProposalKind.EXPLORE,
            rationale="Challenge the bounded route preference under the same conditions",
            acceptance="Strict paired reverse response",
            unknown_ids=["U"],
            claim_ids=["C"],
            information_gain=0.9,
            expected_improvement=0.1,
            estimated_cost=1.0,
            risk=0.1,
        ),
    )
    register(store, repo, contract_id="PRE-2", proposal_id="P2")
    observe(
        store,
        repo,
        contract_id="PRE-2",
        experiment_id="EXP-2",
        evidence_id="EV-2",
        score=0.4,
        outcome="no_gain",
    )
    assert validate(repo, "EXP-2") == 0
    reflect(store, verdict="refuted", evidence_id="EV-2")
    state, _ = store.load()
    assert state.unknowns["U"].status == "open"
    assert state.claims["C"].status.value == "inconclusive"
    assert problem_profile(state)["dimensions"][0]["insights"][0]["status"] == "stale"
    assert _next_action(state)["status"] == "exploration_reconsideration_required"
    with pytest.raises(ValueError, match="new counterevidence"):
        register(store, repo, contract_id="PRE-OPT", proposal_id="OPT")
    assert (
        command(repo, "explore", "reopen", "--reason", "Matched counterexample reopened claim C")
        == 0
    )
    assert store.load()[0].exploration.stage is ExplorationStage.EXPLORATION


def test_diagnostic_template_priority_and_read_only_queries(repo: Path) -> None:
    store = setup_store(repo)
    # The new templates are suggestions; they never create research actions or evidence.
    state, before = store.load()
    assert command(repo, "explore", "probes", "--dimension", "evaluation_reliability") == 0
    assert command(repo, "explore", "profile") == 0
    assert state == store.load()[0] and store.load()[1] == before
    assert not state.problem_insights
    assert command(repo, "explore", "enable", "--budget-share", "0") == 2
    assert store.load()[1] == before


def test_stage_transition_refuses_pending_frozen_work(repo: Path) -> None:
    store = setup_store(repo)
    register(store, repo)
    assert command(repo, "explore", "enable") == 2
    state, _ = store.load()
    assert state.exploration is None
    store.append(
        "experiment_aborted",
        {"contract_id": "PRE-1", "actual_cost": 0.0, "reason": "Cancel unstarted run"},
    )
    assert command(repo, "explore", "enable") == 0

def test_blocked_experiment_preempts_new_exploration_design(repo: Path) -> None:
    store = setup_store(repo, budget=10)
    assert command(repo, "explore", "enable", "--budget-share", "0.5") == 0
    register(store, repo)
    observe(store, repo)
    (repo / "results" / "metrics.json").unlink()
    assert validate(repo) == 2
    state, _ = store.load()
    assert _next_action(state)["status"] == "blocked"
