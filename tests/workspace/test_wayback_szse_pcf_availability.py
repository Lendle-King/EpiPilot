"""Verify the Wayback Availability API route stays a candidate locator only."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.workspace import wayback_szse_pcf_availability as availability_module
from epipilot.workspace.wayback_sse_notice import WaybackResponse
from epipilot.workspace.wayback_szse_pcf_availability import (
    ADAPTER_ID,
    WaybackSZSEPCFAvailabilityComputer,
)

PINNED = (
    ("159915", "2019-01-11"),
    ("159915", "2020-11-30"),
    ("159949", "2023-01-03"),
    ("159949", "2026-10-16"),
)
TARGET_URL = "https://reportdocs.static.szse.cn/files/text/etf/ETF15991520190111.txt"
TARGET_TIME = "20190111011459"
BODY = "申购赎回清单 159915 2019-01-11\r\n".encode("gbk")
TARGET_DIGEST = base64.b32encode(hashlib.sha1(BODY).digest()).decode("ascii").rstrip("=")


def _url(code: str, day: str) -> str:
    return f"https://reportdocs.static.szse.cn/files/text/etf/ETF{code}{day.replace('-', '')}.txt"


def _contract(root: Path, *, first_url: str = TARGET_URL) -> MeasurementContract:
    manifest = {
        "schema": "szse-pcf-wayback-availability-targets/v1",
        "targets": [
            {
                "record_id": f"{code}-{day}",
                "code": code,
                "date": day,
                "url": first_url if (code, day) == PINNED[0] else _url(code, day),
                "cutoff_utc": f"{day}T01:15:00Z",
            }
            for code, day in PINNED
        ],
    }
    path = root / "targets.json"
    content = json.dumps(manifest, separators=(",", ":")).encode()
    path.write_bytes(content)
    return MeasurementContract(
        proposal_id="szse-pcf-wayback-availability-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        inputs=(InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),),
        rules=(Rule(metric="verified_pre_cutoff_targets", minimum=1),),
    )


def _response(url: str, body: bytes, *, status: int = 200) -> WaybackResponse:
    return WaybackResponse(status=status, final_url=url, headers={}, body=body)


def _cdx_body(rows: list[list[str]]) -> bytes:
    return json.dumps(
        [["timestamp", "original", "mimetype", "statuscode", "digest", "length"], *rows]
    ).encode()


def _values(measured: object) -> dict[str, float]:
    return {metric.name: metric.value for metric in measured.metrics}  # type: ignore[attr-defined]


def _availability(url: str, timestamp: str | None = None) -> bytes:
    closest = (
        {
            "available": True,
            "status": "200",
            "timestamp": timestamp,
            "url": f"https://web.archive.org/web/{timestamp}/{url}",
        }
        if timestamp
        else None
    )
    snapshots = {"closest": closest} if closest else {}
    return json.dumps({"url": url, "archived_snapshots": snapshots}).encode()


def test_empty_availability_results_are_inconclusive_not_source_absence(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str) -> WaybackResponse:
        calls.append(url)
        assert urlparse(url).hostname == "archive.org"
        assert urlparse(url).path == "/wayback/available"
        query = parse_qs(urlparse(url).query)
        assert query["url"][0].startswith("https://reportdocs.static.szse.cn/")
        return _response(url, _availability(query["url"][0]))

    measured = WaybackSZSEPCFAvailabilityComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert measured.samples == 4
    assert values["availability_empty_responses"] == 4
    assert values["verified_pre_cutoff_targets"] == 0
    assert len(calls) == 4


def test_manifest_rejects_any_non_pinned_pcf_url(tmp_path: Path) -> None:
    contract = _contract(tmp_path, first_url="https://example.invalid/pcf.txt")

    def fetch(url: str) -> WaybackResponse:
        raise AssertionError(f"untrusted URL was queried: {url}")

    computer = WaybackSZSEPCFAvailabilityComputer(fetcher=fetch)

    with pytest.raises(ValueError, match="four fixed SZSE URL/cutoff pairs"):
        computer.measure(tmp_path, contract)


def test_timeout_capture_at_or_after_target_cutoff_is_not_replayed(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    cutoff_2019 = "20190111011500"
    cutoff_2023 = "20230103011500"
    after_2023 = str(int(cutoff_2023) + 1)
    calls: list[str] = []

    def fetch(url: str) -> WaybackResponse:
        calls.append(url)
        parsed = urlparse(url)
        if parsed.path == "/wayback/available":
            target = parse_qs(parsed.query)["url"][0]
            if target == TARGET_URL:
                return _response(url, _availability(target, cutoff_2019))
            if target == _url("159915", "2020-11-30"):
                raise TimeoutError("synthetic timeout")
            if target == _url("159949", "2023-01-03"):
                return _response(url, _availability(target, after_2023))
            return _response(url, _availability(target))
        raise AssertionError("late candidates and timeouts must not trigger replay requests")

    measured = WaybackSZSEPCFAvailabilityComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["cutoff_or_later_candidates"] == 2
    assert values["availability_requests_failed"] == 1
    assert values["raw_replays_fetched"] == 0
    assert len(calls) == 4


def test_candidate_requires_cdxdigest_length_and_gbk_identity(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    wrong_body = "申购赎回清单 159915 2019-01-12\r\n".encode("gbk")
    calls: list[str] = []

    def fetch(url: str) -> WaybackResponse:
        calls.append(url)
        parsed = urlparse(url)
        if parsed.path == "/wayback/available":
            target = parse_qs(parsed.query)["url"][0]
            return _response(
                url, _availability(target, TARGET_TIME if target == TARGET_URL else None)
            )
        if parsed.path == "/cdx/search/cdx":
            query = parse_qs(parsed.query)
            assert query["url"] == [TARGET_URL]
            assert query["from"] == [TARGET_TIME] and query["to"] == [TARGET_TIME]
            return _response(
                url,
                _cdx_body(
                    [[TARGET_TIME, TARGET_URL, "text/plain", "200", TARGET_DIGEST, str(len(BODY))]]
                ),
            )
        return _response(url, wrong_body)

    measured = WaybackSZSEPCFAvailabilityComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["raw_replays_fetched"] == 1
    assert values["raw_sha1_length_matches_cdx"] == 0
    assert values["gbk_identity_matches"] == 0
    assert values["verified_pre_cutoff_targets"] == 0


def test_matching_bytes_with_wrong_pcf_date_are_not_a_witness(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    wrong_identity = "申购赎回清单 159915 2019-01-12\r\n".encode("gbk")
    digest = base64.b32encode(hashlib.sha1(wrong_identity).digest()).decode("ascii").rstrip("=")

    def fetch(url: str) -> WaybackResponse:
        parsed = urlparse(url)
        if parsed.path == "/wayback/available":
            target = parse_qs(parsed.query)["url"][0]
            return _response(
                url, _availability(target, TARGET_TIME if target == TARGET_URL else None)
            )
        if parsed.path == "/cdx/search/cdx":
            return _response(
                url,
                _cdx_body(
                    [
                        [
                            TARGET_TIME,
                            TARGET_URL,
                            "text/plain",
                            "200",
                            digest,
                            str(len(wrong_identity)),
                        ]
                    ]
                ),
            )
        return _response(url, wrong_identity)

    measured = WaybackSZSEPCFAvailabilityComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["raw_sha1_length_matches_cdx"] == 1
    assert values["gbk_identity_matches"] == 0
    assert values["verified_pre_cutoff_targets"] == 0


def test_independent_refetch_recomputes_same_verified_capture(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str) -> WaybackResponse:
        calls.append(url)
        parsed = urlparse(url)
        if parsed.path == "/wayback/available":
            target = parse_qs(parsed.query)["url"][0]
            return _response(
                url, _availability(target, TARGET_TIME if target == TARGET_URL else None)
            )
        if parsed.path == "/cdx/search/cdx":
            return _response(
                url,
                _cdx_body(
                    [[TARGET_TIME, TARGET_URL, "text/plain", "200", TARGET_DIGEST, str(len(BODY))]]
                ),
            )
        return _response(url, BODY)

    computer = WaybackSZSEPCFAvailabilityComputer(fetcher=fetch)
    measured = computer.measure(tmp_path, contract)
    independently_measured = computer.measure(tmp_path, contract)

    assert measured == independently_measured
    assert _values(measured)["verified_pre_cutoff_targets"] == 1
    adapter_hash = hashlib.sha256(Path(availability_module.__file__).read_bytes()).hexdigest()
    assert f"adapter_source_sha256={adapter_hash}" in measured.details
    assert len(calls) == 12
