"""Contract tests for read-only scientific hypothesis comparison and candidate identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from epipilot.research.candidate_identity import candidate_fingerprint, normalize_candidate
from epipilot.research.cli import main
from epipilot.research.discovery import (
    DiagnosticProbe,
    HypothesisMatrix,
    discovery_frontier,
    load_hypothesis_matrix,
    rank_diagnostic_probes,
)
from epipilot.research.models import (
    Claim,
    ExplorationConfig,
    ResearchGoal,
    ResearchState,
    Unknown,
)
from epipilot.research.store import ResearchStore


def _state() -> ResearchState:
    return ResearchState(
        goal=ResearchGoal(statement="Understand the performance ceiling", budget=10),
        unknowns={
            "U": Unknown(id="U", question="Why does the benchmark saturate?", importance=0.9)
        },
        claims={
            "A": Claim(
                id="A", scope="fixed development split", statement="No useful algorithm gap"
            ),
            "B": Claim(
                id="B", scope="fixed development split", statement="Gap appears on hard cases"
            ),
            "C": Claim(id="C", scope="fixed development split", statement="Noise masks the gap"),
        },
    )


def _matrix() -> HypothesisMatrix:
    return HypothesisMatrix(
        unknown_id="U",
        scope="fixed development split",
        hypothesis_ids=["A", "B", "C"],
        probes=[
            DiagnosticProbe(
                id="cheap",
                title="Difficulty stratification",
                protocol="Run paired hard and easy cases under frozen conditions",
                control="Same model, seed and sample budget in each stratum",
                measurement="Paired success rates for difficulty strata",
                decision_rule="If the gap appears only on hard cases, investigate B",
                estimated_cost=1,
                risk=0.1,
                predictions={"A": "no_gap", "B": "hard_gap", "C": "no_gap"},
            ),
            DiagnosticProbe(
                id="strong",
                title="Matched factorial diagnosis",
                protocol="Cross difficulty and noise in a balanced experiment",
                control="Freeze paired cases and randomization before testing",
                measurement="Measure separate difficulty and noise interactions",
                decision_rule="Select explanation only after independent verification",
                estimated_cost=2,
                risk=0.15,
                predictions={"A": "no_gap", "B": "hard_gap", "C": "noise_gap"},
            ),
            DiagnosticProbe(
                id="empty",
                title="Uninformative repeat",
                protocol="Repeat only the aggregate test from the old pilot",
                control="Keep data and model unchanged in repeated trials",
                measurement="Aggregate score without stratification",
                decision_rule="Cannot distinguish these three hypotheses",
                estimated_cost=0.1,
                risk=0.01,
                predictions={"A": "same", "B": "same", "C": "same"},
            ),
        ],
    )


def test_discriminating_design_ranks_mechanisms_not_cheap_noise() -> None:
    state = _state()
    ranked = rank_diagnostic_probes(state, _matrix())
    assert [probe["id"] for probe in ranked["ranked_probes"]] == [
        "strong",
        "cheap",
        "empty",
    ]
    assert [p["distinguishable_pairs"] for p in ranked["ranked_probes"]] == [3, 2, 0]
    assert ranked["ranked_probes"][-1]["status"] == "non_discriminating"
    assert "NOT measured information gain" in ranked["qualification"]
    assert len(state.proposals) == 0 and len(state.evidence) == 0


def test_budget_filter_never_marks_unknowns_as_resolved() -> None:
    state = _state()
    state.exploration = ExplorationConfig(
        budget_share=0.1, reason="Authorized limited diagnostic experiment"
    )
    ranked = rank_diagnostic_probes(state, _matrix())
    assert ranked["budget_source"] == "exploration_remaining"
    assert ranked["available_budget"] == pytest.approx(1)
    assert ranked["ranked_probes"][0]["id"] == "cheap"
    assert ranked["ranked_probes"][1]["id"] == "empty"
    assert ranked["ranked_probes"][2]["id"] == "strong"
    assert ranked["ranked_probes"][2]["status"] == "over_budget"
    assert state.unknowns["U"].status == "open"


def test_unknown_claim_scope_and_prediction_validation() -> None:
    state = _state()
    draft = _matrix()
    draft.hypothesis_ids = ["A", "missing", "C"]
    with pytest.raises(ValueError, match="unknown Claim"):
        rank_diagnostic_probes(state, draft)
    state.claims["B"].scope = "other environment"
    with pytest.raises(ValueError, match="scope disagrees"):
        rank_diagnostic_probes(state, _matrix())
    with pytest.raises(ValidationError, match="one prediction per hypothesis"):
        HypothesisMatrix(
            unknown_id="U",
            scope="fixed development split",
            hypothesis_ids=["A", "B"],
            probes=[_matrix().probes[0]],
        )


def test_candidate_fingerprint_numeric_equivalence_and_safety() -> None:
    left = {"model": "logistic", "C": 1, "settings": {"depth": 4.0, "enabled": True}}
    right = {"settings": {"enabled": True, "depth": 4}, "C": 1.0, "model": "logistic"}
    assert normalize_candidate(left) == normalize_candidate(right)
    assert candidate_fingerprint(left) == candidate_fingerprint(right)
    assert candidate_fingerprint({"flag": True}) != candidate_fingerprint({"flag": 1})
    with pytest.raises(ValueError, match="finite"):
        candidate_fingerprint({"C": float("nan")})


def test_frontier_preserves_unverified_status_and_scopes() -> None:
    state = _state()
    state.claims["A"].supporting_evidence_ids = ["EV-absent"]
    result = discovery_frontier(state)
    assert result["status"] == "read_only_projection"
    assert result["scoped_claims"][0]["unqualified_evidence_ids"] == ["EV-absent"]
    assert result["scoped_claims"][0]["qualified_evidence_ids"] == []


def test_cli_diagnostic_preview_does_not_write_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _state()
    store = ResearchStore(tmp_path / ".research")
    store.add("goal_set", state.goal)
    store.add("unknown_added", state.unknowns["U"])
    for claim in state.claims.values():
        store.add("claim_added", claim)
    before = store.load()[1]
    draft = tmp_path / "hypothesis.json"
    draft.write_text(_matrix().model_dump_json(), encoding="utf-8")
    assert main(["--root", str(tmp_path), "explore", "frontier"]) == 0
    assert "scoped_claims" in capsys.readouterr().out
    assert (
        main(["--root", str(tmp_path), "explore", "discriminate", "--file", "hypothesis.json"]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["ranked_probes"][0]["id"] == "strong"
    assert store.load()[1] == before


def test_cli_rejects_outside_file_and_duplicate_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path.parent / "outside-discovery.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="inside the research root"):
        load_hypothesis_matrix(tmp_path, outside)
    data = tmp_path / "bad.json"
    data.write_text('{"unknown_id":"U","unknown_id":"U"}', encoding="utf-8")
    assert main(["--root", str(tmp_path), "explore", "discriminate", "--file", "bad.json"]) == 2
    assert "duplicate diagnostic JSON property" in capsys.readouterr().err
