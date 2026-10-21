"""The SSE attachment adapter binds exact Wayback PDF bytes to a pinned cutoff."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.workspace.wayback_sse_pdf_attachment import (
    ADAPTER_ID,
    WaybackResponse,
    WaybackSsePDFAttachmentComputer,
)

NOTICE_URL = "https://www.sse.com.cn/market/sseindex/diclosure/c/c_20260529_10820183.shtml"
PDF_URL = (
    "https://www.sse.com.cn/market/sseindex/diclosure/c/10820183/files/"
    "eaebcb5e5ad343ce87ce9a55f8ba3cce.pdf"
)
CAPTURE_UTC = "20260530013000"
PDF_BODY = b"%PDF-1.4\nsynthetic index adjustment attachment\n%%EOF\n"
CDX_DIGEST = base64.b32encode(hashlib.sha1(PDF_BODY).digest()).decode("ascii").rstrip("=")


def make_contract(root: Path, *, url: str = PDF_URL) -> MeasurementContract:
    target_file = {
        "schema": "wayback-sse-pdf-attachment-targets/v1",
        "targets": [
            {
                "record_id": "sse50-adjustment-2026-05-pdf",
                "url": url,
                "notice_url": NOTICE_URL,
                "cutoff_utc": "2026-06-01T01:30:00Z",
                "page_date": "2026-05-29",
                "effective_date": "2026-06-12",
            }
        ],
    }
    path = root / "targets.json"
    path.write_text(json.dumps(target_file), encoding="utf-8")
    return MeasurementContract(
        proposal_id="wayback-sse-pdf-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        boundary_date="2026-05-29",
        cutoff_date="2026-06-01",
        inputs=(
            InputFile(path="targets.json", sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
        ),
        rules=(Rule(metric="verified_pre_cutoff_pdf_captures", minimum=1.0),),
    )


def response(
    *,
    body: bytes,
    final_url: str,
    headers: Mapping[str, str] | None = None,
    status: int = 200,
) -> WaybackResponse:
    return WaybackResponse(
        status=status,
        final_url=final_url,
        headers=headers or {"Content-Type": "application/json"},
        body=body,
    )


def cdx_body(*, timestamp: str = CAPTURE_UTC, digest: str = CDX_DIGEST) -> bytes:
    return json.dumps(
        [
            ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
            [timestamp, PDF_URL, "application/pdf", "200", digest, str(len(PDF_BODY))],
        ]
    ).encode("utf-8")


def test_wayback_pdf_adapter_verifies_exact_pre_cutoff_attachment_bytes(
    tmp_path: Path,
) -> None:
    contract = make_contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str) -> WaybackResponse:
        calls.append(url)
        parsed = urlparse(url)
        if parsed.hostname == "web.archive.org" and parsed.path == "/cdx/search/cdx":
            query = parse_qs(parsed.query)
            assert query["matchType"] == ["exact"]
            assert query["url"] == [PDF_URL]
            assert query["filter"] == ["statuscode:200", "mimetype:application/pdf"]
            return response(body=cdx_body(), final_url=url)
        assert url == f"https://web.archive.org/web/{CAPTURE_UTC}id_/{PDF_URL}"
        return response(body=PDF_BODY, final_url=url, headers={"Content-Type": "application/pdf"})

    measured = WaybackSsePDFAttachmentComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}

    assert measured.samples == 1
    assert metrics["exact_cdx_rows"] == 1
    assert metrics["verified_pre_cutoff_pdf_captures"] == 1
    assert metrics["pdf_sha1_matches_cdx"] == 1
    assert metrics["pdf_signature_valid"] == 1
    assert len(calls) == 2


def test_wayback_pdf_adapter_does_not_count_post_cutoff_or_mismatched_bytes(
    tmp_path: Path,
) -> None:
    contract = make_contract(tmp_path)
    later = "20260601013001"

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            return response(body=cdx_body(timestamp=later), final_url=url)
        pytest.fail("a post-cutoff archive row must not be replayed")

    measured = WaybackSsePDFAttachmentComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}
    assert metrics["verified_pre_cutoff_pdf_captures"] == 0
    assert metrics["post_cutoff_rows_ignored"] == 1


def test_wayback_pdf_adapter_rejects_non_sse_attachment_before_fetch(tmp_path: Path) -> None:
    contract = make_contract(tmp_path, url="https://attacker.invalid/payload.pdf")

    def fetch(_: str) -> WaybackResponse:
        pytest.fail("a non-allowlisted attachment must be rejected before fetch")

    with pytest.raises(ValueError, match="allowlisted SSE PDF attachment URL"):
        WaybackSsePDFAttachmentComputer(fetcher=fetch).measure(tmp_path, contract)


def test_wayback_pdf_adapter_rejects_replay_bytes_that_disagree_with_cdx(
    tmp_path: Path,
) -> None:
    contract = make_contract(tmp_path)
    changed = b"%PDF-1.4\nchanged bytes\n%%EOF\n"

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            return response(body=cdx_body(), final_url=url)
        return response(body=changed, final_url=url, headers={"Content-Type": "application/pdf"})

    measured = WaybackSsePDFAttachmentComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}
    assert metrics["verified_pre_cutoff_pdf_captures"] == 0
    assert metrics["pdf_sha1_matches_cdx"] == 0
