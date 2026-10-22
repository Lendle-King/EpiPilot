import hashlib
import json
from datetime import UTC, datetime

import pytest
from test_agent_cycle import discovered

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.research.measurement import MeasurementAdapter
from epipilot.research.models import Cost, ExecutionStatus, Observation


def test_observation_old_payload_remains_compatible() -> None:
    legacy = Observation(
        proposal_id="old",
        execution=ExecutionStatus.SUCCEEDED,
        metrics=(),
        samples=0,
        actual_cost=(Cost(unit="seconds", amount=1.0),),
        artifact="old.json",
        summary="legacy",
        recorded_at=datetime.now(UTC),
    ).model_dump()
    observation = Observation.model_validate(legacy)
    assert observation.artifact_sha256 is None
    assert observation.source_revision is None
    assert observation.data_cutoff is None


def test_prospective_contract_requires_cutoff_and_data_source() -> None:
    kwargs = {
        "proposal_id": "prospective",
        "purpose": Purpose.VERIFY,
        "adapter": "series",
        "git_revision": "a" * 40,
        "inputs": (InputFile(path="data.csv", sha256="0" * 64),),
        "rules": (Rule(metric="mean", minimum=0),),
        "prospective": True,
    }
    with pytest.raises(ValueError, match="prospective"):
        MeasurementContract(**kwargs)
    with pytest.raises(ValueError, match="prospective"):
        MeasurementContract(**kwargs, cutoff_date="2024-01-01")


def test_prospective_metadata_changes_fingerprint() -> None:
    kwargs = {
        "proposal_id": "prospective",
        "purpose": Purpose.VERIFY,
        "adapter": "series",
        "git_revision": "a" * 40,
        "inputs": (InputFile(path="data.csv", sha256="0" * 64),),
        "rules": (Rule(metric="mean", minimum=0),),
        "cutoff_date": "2024-01-01",
        "data_source": "frozen-feed",
        "prospective": True,
    }
    first = MeasurementContract(**kwargs)
    second = first.model_copy(update={"data_source": "other-feed"})
    assert first.fingerprint() != second.fingerprint()


def test_artifact_replacement_and_path_reuse_fail_hash_verification(tmp_path) -> None:
    engine, root, _ = discovered(tmp_path)
    adapter = MeasurementAdapter(engine, root)
    proposal = engine.reserve_next()
    output = tmp_path / "result.json"
    observation = adapter.run(proposal, output)
    assert observation.artifact_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert adapter.verify(proposal, observation).verified

    output.write_text(json.dumps({"replaced": True}), encoding="utf8")
    assert not adapter.verify(proposal, observation).verified

    output.write_text(json.dumps({"reused": True}), encoding="utf8")
    reused = observation.model_copy(
        update={"artifact_sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
    )
    assert not adapter.verify(proposal, reused).verified


def test_run_contract_failure_is_not_retried_in_error_handler(tmp_path) -> None:
    engine, root, _ = discovered(tmp_path)
    adapter = MeasurementAdapter(engine, root)
    proposal = engine.reserve_next()
    original = adapter.contract
    calls = 0

    def fail_once(p):
        nonlocal calls
        calls += 1
        raise ValueError("broken contract")

    adapter.contract = fail_once
    with pytest.raises(ValueError, match="broken contract"):
        adapter.run(proposal, tmp_path / "result.json")
    assert calls == 1
    adapter.contract = original


def test_ordinary_measurement_never_qualifies_for_promotion(tmp_path) -> None:
    engine, root, _ = discovered(tmp_path)
    adapter = MeasurementAdapter(engine, root)
    proposal = engine.reserve_next()
    observation = adapter.run(proposal, tmp_path / "result.json")
    assessment = adapter.verify(proposal, observation)
    assert assessment.verified
    assert assessment.qualified_for_promotion is False
    assert observation.execution is ExecutionStatus.SUCCEEDED
    assert observation.actual_cost[0].unit == "wall_seconds"
    assert observation.artifact
