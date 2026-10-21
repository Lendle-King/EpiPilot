"""The SSE PCF archive adapter verifies exact pre-open file captures."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.research.measurement import Measured
from epipilot.workspace.wayback_sse_pcf_archive import (
    ADAPTER_ID,
    WaybackResponse,
    WaybackSSEPCFArchiveComputer,
)

PCF_URL = "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode=510300"
CAPTURE_UTC = "20261016010500"
PCF_TEXT = b"Fundid1=510300\r\nTradingDay=20261016\r\n"
PCF_DIGEST = base64.b32encode(hashlib.sha1(PCF_TEXT).digest()).decode("ascii").rstrip("=")


def make_contract(root: Path, *, url: str = PCF_URL) -> MeasurementContract:
    targets = {
        "schema": "wayback-sse-pcf-archive-targets/v1",
        "targets": [
            {
                "record_id": "sse-pcf-510300-20261016",
                "url": url,
                "fund_code": "510300",
                "trading_day": "20261016",
                "cutoff_utc": "2026-10-16T01:15:00Z",
            }
        ],
    }
    path = root / "targets.json"
    path.write_text(json.dumps(targets), encoding="utf-8")
    return MeasurementContract(
        proposal_id="wayback-sse-pcf-archive-test",
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
) -> WaybackResponse:
    return WaybackResponse(
        status=status,
        final_url=final_url,
        headers=headers or {"Content-Type": "application/json"},
        body=body,
    )


def cdx_body(
    *,
    timestamp: str = CAPTURE_UTC,
    original: str = PCF_URL,
    digest: str = PCF_DIGEST,
    length: int = len(PCF_TEXT),
    mimetype: str = "text/plain",
) -> bytes:
    return json.dumps(
        [
            ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
            [timestamp, original, mimetype, "200", digest, str(length)],
        ]
    ).encode("utf-8")


def metrics_for(measured: Measured) -> dict[str, float]:
    return {metric.name: metric.value for metric in measured.metrics}


def test_wayback_sse_pcf_archive_verifies_exact_preopen_text_capture(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str) -> WaybackResponse:
        calls.append(url)
        if urlparse(url).path == "/cdx/search/cdx":
            query = parse_qs(urlparse(url).query)
            assert query["url"] == [PCF_URL]
            assert query["matchType"] == ["exact"]
            assert query["to"] == ["20261016011500"]
            return response(body=cdx_body(), final_url=url)
        assert url == f"https://web.archive.org/web/{CAPTURE_UTC}id_/{PCF_URL}"
        return response(
            body=PCF_TEXT,
            final_url=url,
            headers={"Content-Type": "text/plain; charset=gbk"},
        )

    measured = WaybackSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = metrics_for(measured)

    assert measured.samples == 1
    assert metrics["exact_cdx_rows"] == 1
    assert metrics["pcf_replay_attempts"] == 1
    assert metrics["pcf_sha1_matches_cdx"] == 1
    assert metrics["pcf_identity_matches"] == 1
    assert metrics["verified_pre_cutoff_pcf_captures"] == 1
    assert len(calls) == 2


def test_wayback_sse_pcf_archive_records_empty_cdx_without_replay(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)

    def fetch(url: str) -> WaybackResponse:
        assert urlparse(url).path == "/cdx/search/cdx"
        return response(body=b"[]", final_url=url)

    measured = WaybackSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = metrics_for(measured)

    assert metrics["empty_cdx_responses"] == 1
    assert metrics["verified_pre_cutoff_pcf_captures"] == 0
    assert metrics["pcf_replay_attempts"] == 0


def test_wayback_sse_pcf_archive_never_replays_post_cutoff_rows(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)

    def fetch(url: str) -> WaybackResponse:
        assert urlparse(url).path == "/cdx/search/cdx"
        return response(body=cdx_body(timestamp="20261016011501"), final_url=url)

    measured = WaybackSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = metrics_for(measured)

    assert metrics["post_cutoff_rows_ignored"] == 1
    assert metrics["verified_pre_cutoff_pcf_captures"] == 0


def test_wayback_sse_pcf_archive_does_not_accept_wrong_internal_trading_day(
    tmp_path: Path,
) -> None:
    contract = make_contract(tmp_path)
    wrong_body = b"Fundid1=510300\r\nTradingDay=20261015\r\n"
    wrong_digest = base64.b32encode(hashlib.sha1(wrong_body).digest()).decode("ascii").rstrip("=")

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            return response(
                body=cdx_body(digest=wrong_digest, length=len(wrong_body)),
                final_url=url,
            )
        return response(
            body=wrong_body,
            final_url=url,
            headers={"Content-Type": "text/plain"},
        )

    measured = WaybackSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = metrics_for(measured)

    assert metrics["pcf_sha1_matches_cdx"] == 1
    assert metrics["pcf_identity_matches"] == 0
    assert metrics["verified_pre_cutoff_pcf_captures"] == 0


def test_wayback_sse_pcf_archive_parses_xml_identity(tmp_path: Path) -> None:
    contract = make_contract(tmp_path)
    xml_body = (
        b"<ETFPCF><FundInstrumentID>510300</FundInstrumentID>"
        b"<TradingDay>20261016</TradingDay></ETFPCF>"
    )
    xml_digest = base64.b32encode(hashlib.sha1(xml_body).digest()).decode("ascii").rstrip("=")

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            return response(
                body=cdx_body(digest=xml_digest, length=len(xml_body), mimetype="application/xml"),
                final_url=url,
            )
        return response(
            body=xml_body,
            final_url=url,
            headers={"Content-Type": "application/xml"},
        )

    measured = WaybackSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)
    assert metrics_for(measured)["verified_pre_cutoff_pcf_captures"] == 1


def test_wayback_sse_pcf_archive_rejects_unapproved_host_before_fetch(tmp_path: Path) -> None:
    contract = make_contract(tmp_path, url="https://attacker.invalid/download?fundCode=510300")

    def fetch(_: str) -> WaybackResponse:
        pytest.fail("non-SSE URLs must be rejected before any request")

    with pytest.raises(ValueError, match="exact SSE PCF download URL"):
        WaybackSSEPCFArchiveComputer(fetcher=fetch).measure(tmp_path, contract)
