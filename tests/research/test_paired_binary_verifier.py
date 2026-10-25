"""Domain verifier recomputes matched binary results; it cannot validate causality."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from epipilot.research.verifiers.paired_binary import (
    InvalidPairedResult,
    exact_mcnemar_p,
    main,
    verify_paired_binary,
)


def paired_result() -> dict[str, object]:
    pairs = (
        [{"id": f"win-{i}", "baseline": False, "candidate": True} for i in range(12)]
        + [{"id": f"loss-{i}", "baseline": True, "candidate": False} for i in range(2)]
        + [{"id": f"tie-{i}", "baseline": False, "candidate": False} for i in range(6)]
    )
    return {
        "protocol": {"split": "heldout-v1"},
        "metrics": {
            "delta_success": 0.5,
            "paired_p_value": 212 / 16384,
            "pair_count": 20,
        },
        "pairs": pairs,
    }


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    path = tmp_path / "paired.json"
    path.write_text(json.dumps(paired_result()), encoding="utf-8")
    return path


def test_exact_p_value_and_raw_pair_consistency(artifact: Path) -> None:
    assert exact_mcnemar_p(0, 0) == 1.0
    assert exact_mcnemar_p(12, 2) == pytest.approx(212 / 16384)
    summary = verify_paired_binary(artifact, min_pairs=20, expected_split="heldout-v1")
    assert summary["pairs"] == 20
    assert summary["wins"] == 12
    assert summary["losses"] == 2
    assert summary["delta_success"] == 0.5
    assert main([str(artifact), "--min-pairs", "20", "--expected-split", "heldout-v1"]) == 0


@pytest.mark.parametrize("invalid", ["metric", "p_value", "duplicate", "nonbinary", "split"])
def test_invalid_or_misleading_paired_result_is_rejected(artifact: Path, invalid: str) -> None:
    data = paired_result()
    metrics = data["metrics"]
    assert isinstance(metrics, dict)
    pairs = data["pairs"]
    assert isinstance(pairs, list)
    if invalid == "metric":
        metrics["delta_success"] = 0.7
    elif invalid == "p_value":
        metrics["paired_p_value"] = 0.0001
    elif invalid == "duplicate":
        pairs[-1]["id"] = pairs[0]["id"]
    elif invalid == "nonbinary":
        pairs[-1]["candidate"] = 1
    else:
        protocol = data["protocol"]
        assert isinstance(protocol, dict)
        protocol["split"] = "train"
    artifact.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InvalidPairedResult):
        verify_paired_binary(artifact, min_pairs=20, expected_split="heldout-v1")
    assert main([str(artifact), "--expected-split", "heldout-v1"]) == 2


def test_insufficient_pairs_and_duplicate_json_fields_fail(artifact: Path) -> None:
    with pytest.raises(InvalidPairedResult, match="insufficient paired sample"):
        verify_paired_binary(artifact, min_pairs=21)
    artifact.write_text('{"metrics":{},"metrics":{}}', encoding="utf-8")
    with pytest.raises(InvalidPairedResult, match="duplicate JSON"):
        verify_paired_binary(artifact)


def test_main_module_can_run_as_frozen_argv(artifact: Path) -> None:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "epipilot.research.verifiers.paired_binary",
            str(artifact),
            "--min-pairs",
            "20",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert '"pairs": 20' in result.stdout
