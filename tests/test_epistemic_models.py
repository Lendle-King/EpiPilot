from __future__ import annotations

import pytest

from epipilot.core.models import Provenance, new_evidence_id
from epipilot.epistemics.models import (
    Fact,
    Hypothesis,
    HypothesisStatus,
    new_fact_id,
    new_hypothesis_id,
)


def test_active_hypothesis_must_be_falsifiable() -> None:
    with pytest.raises(ValueError, match="predictions and falsification"):
        Hypothesis(
            id=new_hypothesis_id(),
            statement="Rollout is the primary bottleneck",
            status=HypothesisStatus.ACTIVE,
        )


def test_supported_hypothesis_requires_supporting_evidence() -> None:
    with pytest.raises(ValueError, match="supporting evidence"):
        Hypothesis(
            id=new_hypothesis_id(),
            statement="Batching improves throughput",
            status=HypothesisStatus.SUPPORTED,
            predictions=("throughput increases",),
            falsification_conditions=("throughput does not improve",),
        )


def test_same_evidence_cannot_support_and_contradict_hypothesis() -> None:
    evidence_id = new_evidence_id()

    with pytest.raises(ValueError, match="both support and contradict"):
        Hypothesis(
            id=new_hypothesis_id(),
            statement="Batching improves throughput",
            supporting_evidence=(evidence_id,),
            contradicting_evidence=(evidence_id,),
        )


def test_canonical_fact_requires_evidence() -> None:
    with pytest.raises(ValueError, match="supporting evidence"):
        Fact(
            id=new_fact_id(),
            statement="Baseline throughput is 37 req/s",
            provenance=Provenance(source="benchmark", scope="project/training"),
            supporting_evidence=(),
        )
