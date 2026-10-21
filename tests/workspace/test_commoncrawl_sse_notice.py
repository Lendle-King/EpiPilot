"""The Common Crawl adapter verifies an exact archived SSE notice WARC record."""

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
from epipilot.workspace.commoncrawl_sse_notice import (
    ADAPTER_ID,
    CommonCrawlResponse,
    CommonCrawlSseNoticeComputer,
)

NOTICE_URL = "https://www.sse.com.cn/market/sseindex/diclosure/c/c_20200529_5114573.shtml"
CRAWL_ID = "CC-MAIN-2020-24"
CUTOFF = "2020-06-02T01:30:00Z"
CAPTURE = "20200602010000"
FILENAME = (
    "crawl-data/CC-MAIN-2020-24/segments/1234567890.1/warc/"
    "CC-MAIN-20200601000000-20200602235959-00000.warc.gz"
)
BODY = "<html><i>2020-06-01</i><p>调整样本于2020年6月15日生效</p></html>".encode()
PAYLOAD_DIGEST = base64.b32encode(hashlib.sha1(BODY).digest()).decode("ascii").rstrip("=")


def make_contract(root: Path, *, url: str = NOTICE_URL) -> MeasurementContract:
    target = {
        "record_id": "sse-index-2020",
        "crawl_id": CRAWL_ID,
        "crawl_window_start_utc": "2020-05-24T21:03:25Z",
        "crawl_window_end_utc": "2020-06-07T10:59:29Z",
        "url": url,
        "cutoff_utc": CUTOFF,
        "page_date": "2020-06-01",
        "effective_date": "2020-06-15",
    }
    manifest = {"schema": "commoncrawl-sse-notice-targets/v1", "targets": [target]}
    path = root / "targets.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return MeasurementContract(
        proposal_id="commoncrawl-sse-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        inputs=(
            InputFile(path="targets.json", sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
        ),
        rules=(Rule(metric="verified_pre_cutoff_snapshots", minimum=1.0),),
    )


def warc_record(
    *, body: bytes = BODY, target: str = NOTICE_URL, timestamp: str = "2020-06-02T01:00:00Z"
) -> bytes:
    digest = base64.b32encode(hashlib.sha1(body).digest()).decode("ascii").rstrip("=")
    http_response = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + body
    headers = (
        "WARC/1.0\r\n"
        "WARC-Type: response\r\n"
        f"WARC-Date: {timestamp}\r\n"
        f"WARC-Target-URI: {target}\r\n"
        f"WARC-Payload-Digest: sha1:{digest}\r\n"
        f"Content-Length: {len(http_response)}\r\n"
        "Content-Type: application/http; msgtype=response\r\n\r\n"
    ).encode("ascii")
    return gzip.compress(headers + http_response + b"\r\n\r\n")


def index_row(*, timestamp: str = CAPTURE, url: str = NOTICE_URL) -> bytes:
    record = {
        "url": url,
        "timestamp": timestamp,
        "status": "200",
        "mime": "text/html",
        "digest": PAYLOAD_DIGEST,
        "offset": "100",
        "length": str(len(warc_record())),
        "filename": FILENAME,
    }
    return (json.dumps(record) + "\n").encode("utf-8")


def response(
    *,
    body: bytes,
    final_url: str,
    status: int,
    headers: Mapping[str, str] | None = None,
) -> CommonCrawlResponse:
    return CommonCrawlResponse(
        status=status,
        final_url=final_url,
        headers=headers or {},
        body=body,
    )


def test_commoncrawl_sse_notice_verifies_pre_cutoff_warc_capture(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)
    calls: list[tuple[str, Mapping[str, str]]] = []

    def fetch(url: str, headers: Mapping[str, str]) -> CommonCrawlResponse:
        calls.append((url, headers))
        parsed = urlparse(url)
        if parsed.hostname == "index.commoncrawl.org":
            assert parsed.path == f"/{CRAWL_ID}-index"
            assert parse_qs(parsed.query)["url"] == [NOTICE_URL]
            return response(body=index_row(), final_url=url, status=200)
        assert parsed.hostname == "data.commoncrawl.org"
        assert headers["Range"] == f"bytes=100-{99 + len(warc_record())}"
        return response(
            body=warc_record(),
            final_url=url,
            status=206,
            headers={"Content-Range": f"bytes 100-{99 + len(warc_record())}/100000"},
        )

    measured = CommonCrawlSseNoticeComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}

    assert measured.samples == 1
    assert metrics["index_rows"] == 1
    assert metrics["pre_cutoff_rows"] == 1
    assert metrics["warc_payload_digest_matches_cdx"] == 1
    assert metrics["warc_target_uri_matches"] == 1
    assert metrics["notice_dates_match"] == 1
    assert metrics["verified_pre_cutoff_snapshots"] == 1
    assert len(calls) == 2


def test_commoncrawl_sse_notice_does_not_fetch_post_cutoff_capture(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        if urlparse(url).hostname == "index.commoncrawl.org":
            return response(body=index_row(timestamp="20200602013001"), final_url=url, status=200)
        pytest.fail("post-cutoff capture must not be downloaded as a PIT witness")

    measured = CommonCrawlSseNoticeComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}

    assert metrics["post_cutoff_rows"] == 1
    assert metrics["verified_pre_cutoff_snapshots"] == 0


def test_commoncrawl_sse_notice_accepts_valid_empty_index_response(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        return response(body=b"", final_url=url, status=200)

    measured = CommonCrawlSseNoticeComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}

    assert metrics["index_empty_responses"] == 1
    assert metrics["index_rows"] == 0
    assert metrics["index_queries_incomplete"] == 0


def test_commoncrawl_sse_notice_rejects_warc_payload_digest_mismatch(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        if urlparse(url).hostname == "index.commoncrawl.org":
            return response(body=index_row(), final_url=url, status=200)
        changed_body = BODY.replace(b"2020-06-01", b"2020-06-02")
        return response(
            body=warc_record(body=changed_body),
            final_url=url,
            status=206,
            headers={"Content-Range": f"bytes 100-{99 + len(warc_record())}/100000"},
        )

    measured = CommonCrawlSseNoticeComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = {metric.name: metric.value for metric in measured.metrics}

    assert metrics["warc_payload_digest_matches_cdx"] == 0
    assert metrics["verified_pre_cutoff_snapshots"] == 0


def test_commoncrawl_sse_notice_rejects_non_allowlisted_original_before_network(
    tmp_path: Path,
) -> None:
    contract = make_contract(tmp_path, url="https://attacker.invalid/notice")

    def fetch(_: str, __: Mapping[str, str]) -> CommonCrawlResponse:
        pytest.fail("non-allowlisted original URL must be rejected before any request")

    with pytest.raises(ValueError, match="allowlisted SSE index notice URL"):
        CommonCrawlSseNoticeComputer(fetcher=fetch).measure(tmp_path, contract)
