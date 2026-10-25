"""Regression tests for research provenance, cost, replay and autonomous handoffs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from epipilot.research.cli import main
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
    ResearchEvent,
    ResearchGoal,
    ResearchState,
    Unknown,
)
from epipilot.research.planner import rank_proposals, research_phase
from epipilot.research.store import ResearchStore
from epipilot.research.validation import (
    artifact_metrics,
    optional_fingerprint,
    require_clean_revision,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    repo = tmp_path / "target"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test User"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "config.json").write_text('{"seed":42}', encoding="utf-8")
    (repo / "data.json").write_text('{"split":"train"}', encoding="utf-8")
    (repo / "README.md").write_text("Synthetic research repository", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True, capture_output=True)
    return repo


def setup_store(root: Path, budget: float = 4.0) -> ResearchStore:
    store = ResearchStore(root / ".research")
    store.add("goal_set", ResearchGoal(statement="Investigate algorithm", budget=budget))
    store.add("unknown_added", Unknown(id="U", question="Does the intervention improve quality?"))
    store.add("claim_added", Claim(id="C", statement="Intervention improves quality", scope="v1"))
    store.add(
        "proposal_added",
        Proposal(
            id="P",
            title="Matched intervention",
            kind=ProposalKind.EXPLORE,
            rationale="Separate treatment from baseline",
            acceptance="Frozen score threshold",
            unknown_ids=["U"],
            claim_ids=["C"],
            information_gain=0.9,
            expected_improvement=0.3,
            unlock_value=0.7,
            novelty=0.4,
            estimated_cost=1.0,
            risk=0.1,
        ),
    )
    return store


def register(
    store: ResearchStore,
    root: Path,
    contract_id: str = "PRE-1",
    proposal_id: str = "P",
    max_cost: float = 1.0,
) -> ExperimentContract:
    contract = ExperimentContract(
        id=contract_id,
        proposal_id=proposal_id,
        git_revision=require_clean_revision(root),
        config_ref="config.json",
        dataset_ref="data.json",
        config_sha256=optional_fingerprint(root, "config.json"),
        dataset_sha256=optional_fingerprint(root, "data.json"),
        baseline_ref="frozen baseline",
        controls=["same seed", "same train split"],
        decision_rule="score >= 0.8 supports claim",
        falsification_rule="score < 0.8 refutes claim",
        artifact_ref="results/metrics.json",
        metric_rules=[MetricRule(name="score", direction=MetricDirection.AT_LEAST, threshold=0.8)],
        verifier_argv=[sys.executable, "-m", "json.tool", "{artifact}"],
        max_cost=max_cost,
    )
    store.add("experiment_preregistered", contract)
    return contract


def observe(
    store: ResearchStore,
    root: Path,
    score: float = 0.9,
    outcome: str = "improved",
    cost: float = 0.8,
    contract_id: str = "PRE-1",
    experiment_id: str = "EXP-1",
    evidence_id: str = "EV-1",
) -> None:
    contract = store.load()[0].contracts[contract_id]
    store.append("experiment_started", {"contract_id": contract_id})
    result = root / "results" / "metrics.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps({"metrics": {"score": score}}), encoding="utf-8")
    exp = Experiment(
        id=experiment_id,
        contract_id=contract_id,
        proposal_id=contract.proposal_id,
        summary="Measured intervention on frozen evaluation",
        outcome=outcome,
        actual_cost=cost,
        git_revision=contract.git_revision,
        config_ref=contract.config_ref,
        dataset_ref=contract.dataset_ref,
        metrics={"score": score},
        artifact_refs=[contract.artifact_ref],
        evidence_id=evidence_id,
    )
    ev = EvidenceRecord(
        id=evidence_id,
        experiment_id=experiment_id,
        summary="Measured score",
        artifact_refs=[contract.artifact_ref],
    )
    store.append(
        "experiment_recorded",
        {"experiment": exp.model_dump(mode="json"), "evidence": ev.model_dump(mode="json")},
    )


def validate(root: Path, experiment_id: str = "EXP-1") -> int:
    return main(["--root", str(root), "verify", "--experiment", experiment_id])


def reflect(store: ResearchStore, verdict: str = "supported", evidence_id: str = "EV-1") -> None:
    store.append(
        "claim_reviewed",
        {
            "claim_id": "C",
            "status": verdict,
            "evidence_id": evidence_id,
            "rationale": "Measured against the pre-registered baseline and controls",
        },
    )


def test_full_evidence_gated_research_loop(root: Path) -> None:
    store = setup_store(root)
    register(store, root)
    observe(store, root)
    state, _ = store.load()
    assert state.proposals["P"].status == "awaiting_verification"
    assert not state.evidence["EV-1"].independently_verified
    with pytest.raises(ValueError, match="independently checked"):
        reflect(store)
    assert validate(root) == 0
    state, _ = store.load()
    assert state.evidence["EV-1"].measurement_validated
    assert state.evidence["EV-1"].scientific_verdict == "supported"
    assert state.contracts["PRE-1"].status is ContractStatus.COMPLETED
    reflect(store)
    store.append(
        "unknown_resolved",
        {
            "unknown_id": "U",
            "evidence_id": "EV-1",
            "rationale": "A validated comparison settles this bounded decision",
        },
    )
    state, count = store.load()
    assert state.claims["C"].status is ClaimStatus.SUPPORTED
    assert state.unknowns["U"].status == "resolved"
    assert state.spent == pytest.approx(0.8)
    assert count == 10
    assert state == ResearchStore(store.root).load()[0]


def test_inconclusive_measurements_cannot_close_an_unknown(root: Path) -> None:
    store = setup_store(root)
    register(store, root)
    observe(store, root, outcome="inconclusive")
    assert validate(root) == 0
    reflect(store, verdict="inconclusive")
    assert "EV-1" in store.load()[0].claims["C"].inconclusive_evidence_ids
    with pytest.raises(ValueError, match="conclusive reviewed claims"):
        store.append(
            "unknown_resolved",
            {"unknown_id": "U", "evidence_id": "EV-1", "rationale": "Not enough"},
        )
    assert store.load()[0].unknowns["U"].status == "open"


def test_refuting_result_is_scoped_and_reviewed(root: Path) -> None:
    store = setup_store(root)
    register(store, root)
    observe(store, root, score=0.5, outcome="no_gain")
    assert validate(root) == 0
    assert store.load()[0].evidence["EV-1"].scientific_verdict == "refuted"
    with pytest.raises(ValueError, match="validated, linked"):
        reflect(store, verdict="supported")
    reflect(store, verdict="refuted")
    assert store.load()[0].claims["C"].status is ClaimStatus.REFUTED


def test_conflicting_validated_evidence_reopens_uncertainty(root: Path) -> None:
    store = setup_store(root)
    register(store, root)
    observe(store, root)
    assert validate(root) == 0
    reflect(store)
    store.add(
        "proposal_added",
        Proposal(
            id="P2",
            title="Counterexample on same frozen split",
            kind=ProposalKind.EXPLORE,
            rationale="Seek counterevidence",
            acceptance="Bounded reverse result",
            unknown_ids=["U"],
            claim_ids=["C"],
            information_gain=0.9,
            expected_improvement=0.1,
            estimated_cost=1,
            risk=0.1,
        ),
    )
    register(store, root, contract_id="PRE-2", proposal_id="P2")
    observe(
        store,
        root,
        score=0.4,
        outcome="no_gain",
        contract_id="PRE-2",
        experiment_id="EXP-2",
        evidence_id="EV-2",
    )
    assert validate(root, "EXP-2") == 0
    reflect(store, verdict="refuted", evidence_id="EV-2")
    state, _ = store.load()
    assert state.claims["C"].status is ClaimStatus.INCONCLUSIVE
    assert state.claims["C"].supporting_evidence_ids == ["EV-1"]
    assert state.claims["C"].contradicting_evidence_ids == ["EV-2"]
    with pytest.raises(ValueError, match="conclusive reviewed claims"):
        store.append(
            "unknown_resolved",
            {"unknown_id": "U", "evidence_id": "EV-2", "rationale": "Contradiction remains"},
        )


def test_unrelated_evidence_cannot_validate_a_different_claim(root: Path) -> None:
    store = setup_store(root)
    store.add("claim_added", Claim(id="C2", statement="Unrelated mechanism", scope="other"))
    register(store, root)
    observe(store, root)
    assert validate(root) == 0
    with pytest.raises(ValueError, match="validated, linked"):
        store.append(
            "claim_reviewed",
            {
                "claim_id": "C2",
                "status": "supported",
                "evidence_id": "EV-1",
                "rationale": "Unrelated",
            },
        )
    with pytest.raises(ValueError, match="conclusive reviewed claims"):
        store.append(
            "unknown_resolved",
            {"unknown_id": "U", "evidence_id": "EV-1", "rationale": "No review yet"},
        )


def test_wrong_verifier_and_trivial_check_are_rejected(root: Path) -> None:
    store = setup_store(root)
    register(store, root)
    observe(store, root)
    assert (
        main(
            [
                "--root",
                str(root),
                "verify",
                "--experiment",
                "EXP-1",
                "--check",
                sys.executable,
                "-c",
                "print('ok')",
            ]
        )
        == 2
    )
    assert not store.load()[0].evidence["EV-1"].independently_verified
    assert (
        main(
            [
                "--root",
                str(root),
                "preregister",
                "--proposal",
                "P",
                "--baseline",
                "b",
                "--config",
                "config.json",
                "--dataset",
                "data.json",
                "--control",
                "same seeds",
                "--decision-rule",
                "score high",
                "--falsification",
                "score low",
                "--metric-rule",
                "score:at_least:0.8",
                "--artifact",
                "results/metrics.json",
                "--max-cost",
                "1",
                "--verifier",
                sys.executable,
                "-c",
                "print('ok')",
                "{artifact}",
            ]
        )
        == 2
    )


def test_metric_artifact_mismatch_blocks_verification_until_correction(root: Path) -> None:
    store = setup_store(root)
    register(store, root)
    observe(store, root)
    (root / "results/metrics.json").write_text('{"metrics": {"score": 0.4}}', encoding="utf-8")
    assert validate(root) == 2
    state, _ = store.load()
    assert state.contracts["PRE-1"].status is ContractStatus.BLOCKED
    assert state.evidence["EV-1"].failure_count == 1
    assert main(["--root", str(root), "iterate"]) == 0
    with pytest.raises(ValueError, match="experiment is not awaiting verification"):
        store.append(
            "verification_recorded",
            {
                "evidence_id": "EV-1",
                "check_argv": [sys.executable],
                "check_output_sha256": "deadbeef",
            },
        )
    store.append(
        "verification_retried",
        {"contract_id": "PRE-1", "reason": "Restored the correct metrics JSON"},
    )
    (root / "results/metrics.json").write_text('{"metrics": {"score": 0.9}}', encoding="utf-8")
    assert validate(root) == 0


def test_failed_verifier_does_not_create_verified_evidence(root: Path) -> None:
    store = setup_store(root)
    contract = register(store, root)
    observe(store, root)
    (root / "results/metrics.json").unlink()
    assert validate(root) == 2
    state, _ = store.load()
    assert not state.evidence["EV-1"].measurement_validated
    assert state.proposals["P"].status == "blocked"
    assert state.contracts[contract.id].status is ContractStatus.BLOCKED


def test_retry_requires_new_strategy_and_limits_attempts(root: Path) -> None:
    store = setup_store(root)
    register(store, root)
    observe(store, root)
    path = root / "results/metrics.json"
    path.unlink()
    for i in range(3):
        assert validate(root) == 2
        if i < 2:
            store.append(
                "verification_retried",
                {"contract_id": "PRE-1", "reason": f"new environment repair {i}"},
            )
    with pytest.raises(ValueError, match="repeated failures"):
        store.append(
            "verification_retried",
            {"contract_id": "PRE-1", "reason": "new environment repair 2"},
        )


def test_reservation_prevents_parallel_overspend(root: Path) -> None:
    store = setup_store(root, budget=2)
    register(store, root, max_cost=1.5)
    store.add(
        "proposal_added",
        Proposal(
            id="P2",
            title="Independent candidate",
            kind=ProposalKind.OPTIMIZE,
            rationale="Independent optimization",
            acceptance="Frozen metric check",
            claim_ids=["C"],
            information_gain=0.4,
            expected_improvement=0.8,
            estimated_cost=1.0,
            risk=0.1,
        ),
    )
    register(store, root, contract_id="PRE-2", proposal_id="P2", max_cost=1.5)
    store.append("experiment_started", {"contract_id": "PRE-1"})
    assert store.load()[0].reserved == pytest.approx(1.5)
    with pytest.raises(ValueError, match="insufficient budget"):
        store.append("experiment_started", {"contract_id": "PRE-2"})


def test_real_cost_overrun_is_recorded_not_discarded(root: Path) -> None:
    store = setup_store(root, budget=2)
    register(store, root, max_cost=1)
    observe(store, root, cost=3)
    state, _ = store.load()
    assert state.spent == 3
    assert state.overrun == 1
    assert state.remaining == 0
    assert validate(root) == 0


def test_aborting_releases_reservation_and_books_spent_cost(root: Path) -> None:
    store = setup_store(root, budget=3)
    register(store, root, max_cost=2)
    store.append("experiment_started", {"contract_id": "PRE-1"})
    assert store.load()[0].reserved == 2
    store.append(
        "experiment_aborted",
        {"contract_id": "PRE-1", "actual_cost": 0.4, "reason": "Hardware failure"},
    )
    state, _ = store.load()
    assert state.reserved == 0
    assert state.spent == pytest.approx(0.4)
    assert state.proposals["P"].status == "aborted"
    assert state.remaining == pytest.approx(2.6)


def test_changed_tracked_source_fails_closed(root: Path) -> None:
    store = setup_store(root)
    register(store, root)
    observe(store, root)
    (root / "config.json").write_text('{"seed":0}', encoding="utf-8")
    assert validate(root) == 2
    assert not store.load()[0].evidence["EV-1"].independently_verified
    subprocess.run(
        ["git", "checkout", "--", "config.json"], cwd=root, check=True, capture_output=True
    )
    store.append(
        "verification_retried",
        {"contract_id": "PRE-1", "reason": "Restored pinned configuration"},
    )
    assert validate(root) == 0


def test_invalid_artifact_path_and_nonfinite_metric(root: Path) -> None:
    with pytest.raises(ValidationError, match="artifact_ref"):
        ExperimentContract(
            id="bad",
            proposal_id="P",
            git_revision="a" * 40,
            config_ref="cfg",
            dataset_ref="data",
            baseline_ref="baseline",
            controls=["matched"],
            decision_rule="metric",
            falsification_rule="not metric",
            artifact_ref="../secret.json",
            metric_rules=[
                MetricRule(name="score", direction=MetricDirection.AT_LEAST, threshold=0.8)
            ],
            verifier_argv=["pytest", "{artifact}"],
            max_cost=1,
        )
    store = setup_store(root)
    contract = register(store, root)
    (root / contract.artifact_ref).parent.mkdir(parents=True, exist_ok=True)
    (root / contract.artifact_ref).write_text('{"metrics":{"score":NaN}}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        artifact_metrics(root, contract)


def test_duplicate_proposals_are_rejected(root: Path) -> None:
    store = setup_store(root)
    with pytest.raises(ValueError, match="duplicate research proposal"):
        store.add(
            "proposal_added",
            Proposal(
                id="new",
                title="Matched intervention",
                kind=ProposalKind.EXPLORE,
                rationale="same experiment",
                acceptance="same protocol",
                unknown_ids=["U"],
                claim_ids=["C"],
                information_gain=0.7,
                expected_improvement=0.2,
                estimated_cost=1,
                risk=0,
            ),
        )


def test_child_lookahead_needs_budget_and_all_other_dependencies(root: Path) -> None:
    store = setup_store(root, budget=2)
    store.add(
        "proposal_added",
        Proposal(
            id="P2",
            title="Follow-on optimization",
            kind=ProposalKind.OPTIMIZE,
            rationale="Build on parent",
            acceptance="Independent score check",
            depends_on=["P"],
            claim_ids=["C"],
            information_gain=0.1,
            expected_improvement=1,
            estimated_cost=1.8,
            risk=0,
        ),
    )
    state, _ = store.load()
    ranked = rank_proposals(state)
    assert [item.proposal.id for item in ranked] == ["P"]
    assert "discounted_follow_on=0.0000" in ranked[0].rationale


def test_no_open_unknown_means_optimization_phase() -> None:
    assert research_phase(ResearchState()) == "optimization"


def test_legacy_events_replay_but_are_not_new_scientific_evidence(root: Path) -> None:
    store = ResearchStore(root / ".research")
    events = [
        ResearchEvent(
            sequence=1,
            schema_version=1,
            kind="goal_set",
            payload=ResearchGoal(statement="Old", budget=2).model_dump(mode="json"),
        ),
        ResearchEvent(
            sequence=2,
            schema_version=1,
            kind="claim_added",
            payload=Claim(id="old", statement="Old result", scope="v1").model_dump(mode="json"),
        ),
    ]
    store.root.mkdir(parents=True, exist_ok=True)
    store.events_path.write_text(
        "".join(event.model_dump_json() + "\n" for event in events), encoding="utf-8"
    )
    state, count = store.load()
    assert count == 2
    assert state.claims["old"].status is ClaimStatus.HYPOTHESIS
    store.add("claim_added", Claim(id="new", statement="New claim", scope="v2"))
    assert store.load()[1] == 3


def test_bad_event_replay_and_writer_lock_fail_closed(root: Path) -> None:
    store = setup_store(root)
    with store.events_path.open("a", encoding="utf-8") as out:
        out.write('{"schema_version":2,"sequence":999,"kind":"goal_set","payload":{}}\n')
    with pytest.raises(ValueError, match="out-of-order"):
        store.load()
    with pytest.raises(ValueError, match="out-of-order"):
        store.add("claim_added", Claim(id="bad", statement="bad", scope="v1"))
    # Separate store tests single-writer behavior on an uncorrupted stream.
    other = ResearchStore(root / ".other-research")
    other.lock_path.parent.mkdir(parents=True)
    other.lock_path.write_text("held", encoding="utf-8")
    with pytest.raises(RuntimeError, match="locked"):
        other.add("goal_set", ResearchGoal(statement="G", budget=1))


def test_agent_cli_end_to_end(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = ["--root", str(root)]
    assert main([*path, "init", "--goal", "Optimize", "--budget", "4"]) == 0
    assert main([*path, "unknown", "add", "--id", "U", "--question", "Why?"]) == 0
    assert (
        main(
            [
                *path,
                "claim",
                "--id",
                "C",
                "--statement",
                "Score improves",
                "--scope",
                "v1",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                *path,
                "propose",
                "--id",
                "P",
                "--title",
                "Matched study",
                "--kind",
                "explore",
                "--rationale",
                "matched seed",
                "--acceptance",
                "score threshold",
                "--unknown",
                "U",
                "--claim",
                "C",
                "--ig",
                "0.8",
                "--gain",
                "0.2",
                "--cost",
                "1",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                *path,
                "preregister",
                "--proposal",
                "P",
                "--baseline",
                "baseline-v1",
                "--config",
                "config.json",
                "--dataset",
                "data.json",
                "--control",
                "same-seed",
                "--decision-rule",
                "score >= 0.8",
                "--falsification",
                "score < 0.8",
                "--metric-rule",
                "score:at_least:0.8",
                "--artifact",
                "metrics.json",
                "--max-cost",
                "1",
                "--verifier",
                sys.executable,
                "-m",
                "json.tool",
                "{artifact}",
            ]
        )
        == 0
    )
    store = ResearchStore(root / ".research")
    contract_id = next(iter(store.load()[0].contracts))
    assert main([*path, "start", "--contract", contract_id]) == 0
    (root / "metrics.json").write_text('{"metrics":{"score":0.9}}', encoding="utf-8")
    assert (
        main(
            [
                *path,
                "record",
                "--contract",
                contract_id,
                "--summary",
                "Measured score",
                "--outcome",
                "improved",
                "--cost",
                "0.5",
                "--metric",
                "score=0.9",
            ]
        )
        == 0
    )
    exp_id = next(iter(store.load()[0].experiments))
    assert f"epi verify --experiment {exp_id}" in capsys.readouterr().out
    assert main([*path, "verify", "--experiment", exp_id]) == 0
    evidence_id = next(iter(store.load()[0].evidence))
    assert (
        main(
            [
                *path,
                "reflect",
                "--claim",
                "C",
                "--verdict",
                "supported",
                "--evidence",
                evidence_id,
                "--rationale",
                "Matched preregistered evidence",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                *path,
                "unknown",
                "resolve",
                "--id",
                "U",
                "--evidence",
                evidence_id,
                "--rationale",
                "The tested bounded decision is settled",
            ]
        )
        == 0
    )
    assert main([*path, "doctor"]) == 0
    assert '"legacy_evidence": 0' in capsys.readouterr().out


def test_onboarding_includes_report_artifacts_without_promoting_claims(root: Path) -> None:
    file = root / "artifacts" / "experiment-results.json"
    file.parent.mkdir()
    file.write_text('{"metric":3}', encoding="utf-8")
    (root / ".env").write_text("SECRET=never_index", encoding="utf-8")
    assert main(["--root", str(root), "onboard"]) == 0
    state, _ = ResearchStore(root / ".research").load()
    assert not state.claims
    assert "artifacts/experiment-results.json" in state.onboarding[0].relevant_files
    assert ".env" not in state.onboarding[0].relevant_files


def test_versioned_event_log_preserves_frozen_source_after_log_only_commit(root: Path) -> None:
    """Research event commits must not invalidate the code/config snapshot."""
    store = setup_store(root)
    subprocess.run(
        ["git", "add", ".research/events.jsonl"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-qm", "track scientific history"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    contract = register(store, root)
    # Pre-registration writes to the tracked event file without changing source.
    assert require_clean_revision(root, contract.git_revision)
    # A log-only commit advances HEAD; the pinned source remains identical.
    subprocess.run(
        ["git", "add", ".research/events.jsonl"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-qm", "record preregistration"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    assert require_clean_revision(root, contract.git_revision)
    observe(store, root)
    assert validate(root) == 0


def test_frozen_source_rejects_committed_code_change_even_with_versioned_log(root: Path) -> None:
    store = setup_store(root)
    contract = register(store, root)
    (root / "README.md").write_text("Changed actual research code/docs", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "modify pinned source"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    with pytest.raises(ValueError, match="source differs"):
        require_clean_revision(root, contract.git_revision)


def test_counterevidence_reopens_resolved_unknown_and_restores_research_frontier(
    root: Path,
) -> None:
    store = setup_store(root)
    register(store, root)
    observe(store, root)
    assert validate(root) == 0
    reflect(store)
    store.append(
        "unknown_resolved",
        {"unknown_id": "U", "evidence_id": "EV-1", "rationale": "Initial scoped evidence"},
    )
    state, _ = store.load()
    assert state.unknowns["U"].status == "resolved"
    assert research_phase(state) == "optimization"

    store.add(
        "proposal_added",
        Proposal(
            id="P2",
            title="Counterexample on held-out evaluation",
            kind=ProposalKind.EXPLORE,
            rationale="Try to falsify the original supported mechanism",
            acceptance="Same frozen protocol and opposing metric",
            unknown_ids=["U"],
            claim_ids=["C"],
            information_gain=0.9,
            expected_improvement=0.1,
            estimated_cost=1,
            risk=0.1,
        ),
    )
    register(store, root, contract_id="PRE-2", proposal_id="P2")
    observe(
        store,
        root,
        score=0.4,
        outcome="no_gain",
        contract_id="PRE-2",
        experiment_id="EXP-2",
        evidence_id="EV-2",
    )
    assert validate(root, "EXP-2") == 0
    reflect(store, verdict="refuted", evidence_id="EV-2")

    new_state, _ = store.load()
    assert new_state.claims["C"].status is ClaimStatus.INCONCLUSIVE
    assert new_state.unknowns["U"].status == "open"
    assert new_state.unknowns["U"].resolution_evidence_ids == ["EV-1"]
    assert new_state.unknowns["U"].reopening_evidence_ids == ["EV-2"]
    assert research_phase(new_state) != "optimization"
    assert ResearchStore(store.root).load()[0] == new_state
