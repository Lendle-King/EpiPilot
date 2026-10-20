"""The SSE archive adapter re-fetches allowlisted historical records fail-closed."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Quality, Rule
from epipilot.workspace.sse_lifecycle_archive import (
    ADAPTER_ID,
    SSEArchiveResponse,
    SSELifecycleArchiveComputer,
)

URL = "https://www.sse.com.cn/disclosure/fund/announcement/c/2014-03-06/510520_20140306_3.pdf"
BODY = b"%PDF-1.4\nsynthetic SSE archived record\n%%EOF"


def make_contract(root: Path, *, url: str = URL, body: bytes = BODY) -> MeasurementContract:
    captured_at = datetime.now(UTC).isoformat()
    record = {
        "record_id": "510520-listing",
        "url": url,
        "body_path": "captures/listing.pdf",
        "status": 200,
        "content_type": "application/pdf",
        "server_date": "Tue, 20 Oct 2026 19:00:00 GMT",
        "captured_at_utc": captured_at,
        "byte_count": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    manifest = {"schema": "sse-lifecycle-archive-capture/v1", "records": [record]}
    (root / "captures").mkdir(parents=True, exist_ok=True)
    manifest_path = root / "captures/manifest.json"
    body_path = root / record["body_path"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    body_path.write_bytes(body)
    return MeasurementContract(
        proposal_id="sse-archive-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        inputs=(
            InputFile(
                path="captures/manifest.json",
                sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            ),
            InputFile(
                path=record["body_path"], sha256=hashlib.sha256(body_path.read_bytes()).hexdigest()
            ),
        ),
        rules=(Rule(metric="reachable_http_200", minimum=1),),
    )


def response(*, body: bytes = BODY, final_url: str = URL, headers: Mapping[str, str] | None = None):
    return SSEArchiveResponse(
        status=200,
        final_url=final_url,
        headers=headers
        or {"Content-Type": "application/pdf", "Date": "Tue, 20 Oct 2026 19:00:00 GMT"},
        body=body,
    )


def test_sse_lifecycle_archive_rechecks_live_bytes_and_returns_integrity_metrics(
    tmp_path: Path,
) -> None:
    contract = make_contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str) -> SSEArchiveResponse:
        calls.append(url)
        return response()

    measured = SSELifecycleArchiveComputer(fetcher=fetch).measure(tmp_path, contract)

    metrics = {item.name: item.value for item in measured.metrics}
    assert measured.samples == 1
    assert SSELifecycleArchiveComputer(fetcher=fetch).quality is Quality.MEASUREMENT
    assert metrics["reachable_http_200"] == 1
    assert metrics["body_sha256_matches"] == 1
    assert calls == [URL]


def test_sse_lifecycle_archive_rejects_non_sse_urls_before_network_access(tmp_path: Path) -> None:
    contract = make_contract(
        tmp_path,
        url="https://attacker.invalid/disclosure/fund/announcement/c/2014-03-06/510520_20140306_3.pdf",
    )

    def fetch(_: str) -> SSEArchiveResponse:
        pytest.fail("non-SSE source must be rejected before fetch")

    with pytest.raises(ValueError, match="allowlisted SSE archive URL"):
        SSELifecycleArchiveComputer(fetcher=fetch).measure(tmp_path, contract)


def test_sse_lifecycle_archive_measures_changed_live_archive_bytes(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)

    measured = SSELifecycleArchiveComputer(
        fetcher=lambda _: response(body=BODY + b"changed")
    ).measure(tmp_path, contract)

    metrics = {item.name: item.value for item in measured.metrics}
    assert metrics["reachable_http_200"] == 1
    assert metrics["body_sha256_matches"] == 0


def test_sse_lifecycle_archive_measures_redirects_and_non_success_status(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)

    redirected = SSELifecycleArchiveComputer(
        fetcher=lambda _: response(final_url="https://other.example/file.pdf")
    ).measure(tmp_path, contract)
    assert {item.name: item.value for item in redirected.metrics}["reachable_http_200"] == 0

    missing = SSELifecycleArchiveComputer(
        fetcher=lambda _: SSEArchiveResponse(
            status=404,
            final_url=URL,
            headers={"Content-Type": "application/pdf", "Date": "Tue, 20 Oct 2026 19:00:00 GMT"},
            body=b"not found",
        )
    ).measure(tmp_path, contract)
    assert {item.name: item.value for item in missing.metrics}["reachable_http_200"] == 0
