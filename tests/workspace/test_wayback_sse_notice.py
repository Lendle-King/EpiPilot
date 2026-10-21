"""The Wayback adapter uses only exact SSE notice URLs and verifies replay bytes."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.workspace.wayback_sse_notice import (
    ADAPTER_ID,
    WaybackResponse,
    WaybackSseNoticeComputer,
)

NOTICE_URL = "https://www.sse.com.cn/market/sseindex/diclosure/c/c_20191129_4956862.shtml"
CAPTURE_UTC = "20191203013000"
BODY = "<html><i>2019-12-02</i><p>调整样本于 2019年12月16日生效</p></html>".encode()
CDX_DIGEST = base64.b32encode(hashlib.sha1(BODY).digest()).decode("ascii").rstrip("=")


def make_contract(root: Path, *, url: str = NOTICE_URL) -> MeasurementContract:
    targets = {
        "schema": "wayback-sse-notice-targets/v1",
        "targets": [
            {
                "record_id": "sse-index-2019",
                "url": url,
                "cutoff_utc": "2019-12-03T01:30:00Z",
                "page_date": "2019-12-02",
                "effective_date": "2019-12-16",
            }
        ],
    }
    path = root / "targets.json"
    path.write_text(json.dumps(targets), encoding="utf-8")
    return MeasurementContract(
        proposal_id="wayback-sse-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        inputs=(
            InputFile(path="targets.json", sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
        ),
        rules=(Rule(metric="verified_pre_cutoff_snapshots", minimum=1.0),),
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
            [timestamp, NOTICE_URL, "text/html", "200", digest, str(len(BODY))],
        ]
    ).encode("utf-8")


def test_wayback_sse_notice_measures_verified_exact_pre_cutoff_capture(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str) -> WaybackResponse:
        calls.append(url)
        parsed = urlparse(url)
        if parsed.hostname == "web.archive.org" and parsed.path == "/cdx/search/cdx":
            query = parse_qs(parsed.query)
            assert query["matchType"] == ["exact"]
            assert query["url"] == [NOTICE_URL]
            return response(body=cdx_body(), final_url=url)
        assert url == f"https://web.archive.org/web/{CAPTURE_UTC}id_/{NOTICE_URL}"
        return response(
            body=BODY,
            final_url=url,
            headers={"Content-Type": "text/plain"},
        )

    measured = WaybackSseNoticeComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}

    assert measured.samples == 1
    assert metrics["exact_cdx_rows"] == 1
    assert metrics["verified_pre_cutoff_snapshots"] == 1
    assert metrics["snapshot_sha1_matches_cdx"] == 1
    assert metrics["snapshot_page_dates_match"] == 1
    assert len(calls) == 2


def test_wayback_sse_notice_does_not_count_capture_after_cutoff(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)
    later = "20191203013001"

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            return response(body=cdx_body(timestamp=later), final_url=url)
        pytest.fail("a post-cutoff CDX row must not be fetched as a witness")

    measured = WaybackSseNoticeComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}

    assert metrics["verified_pre_cutoff_snapshots"] == 0
    assert metrics["post_cutoff_rows_ignored"] == 1


def test_wayback_sse_notice_rejects_non_sse_url_before_network_access(tmp_path: Path) -> None:
    contract = make_contract(tmp_path, url="https://attacker.invalid/not-an-sse-notice")

    def fetch(_: str) -> WaybackResponse:
        pytest.fail("a non-allowlisted original URL must be rejected before fetch")

    with pytest.raises(ValueError, match="allowlisted SSE index notice URL"):
        WaybackSseNoticeComputer(fetcher=fetch).measure(tmp_path, contract)


def test_wayback_sse_notice_rejects_replay_body_that_disagrees_with_cdx_digest(
    tmp_path: Path,
) -> None:
    contract = make_contract(tmp_path)
    changed = b"different archived bytes"

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            return response(body=cdx_body(), final_url=url)
        return response(body=changed, final_url=url, headers={"Content-Type": "text/plain"})

    measured = WaybackSseNoticeComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}

    assert metrics["verified_pre_cutoff_snapshots"] == 0
    assert metrics["snapshot_sha1_matches_cdx"] == 0
    assert metrics["snapshot_page_dates_match"] == 0
