"""Tests for source-grounded claim, assumption and limitation extraction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from epipilot.topic_discovery.analysis import (
    build_paper_analysis_prompt,
    load_paper_text,
    validate_paper_analysis,
)
from epipilot.topic_discovery.models import PaperReference


def _paper() -> PaperReference:
    return PaperReference(
        id="paper-analysis-1",
        title="Evidence Grounded Research",
        year=2025,
        url="https://example.org/paper",
        relevance_note="A source paper for analysis validation tests.",
    )


def _payload(text: str) -> dict[str, object]:
    return {
        "paper_id": "paper-analysis-1",
        "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "extraction_model": "test-model",
        "statements": [
            {
                "id": "claim-1",
                "kind": "claim",
                "statement": "The proposed method improves held-out accuracy.",
                "evidence_locator": "Section 4, paragraph 2",
                "supporting_quote": "The proposed method improves held-out accuracy.",
                "confidence": 0.9,
            },
            {
                "id": "assumption-1",
                "kind": "assumption",
                "statement": "The observation model is assumed to be calibrated.",
                "evidence_locator": "Section 3, paragraph 1",
                "supporting_quote": "We assume the observation model is calibrated.",
                "confidence": 0.8,
            },
        ],
        "reviewed_by_human": False,
    }


def test_analysis_requires_exact_source_quotes_and_matching_hash() -> None:
    text = (
        "Section 3. We assume the observation model is calibrated. "
        "Section 4. The proposed method improves held-out accuracy."
    )
    analysis = validate_paper_analysis(_paper(), json.dumps(_payload(text)), text)
    assert len(analysis.statements) == 2
    assert analysis.reviewed_by_human is False


def test_analysis_rejects_quote_not_found_in_source() -> None:
    text = "The paper discusses an unrelated topic."
    payload = _payload(text)
    payload["statements"] = [
        {
            "id": "claim-1",
            "kind": "claim",
            "statement": "A fabricated result.",
            "evidence_locator": "Section 4",
            "supporting_quote": "The model achieves state of the art.",
            "confidence": 0.99,
        }
    ]
    with pytest.raises(ValueError, match="supporting quote is not present"):
        validate_paper_analysis(_paper(), json.dumps(payload), text)


def test_analysis_rejects_source_hash_mismatch() -> None:
    text = "The method is evaluated on held-out tasks."
    payload = _payload(text)
    payload["source_text_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256 does not match"):
        validate_paper_analysis(_paper(), json.dumps(payload), text)


def test_prompt_is_bounded_and_requires_evidence_locators() -> None:
    text = "A" * 100
    prompt = build_paper_analysis_prompt(_paper(), text, max_characters=20)
    assert "evidence_locator" in prompt
    assert "supporting_quote" in prompt
    assert "A" * 21 not in prompt
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() in prompt


def test_load_paper_text_enforces_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "paper.txt"
    path.write_text("small text", encoding="utf-8")
    assert load_paper_text(path) == "small text"
    with pytest.raises(ValueError, match="exceeds configured size limit"):
        load_paper_text(path, max_bytes=2)


def test_analysis_rejects_duplicate_statement_ids() -> None:
    from pydantic import ValidationError

    from epipilot.topic_discovery.models import PaperAnalysis

    text = "A claim is supported by this exact sentence."
    payload = {
        "paper_id": "paper-analysis-1",
        "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "statements": [
            {
                "id": "same",
                "kind": "claim",
                "statement": "A source-grounded claim.",
                "evidence_locator": "Section 1",
                "supporting_quote": "A claim is supported by this exact sentence.",
            },
            {
                "id": "same",
                "kind": "limitation",
                "statement": "A source-grounded limitation.",
                "evidence_locator": "Section 2",
                "supporting_quote": "A claim is supported by this exact sentence.",
            },
        ],
    }
    with pytest.raises(ValidationError, match="statement IDs must be unique"):
        PaperAnalysis.model_validate(payload)
