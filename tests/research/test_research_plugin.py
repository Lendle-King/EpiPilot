"""Research plugin tests: evidence gates, replay, budget, dependency and CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from epipilot.research.cli import main
from epipilot.research.models import (
    Claim,
    EvidenceRecord,
    Experiment,
    Proposal,
    ProposalKind,
    ResearchGoal,
    Unknown,
)
from epipilot.research.planner import rank_proposals, research_phase
from epipilot.research.store import ResearchStore


def setup_store(tmp_path: Path, budget: float = 10.0) -> ResearchStore:
    store = ResearchStore(tmp_path / ".research")
    store.add(
        "goal_set",
        ResearchGoal(statement="Identify bottlenecks and improve an algorithm", budget=budget),
    )
    store.add(
        "unknown_added",
        Unknown(id="UNK-1", question="Which mechanism causes failures?", importance=1.0),
    )
    store.add(
        "claim_added",
        Claim(id="CLM-1", statement="Ranking is the bottleneck", scope="snapshot-v1"),
    )
    store.add(
        "proposal_added",
        Proposal(
            id="ACT-1",
            title="Matched intervention",
            kind=ProposalKind.EXPLORE,
            rationale="Separate ranking and feature effects",
            acceptance="Matched benchmark produces comparable metrics",
            unknown_ids=["UNK-1"],
            claim_ids=["CLM-1"],
            information_gain=0.9,
            expected_improvement=0.1,
            unlock_value=0.8,
            novelty=0.8,
            estimated_cost=1.0,
            risk=0.1,
        ),
    )
    return store


def submit_observation(store: ResearchStore, cost: float = 0.5) -> None:
    experiment = Experiment(
        id="EXP-1",
        proposal_id="ACT-1",
        summary="Benchmark result; interpretation pending",
        outcome="inconclusive",
        actual_cost=cost,
        git_revision="a" * 40,
        config_ref="configs/ablation.json",
        dataset_ref="frozen/split-v1",
        evidence_id="EV-1",
        artifact_refs=["runs/ablation.json"],
    )
    evidence = EvidenceRecord(
        id="EV-1",
        experiment_id="EXP-1",
        summary=experiment.summary,
        artifact_refs=experiment.artifact_refs,
    )
    store.append(
        "experiment_recorded",
        {
            "experiment": experiment.model_dump(mode="json"),
            "evidence": evidence.model_dump(mode="json"),
        },
    )


def test_replay_is_deterministic_and_scoped(tmp_path: Path) -> None:
    store = setup_store(tmp_path)
    first, version = store.load()
    second, repeated_version = ResearchStore(store.root).load()
    assert version == 4 == repeated_version
    assert first == second
    assert first.goal is not None
    assert first.goal.user_approved
    assert len(first.unknowns) == len(first.claims) == len(first.proposals) == 1


def test_agent_observation_cannot_promote_claim(tmp_path: Path) -> None:
    store = setup_store(tmp_path)
    submit_observation(store)
    with pytest.raises(ValueError, match="independently checked"):
        store.append(
            "claim_reviewed",
            {"claim_id": "CLM-1", "status": "supported", "evidence_id": "EV-1"},
        )
    state, version = store.load()
    assert version == 5
    assert state.claims["CLM-1"].status == "hypothesis"
    assert state.proposals["ACT-1"].status == "awaiting_verification"


def test_verified_evidence_allows_provisional_review(tmp_path: Path) -> None:
    store = setup_store(tmp_path)
    submit_observation(store)
    store.append(
        "verification_recorded",
        {
            "evidence_id": "EV-1",
            "check_argv": [sys.executable, "-m", "pytest"],
            "check_output_sha256": "deadbeef",
        },
    )
    store.append(
        "claim_reviewed",
        {"claim_id": "CLM-1", "status": "inconclusive", "evidence_id": "EV-1"},
    )
    store.append("unknown_resolved", {"unknown_id": "UNK-1", "evidence_id": "EV-1"})
    state, _ = store.load()
    assert state.proposals["ACT-1"].status == "completed"
    assert state.evidence["EV-1"].independently_verified
    assert state.claims["CLM-1"].status == "inconclusive"
    assert state.unknowns["UNK-1"].status == "resolved"


def test_budget_fails_closed_without_new_event(tmp_path: Path) -> None:
    store = setup_store(tmp_path, budget=0.1)
    with pytest.raises(ValueError, match="budget"):
        submit_observation(store, cost=0.5)
    state, version = store.load()
    assert version == 4
    assert not state.experiments
    assert state.spent == 0


def test_unknown_links_are_not_guessed(tmp_path: Path) -> None:
    store = setup_store(tmp_path)
    with pytest.raises(ValueError, match="unknown unknowns"):
        store.add(
            "proposal_added",
            Proposal(
                id="ACT-BAD",
                title="Unknown experiment",
                kind=ProposalKind.EXPLORE,
                rationale="Reason",
                acceptance="Check",
                unknown_ids=["UNK-DOES-NOT-EXIST"],
                information_gain=0.8,
                expected_improvement=0.1,
                unlock_value=0,
                novelty=0.2,
                estimated_cost=1.0,
                risk=0,
            ),
        )


def test_dependencies_are_gated_and_lookahead_is_used(tmp_path: Path) -> None:
    store = setup_store(tmp_path)
    store.add(
        "proposal_added",
        Proposal(
            id="ACT-2",
            title="Follow-on optimization",
            kind=ProposalKind.OPTIMIZE,
            rationale="Depends on identifying mechanism",
            acceptance="Validated benchmark",
            depends_on=["ACT-1"],
            claim_ids=["CLM-1"],
            information_gain=0.2,
            expected_improvement=1.0,
            unlock_value=0.5,
            novelty=0.2,
            estimated_cost=1,
            risk=0.2,
        ),
    )
    state, _ = store.load()
    ranked = rank_proposals(state)
    assert [x.proposal.id for x in ranked] == ["ACT-1"]
    assert "discounted_follow_on=" in ranked[0].rationale
    assert research_phase(state) == "exploration"


def test_corrupt_event_stream_fails_closed(tmp_path: Path) -> None:
    store = setup_store(tmp_path)
    with store.events_path.open("a", encoding="utf8") as stream:
        stream.write('{"schema_version":1,"sequence":999,"kind":"goal_set","payload":{}}\n')
    with pytest.raises(ValueError, match="out-of-order"):
        store.load()


def test_writer_lock_prevents_race(tmp_path: Path) -> None:
    store = setup_store(tmp_path)
    store.lock_path.write_text("already held", encoding="utf8")
    with pytest.raises(RuntimeError, match="locked"):
        store.add("unknown_added", Unknown(id="B", question="Unanswered"))
    assert store.lock_path.exists()


def test_cli_agent_iteration_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(tmp_path)]
    assert main([*root, "init", "--goal", "Investigate", "--budget", "2"]) == 0
    assert main([*root, "unknown", "add", "--id", "U", "--question", "Why?"]) == 0
    assert (
        main(
            [
                *root,
                "propose",
                "--id",
                "P",
                "--title",
                "Mechanism test",
                "--kind",
                "explore",
                "--rationale",
                "Controls confounding",
                "--acceptance",
                "Independent output check",
                "--unknown",
                "U",
                "--ig",
                "0.9",
                "--gain",
                "0.2",
                "--cost",
                "1",
            ]
        )
        == 0
    )
    assert main([*root, "iterate"]) == 0
    assert '"status": "ready_for_agent"' in capsys.readouterr().out
    assert (
        main(
            [
                *root,
                "record",
                "--proposal",
                "P",
                "--summary",
                "Synthetic checked result",
                "--outcome",
                "inconclusive",
                "--cost",
                "0.4",
                "--revision",
                "a" * 40,
                "--config",
                "cfg-v1",
                "--dataset",
                "split-v1",
                "--artifact",
                "result.json",
            ]
        )
        == 0
    )
    state, _ = ResearchStore(tmp_path / ".research").load()
    experiment = next(iter(state.experiments.values()))
    assert (
        main(
            [
                *root,
                "verify",
                "--experiment",
                experiment.id,
                "--check",
                sys.executable,
                "-c",
                "print('independent check completed')",
            ]
        )
        == 0
    )
    state, _ = ResearchStore(tmp_path / ".research").load()
    assert state.evidence[experiment.evidence_id].independently_verified
    assert (
        main([*root, "unknown", "resolve", "--id", "U", "--evidence", experiment.evidence_id]) == 0
    )


def test_cli_failed_check_does_not_advance(tmp_path: Path) -> None:
    store = setup_store(tmp_path)
    submit_observation(store)
    result = main(
        [
            "--root",
            str(tmp_path),
            "verify",
            "--experiment",
            "EXP-1",
            "--check",
            sys.executable,
            "-c",
            "raise SystemExit(3)",
        ]
    )
    assert result == 2
    state, version = store.load()
    assert version == 6
    assert not state.evidence["EV-1"].independently_verified
    assert state.proposals["ACT-1"].status == "awaiting_verification"


def test_onboarding_does_not_infer_claims(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Claims may be unverified", encoding="utf8")
    (tmp_path / ".env").write_text("SECRET", encoding="utf8")
    result = main(["--root", str(tmp_path), "onboard"])
    assert result == 0
    state, _ = ResearchStore(tmp_path / ".research").load()
    assert not state.claims
    assert state.onboarding[0].relevant_files == ["README.md"]
