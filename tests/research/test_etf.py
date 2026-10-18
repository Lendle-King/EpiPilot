from datetime import UTC, datetime

import pytest

from epipilot.research.etf import ETFAdapter, available_records, parse_stress

STRESS = "\n".join(
    f"| M{branch} | {cost} | {slip} | {10 - cost * 0.1 - slip * 0.1}% | "
    f"{-10 - cost * 0.1 - slip * 0.1}% | 1 | 1x | 20% | 100% | 0 | 0 | 0 | 1 | 0 |"
    for branch in range(3)
    for cost in (5, 10, 20)
    for slip in (0, 5, 10)
)


def test_stress_checks_grid_and_monotonicity():
    result = parse_stress(STRESS)
    assert result.scenarios == 27
    assert result.monotonic
    with pytest.raises(ValueError, match="27"):
        parse_stress(STRESS.splitlines()[0])


def test_replay_reveals_only_available_records():
    records = [
        {"available_at": "2026-10-07T08:00:00+00:00", "summary": "early"},
        {"available_at": "2026-10-14T08:00:00+00:00", "summary": "late"},
    ]
    assert len(available_records(records, datetime(2026, 10, 10, tzinfo=UTC))) == 1


def test_missing_journal_is_blocked_not_zero_alpha(tmp_path):
    adapter = ETFAdapter(tmp_path)
    result = adapter.audit_journals()
    assert not result.qualified
    assert result.completed_intervals is None
    assert len(result.missing) == 6


def test_tampered_artifact_cannot_be_evidence(tmp_path):
    from uuid import uuid4

    from epipilot.research.models import Cost, ExperimentProposal

    adapter = ETFAdapter(tmp_path)
    proposal = ExperimentProposal(
        id="journal-availability",
        hypothesis_id=uuid4(),
        title="check",
        input_revision="fixture",
        estimated_cost=(Cost(unit="wall_seconds", amount=1.0),),
    )
    observation = adapter.run(proposal, tmp_path / "audit.json")
    assert adapter.verify(proposal, observation).verified
    (tmp_path / "audit.json").write_text("{}", encoding="utf8")
    assert not adapter.verify(proposal, observation).verified


def test_historical_frontier_has_no_later_pit_or_execution_information():
    from epipilot.research.etf import _replay_frontier

    early = [{"route": "V4", "revision": "old"}]
    engine = _replay_frontier(early, datetime(2026, 10, 7, tzinfo=UTC), "unknown")
    ranking = engine.ranking()
    assert not ranking.ranked
    assert all("pit" not in p.id and "stress" not in p.id for p in engine.state.research.proposals)
    assert any("unknown cost" in item.reason for item in ranking.rejected)
