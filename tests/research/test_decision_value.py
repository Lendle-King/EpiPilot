"""Ported Scout V1 value-of-information behavior on the canonical v2 event store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_research_plugin import observe, register, setup_store, validate


@pytest.fixture
def root(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "research"
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

from epipilot.research.cli import main
from epipilot.research.decision_models import (
    DecisionBranch,
    DecisionEstimate,
    DecisionForecast,
    DecisionOption,
    DecisionOutcome,
    DecisionScore,
    OutcomeKind,
)
from epipilot.research.decision_value import (
    calibrate_probabilities,
    rank_decision,
    score_estimate,
)
from epipilot.research.models import Proposal, ProposalKind
from epipilot.research.store import ResearchStore


def make_estimate(proposal_id: str = "P", option: str | None = "CHILD") -> DecisionEstimate:
    return DecisionEstimate(
        proposal_id=proposal_id,
        predictor="agent-fixed-v1",
        utility_version="goal-v1",
        group="matched-ablation",
        rationale="Outcome could change whether to deploy or stop",
        options=(
            DecisionOption(key="stop", prior_utility=0.4),
            DecisionOption(key="next", proposal_id=option, prior_utility=0.5),
        ),
        branches=tuple(
            DecisionBranch(outcome=outcome, probability=0.25, utilities=values)
            for outcome, values in zip(
                OutcomeKind,
                ((0.4, 0.8), (0.4, 0.2), (0.4, 0.5), (0.4, 0.5)),
                strict=True,
            )
        ),
    )


def make_child(store: ResearchStore, cost: float = 0.5) -> None:
    store.add(
        "proposal_added",
        Proposal(
            id="CHILD",
            title="Conditional follow-on",
            kind=ProposalKind.OPTIMIZE,
            rationale="Only after earlier discriminative evidence",
            acceptance="Frozen matched improvement",
            unknown_ids=["U"],
            claim_ids=["C"],
            depends_on=["P"],
            information_gain=0.1,
            expected_improvement=0.8,
            estimated_cost=cost,
            risk=0.1,
        ),
    )


def test_value_of_information_from_same_joint_table() -> None:
    est = make_estimate()
    assert score_estimate(est) == pytest.approx(0.05)
    assert score_estimate(
        est,
        enabled_before=(True, False),
        enabled_after=((True, True), (True, True), (True, True), (True, False)),
    ) == pytest.approx(0.125)
    # Unaffordable or unavailable follow-ups cannot earn hypothetical value.
    assert score_estimate(
        est, enabled_before=(True, False), enabled_after=((True, False),) * 4
    ) == pytest.approx(0)
    # A posterior cannot exploit an option absent from every feasible mask.
    with pytest.raises(ValueError, match="non-executing"):
        score_estimate(est, enabled_before=(False, False))


def test_cold_start_and_group_isolated_calibration() -> None:
    est = make_estimate()
    score = DecisionScore(
        proposal_id="P",
        value_of_information=0.1,
        normalized_cost=0.1,
        risk_penalty=0.1,
        net_value=-0.1,
        probabilities=(0.25,) * 4,
        calibration_samples=0,
        enabled_before=(True, False),
        enabled_after=((True, True),) * 4,
    )
    samples = tuple(
        (
            DecisionForecast(
                contract_id=f"PRE-{i}",
                git_revision="a" * 40,
                recorded_at=datetime.now(UTC),
                estimate=est,
                score=score,
            ),
            DecisionOutcome(
                contract_id=f"PRE-{i}",
                evidence_id=f"EV-{i}",
                outcome=OutcomeKind.NO_GAIN,
                actual_cost=1,
                brier_score=0.75,
                qualified=True,
            ),
        )
        for i in range(20)
    )
    assert calibrate_probabilities(est, samples[:19]) == (0.25,) * 4
    adjusted = calibrate_probabilities(est, samples)
    assert adjusted[1] > 0.25
    assert sum(adjusted) == pytest.approx(1.0)


def test_frozen_forecast_requires_approved_proposal_and_real_evidence(root: Path) -> None:
    store = setup_store(root)
    make_child(store)
    est = make_estimate()
    store.append("decision_estimate_registered", est.model_dump(mode="json"))
    before, _ = store.load()
    score = rank_decision(before, est)
    assert score.value_of_information > 0
    assert score.enabled_before == (True, False)
    assert score.enabled_after[0] == (True, True)
    register(store, root)
    frozen = store.load()[0].decision_forecasts["PRE-1"]
    assert frozen.estimate == est
    assert frozen.score.value_of_information == pytest.approx(score.value_of_information)
    observe(store, root)
    assert store.load()[0].decision_outcomes == {}
    assert validate(root) == 0
    after, count = store.load()
    assert count == 10
    actual = after.decision_outcomes["PRE-1"]
    assert actual.qualified and actual.outcome is OutcomeKind.IMPROVED
    assert actual.actual_cost == pytest.approx(0.8)
    assert actual.observed_value_of_information is None
    assert after == ResearchStore(store.root).load()[0]


def test_unverified_or_inconclusive_experiment_does_not_train_forecaster(root: Path) -> None:
    store = setup_store(root)
    make_child(store)
    est = make_estimate()
    store.append("decision_estimate_registered", est.model_dump(mode="json"))
    register(store, root)
    observe(store, root)
    metrics = root / "results/metrics.json"
    metrics.write_text('{"metrics":{"score":0.1}}', encoding="utf-8")
    assert validate(root) == 2
    state, _ = store.load()
    assert state.decision_outcomes == {}
    assert len(state.decision_forecasts) == 1


def test_invalid_and_repeated_decision_tables_fail_closed(root: Path) -> None:
    store = setup_store(root)
    make_child(store)
    est = make_estimate()
    bad = est.model_dump(mode="json")
    bad["branches"][0]["probability"] = 0.5
    with pytest.raises(ValidationError, match="sum to one"):
        DecisionEstimate.model_validate(bad)
    invalid = est.model_copy(
        update={
            "options": (
                est.options[0],
                est.options[1].model_copy(update={"proposal_id": "UNKNOWN"}),
            )
        }
    )
    with pytest.raises(ValueError, match="unknown"):
        store.append("decision_estimate_registered", invalid.model_dump(mode="json"))
    store.append("decision_estimate_registered", est.model_dump(mode="json"))
    with pytest.raises(ValueError, match="already registered"):
        store.append("decision_estimate_registered", est.model_dump(mode="json"))


def test_cli_register_rank_and_status(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = setup_store(root)
    make_child(store)
    file = root / "decision.json"
    file.write_text(make_estimate().model_dump_json(), encoding="utf-8")
    args = ["--root", str(root), "decision"]
    assert main([*args, "register", "--file", "decision.json"]) == 0
    assert main([*args, "rank"]) == 0
    rank_output = capsys.readouterr().out
    assert '"value_of_information"' in rank_output
    assert main([*args, "status"]) == 0
    printed = capsys.readouterr().out
    assert '"estimates": [' in printed
    assert "P" in printed
