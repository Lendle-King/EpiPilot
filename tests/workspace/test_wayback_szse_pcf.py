"""Verify pinned SZSE PCF targets using exact Wayback CDX and replay bytes."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.workspace.wayback_szse_pcf import (
    ADAPTER_ID,
    WaybackResponse,
    WaybackSZSEPCFComputer,
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


def _contract(root: Path, *, bounded_replay: bool = False) -> MeasurementContract:
    manifest = {
        "schema": (
            "szse-pcf-wayback-targets/v2" if bounded_replay else "szse-pcf-wayback-targets/v1"
        ),
        "targets": [
            {
                "record_id": f"{code}-{day}",
                "code": code,
                "date": day,
                "url": _url(code, day),
                "cutoff_utc": f"{day}T01:15:00Z",
            }
            for code, day in PINNED
        ],
    }
    if bounded_replay:
        manifest["replay_candidate_limit"] = 1
    path = root / "targets.json"
    content = json.dumps(manifest, separators=(",", ":")).encode()
    path.write_bytes(content)
    return MeasurementContract(
        proposal_id="szse-pcf-wayback-test",
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


def test_empty_cdx_results_do_not_claim_a_preopen_witness(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str) -> WaybackResponse:
        calls.append(url)
        parsed = urlparse(url)
        assert parsed.hostname == "web.archive.org"
        assert parsed.path == "/cdx/search/cdx"
        query = parse_qs(parsed.query)
        assert query["matchType"] == ["exact"]
        return _response(url, b"[]\n")

    measured = WaybackSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)

    values = _values(measured)
    assert measured.samples == 4
    assert values["verified_pre_cutoff_targets"] == 0
    assert values["empty_cdx_responses"] == 4
    assert len(calls) == 4


def test_capture_at_or_after_0915_china_time_is_not_replayed(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    cutoff = "20190111011500"
    post_cutoff = "20190111011501"

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            target = parse_qs(urlparse(url).query)["url"][0]
            rows = (
                [
                    [cutoff, TARGET_URL, "text/plain", "200", TARGET_DIGEST, str(len(BODY))],
                    [
                        post_cutoff,
                        TARGET_URL,
                        "text/plain",
                        "200",
                        TARGET_DIGEST,
                        str(len(BODY)),
                    ],
                ]
                if target == TARGET_URL
                else []
            )
            return _response(url, _cdx_body(rows))
        raise AssertionError("at-cutoff and post-cutoff captures cannot be replayed")

    measured = WaybackSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["verified_pre_cutoff_targets"] == 0
    assert values["cutoff_or_later_captures_ignored"] == 2
    assert values["raw_replays_fetched"] == 0


def test_replay_must_match_cdx_sha1_length_and_gbk_identity(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    changed = "申购赎回清单 159915 2019-01-12\r\n".encode("gbk")

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            return _response(
                url,
                _cdx_body(
                    [[TARGET_TIME, TARGET_URL, "text/plain", "200", TARGET_DIGEST, str(len(BODY))]]
                ),
            )
        return _response(url, changed)

    measured = WaybackSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["verified_pre_cutoff_targets"] == 0
    assert values["raw_replays_fetched"] == 1
    assert values["raw_sha1_length_matches_cdx"] == 0
    assert values["gbk_identity_matches"] == 0


@pytest.mark.parametrize(
    ("wrong_identity_body", "wrong_identity_digest"),
    [
        (
            "申购赎回清单 159949 2019-01-11\r\n".encode("gbk"),
            "ZWUM5EJHXG26PRVXPXQ5LZJFH63NXN5V",
        ),
        (
            "申购赎回清单 159915 2019-01-12\r\n".encode("gbk"),
            "46OQN7YRIUKE7OPOR7N64PQTLL3OM3JW",
        ),
    ],
    ids=("wrong-code", "wrong-date"),
)
def test_matching_replay_bytes_with_wrong_gbk_identity_are_not_a_witness(
    tmp_path: Path, wrong_identity_body: bytes, wrong_identity_digest: str
) -> None:
    contract = _contract(tmp_path)

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            target = parse_qs(urlparse(url).query)["url"][0]
            rows = (
                [
                    [
                        TARGET_TIME,
                        TARGET_URL,
                        "text/plain",
                        "200",
                        wrong_identity_digest,
                        str(len(wrong_identity_body)),
                    ]
                ]
                if target == TARGET_URL
                else []
            )
            return _response(url, _cdx_body(rows))
        return _response(url, wrong_identity_body)

    measured = WaybackSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["raw_replays_fetched"] == 1
    assert values["raw_sha1_length_matches_cdx"] == 1
    assert values["gbk_identity_matches"] == 0
    assert values["verified_pre_cutoff_targets"] == 0


def test_v2_manifest_replays_only_the_latest_candidate_per_target(tmp_path: Path) -> None:
    contract = _contract(tmp_path, bounded_replay=True)
    latest = "20190111011459"
    earlier = "20190111011458"
    changed = "申购赎回清单 159915 2019-01-12\r\n".encode("gbk")
    replayed: list[str] = []

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            target = parse_qs(urlparse(url).query)["url"][0]
            rows = (
                [
                    [latest, TARGET_URL, "text/plain", "200", TARGET_DIGEST, str(len(BODY))],
                    [earlier, TARGET_URL, "text/plain", "200", TARGET_DIGEST, str(len(BODY))],
                ]
                if target == TARGET_URL
                else []
            )
            return _response(url, _cdx_body(rows))
        replayed.append(url)
        return _response(url, changed if latest in url else BODY)

    measured = WaybackSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)

    values = _values(measured)
    assert values["raw_replays_fetched"] == 1
    assert values["verified_pre_cutoff_targets"] == 0
    assert len(replayed) == 1
    assert latest in replayed[0]


def test_independent_refetch_reproduces_the_same_verified_measurement(tmp_path: Path) -> None:
    contract = _contract(tmp_path)

    def fetch(url: str) -> WaybackResponse:
        if urlparse(url).path == "/cdx/search/cdx":
            target = parse_qs(urlparse(url).query)["url"][0]
            rows = (
                [[TARGET_TIME, TARGET_URL, "text/plain", "200", TARGET_DIGEST, str(len(BODY))]]
                if target == TARGET_URL
                else []
            )
            return _response(url, _cdx_body(rows))
        return _response(url, BODY)

    first = WaybackSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    second = WaybackSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)

    values = _values(first)
    assert values["verified_pre_cutoff_targets"] == 1
    assert values["raw_sha1_length_matches_cdx"] == 1
    assert values["gbk_identity_matches"] == 1
    assert first == second


def test_only_the_four_pinned_exact_urls_are_accepted(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    manifest_path = tmp_path / "targets.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["targets"][0]["url"] = "https://example.invalid/ETF15991520190111.txt"
    content = json.dumps(manifest, separators=(",", ":")).encode()
    manifest_path.write_bytes(content)
    contract = contract.model_copy(
        update={
            "inputs": (InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),)
        }
    )

    def forbidden_fetch(_: str) -> WaybackResponse:
        raise AssertionError("untrusted URL must be rejected before network access")

    try:
        WaybackSZSEPCFComputer(fetcher=forbidden_fetch).measure(tmp_path, contract)
    except ValueError as error:
        assert "fixed SZSE PCF URL" in str(error)
    else:
        raise AssertionError("non-pinned URL was accepted")
