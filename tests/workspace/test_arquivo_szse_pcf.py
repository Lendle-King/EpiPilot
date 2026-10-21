"""Verify timestamped SZSE PCF witnesses from the independent Arquivo.pt archive."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.workspace.arquivo_szse_pcf import (
    ADAPTER_ID,
    ArquivoResponse,
    ArquivoSZSEPCFComputer,
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


def _contract(root: Path) -> MeasurementContract:
    manifest = {
        "schema": "arquivo-szse-pcf-targets/v1",
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
    path = root / "targets.json"
    content = json.dumps(manifest, separators=(",", ":")).encode()
    path.write_bytes(content)
    return MeasurementContract(
        proposal_id="arquivo-szse-pcf-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        inputs=(InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),),
        rules=(Rule(metric="verified_pre_cutoff_targets", minimum=1),),
    )


def _response(url: str, body: bytes, *, status: int = 200) -> ArquivoResponse:
    return ArquivoResponse(status=status, final_url=url, headers={}, body=body)


def _cdx_line(
    *,
    timestamp: str = TARGET_TIME,
    original: str = TARGET_URL,
    digest: str = TARGET_DIGEST,
    record_length: int = 170,
) -> bytes:
    row = {
        "url": original,
        "timestamp": timestamp,
        "mime": "text/plain",
        "status": "200",
        "digest": digest,
        "length": str(record_length),
        "offset": "0",
        "filename": "arquivo-test.warc.gz",
    }
    return (json.dumps(row) + "\n").encode()


def _values(measured: object) -> dict[str, float]:
    return {metric.name: metric.value for metric in measured.metrics}  # type: ignore[attr-defined]


def test_empty_cdx_body_is_a_zero_row_observation_without_replay(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str) -> ArquivoResponse:
        calls.append(url)
        assert urlparse(url).hostname == "arquivo.pt"
        if urlparse(url).path == "/wayback/cdx":
            query = parse_qs(urlparse(url).query)
            assert query["matchType"] == ["exact"]
            assert query["output"] == ["json"]
            return _response(url, b"")
        pytest.fail("an empty CDX response must not trigger replay")

    measured = ArquivoSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)

    values = _values(measured)
    assert measured.samples == 4
    assert values["empty_cdx_responses"] == 4
    assert values["verified_pre_cutoff_targets"] == 0
    assert len(calls) == 4


def test_captures_at_or_after_0915_china_time_are_not_replayed(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    cutoff = "20190111011500"
    post_cutoff = "20190111011501"

    def fetch(url: str) -> ArquivoResponse:
        if urlparse(url).path == "/wayback/cdx":
            target = parse_qs(urlparse(url).query)["url"][0]
            body = (
                _cdx_line(timestamp=cutoff) + _cdx_line(timestamp=post_cutoff)
                if target == TARGET_URL
                else b""
            )
            return _response(url, body)
        pytest.fail("captures on/after the decision cutoff must never be replayed")

    measured = ArquivoSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["cutoff_or_later_captures_ignored"] == 2
    assert values["raw_replays_fetched"] == 0
    assert values["verified_pre_cutoff_targets"] == 0


def test_matching_pre_cutoff_raw_replay_verifies_even_when_cdx_length_is_compressed(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    replayed: list[str] = []

    def fetch(url: str) -> ArquivoResponse:
        if urlparse(url).path == "/wayback/cdx":
            target = parse_qs(urlparse(url).query)["url"][0]
            return _response(url, _cdx_line() if target == TARGET_URL else b"")
        replayed.append(url)
        return _response(url, BODY)

    measured = ArquivoSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["verified_pre_cutoff_targets"] == 1
    assert values["raw_sha1_matches_cdx"] == 1
    assert values["gbk_identity_matches"] == 1
    assert values["cdx_compressed_record_length_bytes"] == 170
    assert len(BODY) != 170
    assert replayed == [f"https://arquivo.pt/noFrame/replay/{TARGET_TIME}id_/{TARGET_URL}"]


def test_replay_body_with_wrong_target_date_is_not_a_witness(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    wrong_identity = "申购赎回清单 159915 2019-01-12\r\n".encode("gbk")
    wrong_digest = base64.b32encode(hashlib.sha1(wrong_identity).digest()).decode().rstrip("=")

    def fetch(url: str) -> ArquivoResponse:
        if urlparse(url).path == "/wayback/cdx":
            target = parse_qs(urlparse(url).query)["url"][0]
            return _response(
                url,
                _cdx_line(digest=wrong_digest) if target == TARGET_URL else b"",
            )
        return _response(url, wrong_identity)

    measured = ArquivoSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["raw_sha1_matches_cdx"] == 1
    assert values["gbk_identity_matches"] == 0
    assert values["verified_pre_cutoff_targets"] == 0


def test_replay_body_that_differs_from_cdx_digest_is_not_a_witness(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    changed = "申购赎回清单 159915 2019-01-11 改动\r\n".encode("gbk")

    def fetch(url: str) -> ArquivoResponse:
        if urlparse(url).path == "/wayback/cdx":
            target = parse_qs(urlparse(url).query)["url"][0]
            return _response(url, _cdx_line() if target == TARGET_URL else b"")
        return _response(url, changed)

    measured = ArquivoSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    values = _values(measured)

    assert values["raw_sha1_matches_cdx"] == 0
    assert values["verified_pre_cutoff_targets"] == 0


def test_independent_refetch_reproduces_the_same_witness_measurement(tmp_path: Path) -> None:
    contract = _contract(tmp_path)

    def fetch(url: str) -> ArquivoResponse:
        if urlparse(url).path == "/wayback/cdx":
            target = parse_qs(urlparse(url).query)["url"][0]
            return _response(url, _cdx_line() if target == TARGET_URL else b"")
        return _response(url, BODY)

    computer = ArquivoSZSEPCFComputer(fetcher=fetch)
    first = computer.measure(tmp_path, contract)
    second = computer.measure(tmp_path, contract)

    assert _values(first)["verified_pre_cutoff_targets"] == 1
    assert first == second


def test_unpinned_target_is_rejected_before_network_access(tmp_path: Path) -> None:
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

    def forbidden_fetch(_: str) -> ArquivoResponse:
        pytest.fail("untrusted URL must be rejected before network access")

    with pytest.raises(ValueError, match="four fixed SZSE PCF URL/cutoff pairs"):
        ArquivoSZSEPCFComputer(fetcher=forbidden_fetch).measure(tmp_path, contract)
