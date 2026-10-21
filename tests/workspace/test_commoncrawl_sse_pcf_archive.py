"""Common Crawl SSE PCF checks bind CDX rows to verified WARC bytes."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.research.measurement import Measured
from epipilot.workspace import commoncrawl_sse_pcf_archive as archive
from epipilot.workspace.commoncrawl_sse_pcf_archive import (
    ADAPTER_ID,
    ADAPTER_ID_LONG_INDEX_TIMEOUT,
    CommonCrawlResponse,
    CommonCrawlSSEPCFArchiveComputer,
    CommonCrawlSSEPCFLongIndexTimeoutComputer,
)

PCF_URL = "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=510300"
CAPTURE_UTC = "20261016010500"
PCF_TEXT = b"Fundid1=510300\r\nTradingDay=20261016\r\n"
PCF_DIGEST = base64.b32encode(hashlib.sha1(PCF_TEXT).digest()).decode("ascii").rstrip("=")
FILENAME = (
    "crawl-data/CC-MAIN-2026-43/segments/1760000000000.0/warc/"
    "CC-MAIN-20261016-20261016-00000.warc.gz"
)
OFFSET = 1000


def make_contract(root: Path, *, url: str = PCF_URL) -> MeasurementContract:
    manifest = {
        "schema": "commoncrawl-sse-pcf-targets/v1",
        "targets": [
            {
                "record_id": "sse-pcf-510300-20261016",
                "url": url,
                "fund_code": "510300",
                "trading_day": "20261016",
                "crawl_id": "CC-MAIN-2026-43",
                "crawl_window_start_utc": "2026-10-05T10:56:23Z",
                "crawl_window_end_utc": "2026-10-18T05:34:47Z",
                "cutoff_utc": "2026-10-16T01:15:00Z",
            }
        ],
    }
    path = root / "targets.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return MeasurementContract(
        proposal_id="commoncrawl-sse-pcf-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        boundary_date="2026-10-16",
        cutoff_date="2026-10-16",
        inputs=(
            InputFile(path="targets.json", sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
        ),
        rules=(Rule(metric="verified_pre_cutoff_pcf_captures", minimum=1.0),),
    )


def response(
    *,
    body: bytes,
    final_url: str,
    headers: Mapping[str, str] | None = None,
    status: int = 200,
) -> CommonCrawlResponse:
    return CommonCrawlResponse(
        status=status,
        final_url=final_url,
        headers=headers or {"Content-Type": "application/json"},
        body=body,
    )


def warc_record(payload: bytes = PCF_TEXT, *, target_url: str = PCF_URL) -> bytes:
    digest = base64.b32encode(hashlib.sha1(payload).digest()).decode("ascii").rstrip("=")
    http_record = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n" + payload
    warc_headers = (
        "WARC/1.0\r\n"
        "WARC-Type: response\r\n"
        f"WARC-Target-URI: {target_url}\r\n"
        "WARC-Date: 2026-10-16T01:05:00Z\r\n"
        f"WARC-Payload-Digest: sha1:{digest}\r\n"
        f"Content-Length: {len(http_record)}\r\n\r\n"
    ).encode("ascii")
    return gzip.compress(warc_headers + http_record + b"\r\n\r\n")


def index_body(
    *,
    timestamp: str = CAPTURE_UTC,
    url: str = PCF_URL,
    filename: str = FILENAME,
    offset: int = OFFSET,
    warc_length: int = len(warc_record()),
    digest: str = PCF_DIGEST,
) -> bytes:
    row = {
        "url": url,
        "timestamp": timestamp,
        "status": "200",
        "mime": "text/plain",
        "digest": digest,
        "filename": filename,
        "offset": str(offset),
        "length": str(warc_length),
    }
    return (json.dumps(row) + "\n").encode("utf-8")


def metrics_for(measured: Measured) -> dict[str, float]:
    return {metric.name: metric.value for metric in measured.metrics}


def test_commoncrawl_sse_pcf_verifies_exact_preopen_warc_and_identity(
    tmp_path: Path,
) -> None:
    contract = make_contract(tmp_path)
    compressed = warc_record()
    calls: list[tuple[str, Mapping[str, str]]] = []

    def fetch(url: str, headers: Mapping[str, str]) -> CommonCrawlResponse:
        calls.append((url, headers))
        parsed = urlparse(url)
        if parsed.hostname == "index.commoncrawl.org":
            query = parse_qs(parsed.query)
            assert parsed.path == "/CC-MAIN-2026-43-index"
            assert query["url"] == [PCF_URL]
            assert query["output"] == ["json"]
            return response(body=index_body(warc_length=len(compressed)), final_url=url)
        assert parsed.hostname == "data.commoncrawl.org"
        assert headers["Range"] == f"bytes={OFFSET}-{OFFSET + len(compressed) - 1}"
        return response(
            body=compressed,
            final_url=url,
            headers={"Content-Range": f"bytes {OFFSET}-{OFFSET + len(compressed) - 1}/999999"},
            status=206,
        )

    measured = CommonCrawlSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = metrics_for(measured)

    assert measured.samples == 1
    assert metrics["index_rows"] == 1
    assert metrics["warc_range_requests"] == 1
    assert metrics["warc_payload_digest_matches_cdx"] == 1
    assert metrics["warc_target_uri_matches"] == 1
    assert metrics["warc_timestamps_match_cdx"] == 1
    assert metrics["pcf_identity_matches"] == 1
    assert metrics["verified_pre_cutoff_pcf_captures"] == 1
    assert len(calls) == 2


def test_commoncrawl_sse_pcf_records_exact_no_captures_without_warc_request(
    tmp_path: Path,
) -> None:
    contract = make_contract(tmp_path)

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        assert urlparse(url).hostname == "index.commoncrawl.org"
        body = json.dumps({"message": f"No Captures found for: {PCF_URL}"}).encode("utf-8")
        return response(body=body, final_url=url, status=404)

    measured = CommonCrawlSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = metrics_for(measured)

    assert metrics["index_no_captures_responses"] == 1
    assert metrics["verified_pre_cutoff_pcf_captures"] == 0
    assert metrics["warc_range_requests"] == 0


def test_commoncrawl_sse_pcf_does_not_fetch_post_cutoff_warc_rows(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        assert urlparse(url).hostname == "index.commoncrawl.org"
        return response(
            body=index_body(timestamp="20261016011501"),
            final_url=url,
        )

    measured = CommonCrawlSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = metrics_for(measured)

    assert metrics["post_cutoff_rows_ignored"] == 1
    assert metrics["warc_range_requests"] == 0
    assert metrics["verified_pre_cutoff_pcf_captures"] == 0


def test_commoncrawl_sse_pcf_rejects_non_sse_pcfs_before_network(tmp_path: Path) -> None:
    contract = make_contract(tmp_path, url="https://attacker.invalid/file?fundCode=510300")

    def fetch(_: str, __: Mapping[str, str]) -> CommonCrawlResponse:
        pytest.fail("non-SSE targets must be rejected before any request")

    with pytest.raises(ValueError, match="exact SSE PCF download URL"):
        CommonCrawlSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)


def test_long_index_timeout_adapter_uses_longer_index_and_bounded_warc_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = make_contract(tmp_path).model_copy(update={"adapter": ADAPTER_ID_LONG_INDEX_TIMEOUT})
    compressed = warc_record()
    observed: list[tuple[str, float]] = []

    def fetch(url: str, headers: Mapping[str, str], timeout_seconds: float) -> CommonCrawlResponse:
        parsed = urlparse(url)
        if parsed.hostname == "index.commoncrawl.org":
            observed.append(("index", timeout_seconds))
            return response(body=index_body(warc_length=len(compressed)), final_url=url)
        observed.append(("warc", timeout_seconds))
        start = OFFSET
        end = start + len(compressed) - 1
        assert headers["Range"] == f"bytes={start}-{end}"
        return response(
            body=compressed,
            final_url=url,
            headers={"Content-Range": f"bytes {start}-{end}/999999"},
            status=206,
        )

    monkeypatch.setattr(archive, "_fetch_commoncrawl", fetch)
    measured = CommonCrawlSSEPCFLongIndexTimeoutComputer().measure(tmp_path, contract)

    assert metrics_for(measured)["verified_pre_cutoff_pcf_captures"] == 1
    assert observed == [("index", 16), ("warc", 8)]
