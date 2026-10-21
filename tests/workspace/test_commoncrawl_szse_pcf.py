"""Common Crawl witnesses must bind fixed SZSE PCF URLs to raw WARC bytes."""

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
from epipilot.workspace.commoncrawl_sse_notice import CommonCrawlResponse
from epipilot.workspace.commoncrawl_szse_pcf import (
    ADAPTER_ID,
    CommonCrawlSZSEPCFComputer,
)

PINNED = (
    ("159915", "2019-01-11", "CC-MAIN-2018-51", "2018-12-09T18:55:47Z", "2018-12-19T17:31:24Z"),
    ("159915", "2020-11-30", "CC-MAIN-2020-50", "2020-11-23T15:38:26Z", "2020-12-06T03:20:41Z"),
    ("159949", "2023-01-03", "CC-MAIN-2022-49", "2022-11-26T08:07:25Z", "2022-12-10T10:42:42Z"),
    ("159949", "2026-10-16", "CC-MAIN-2026-43", "2026-10-05T10:56:23Z", "2026-10-18T05:34:47Z"),
)
INDEX_HOST = "index.commoncrawl.org"
DATA_HOST = "data.commoncrawl.org"
VALID_BODY = "申购赎回清单 ETF159915 2019-01-11\r\n".encode("gbk")
VALID_DIGEST = base64.b32encode(hashlib.sha1(VALID_BODY).digest()).decode("ascii").rstrip("=")
VALID_TIMESTAMP = "20190111011459"
VALID_FILENAME = (
    "crawl-data/CC-MAIN-2018-51/segments/1234567890.1/warc/"
    "CC-MAIN-20181209000000-20181219173124-00000.warc.gz"
)


def _url(code: str, day: str) -> str:
    return f"https://reportdocs.static.szse.cn/files/text/etf/ETF{code}{day.replace('-', '')}.txt"


def _contract(root: Path) -> MeasurementContract:
    targets = [
        {
            "record_id": f"{code}-{day}",
            "code": code,
            "date": day,
            "url": _url(code, day),
            "crawl_id": crawl_id,
            "crawl_window_start_utc": start,
            "crawl_window_end_utc": end,
            "cutoff_utc": f"{day}T01:15:00Z",
        }
        for code, day, crawl_id, start, end in PINNED
    ]
    content = json.dumps(
        {"schema": "szse-pcf-commoncrawl-targets/v1", "targets": targets},
        separators=(",", ":"),
    ).encode()
    path = root / "targets.json"
    path.write_bytes(content)
    return MeasurementContract(
        proposal_id="szse-commoncrawl-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        inputs=(InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),),
        rules=(Rule(metric="verified_pre_cutoff_targets", minimum=1),),
    )


def _response(
    url: str,
    body: bytes,
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> CommonCrawlResponse:
    return CommonCrawlResponse(status=status, final_url=url, headers=headers or {}, body=body)


def _warc_record(
    body: bytes = VALID_BODY,
    *,
    target_url: str | None = None,
    timestamp: str = "2019-01-11T01:14:59Z",
) -> bytes:
    url = target_url or _url("159915", "2019-01-11")
    digest = base64.b32encode(hashlib.sha1(body).digest()).decode("ascii").rstrip("=")
    http_response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=gbk\r\n\r\n" + body
    headers = (
        "WARC/1.0\r\n"
        "WARC-Type: response\r\n"
        f"WARC-Date: {timestamp}\r\n"
        f"WARC-Target-URI: {url}\r\n"
        f"WARC-Payload-Digest: sha1:{digest}\r\n"
        f"Content-Length: {len(http_response)}\r\n"
        "Content-Type: application/http; msgtype=response\r\n\r\n"
    ).encode("ascii")
    return gzip.compress(headers + http_response + b"\r\n\r\n")


def _index_row(
    *,
    url: str | None = None,
    timestamp: str = VALID_TIMESTAMP,
    digest: str | None = None,
    filename: str = VALID_FILENAME,
    warc_length: int | None = None,
    body: bytes = VALID_BODY,
) -> bytes:
    target = url or _url("159915", "2019-01-11")
    record = _warc_record(body, target_url=target)
    actual_digest = base64.b32encode(hashlib.sha1(body).digest()).decode("ascii").rstrip("=")
    row = {
        "url": target,
        "timestamp": timestamp,
        "status": "200",
        "mime": "text/plain",
        "digest": digest or actual_digest,
        "offset": "100",
        "length": str(warc_length if warc_length is not None else len(record)),
        "filename": filename,
    }
    return (json.dumps(row) + "\n").encode()


def _metrics(measured: object) -> dict[str, float]:
    return {metric.name: metric.value for metric in measured.metrics}  # type: ignore[attr-defined]


def test_commoncrawl_szse_pcf_accepts_matching_pre_cutoff_warc_bytes(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    calls: list[tuple[str, Mapping[str, str]]] = []

    def fetch(url: str, headers: Mapping[str, str]) -> CommonCrawlResponse:
        calls.append((url, headers))
        parsed = urlparse(url)
        if parsed.hostname == INDEX_HOST:
            query = parse_qs(parsed.query)
            if query["url"] == [_url("159915", "2019-01-11")]:
                assert parsed.path == "/CC-MAIN-2018-51-index"
                return _response(url, _index_row(), headers={})
            return _response(url, b"[]\n")
        assert parsed.hostname == DATA_HOST
        assert headers["Range"].startswith("bytes=100-")
        record = _warc_record()
        return _response(
            url,
            record,
            status=206,
            headers={"Content-Range": f"bytes 100-{99 + len(record)}/100000"},
        )

    measured = CommonCrawlSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = _metrics(measured)

    assert measured.samples == 4
    assert metrics["index_rows"] == 1
    assert metrics["pre_cutoff_rows"] == 1
    assert metrics["warc_payload_digest_matches_cdx"] == 1
    assert metrics["warc_target_uri_matches"] == 1
    assert metrics["gbk_identity_matches"] == 1
    assert metrics["verified_pre_cutoff_targets"] == 1
    assert len(calls) == 5


def test_commoncrawl_empty_indexes_are_recorded_without_claiming_absence(tmp_path: Path) -> None:
    contract = _contract(tmp_path)

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        return _response(url, b"[]\n")

    measured = CommonCrawlSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = _metrics(measured)

    assert metrics["index_empty_responses"] == 4
    assert metrics["index_rows"] == 0
    assert metrics["verified_pre_cutoff_targets"] == 0
    assert metrics["index_requests_failed"] == 0


def test_commoncrawl_exact_no_capture_response_is_not_an_error_or_proof_of_absence(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        target_url = parse_qs(urlparse(url).query)["url"][0]
        body = json.dumps({"message": f"No Captures found for: {target_url}"}).encode()
        return _response(url, body, status=404)

    measured = CommonCrawlSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = _metrics(measured)

    assert metrics["index_no_captures_responses"] == 4
    assert metrics["index_requests_failed"] == 0
    assert metrics["verified_pre_cutoff_targets"] == 0


def test_commoncrawl_szse_pcf_never_replays_at_or_after_cutoff(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    cutoff = "20190111011500"

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        if urlparse(url).hostname == INDEX_HOST:
            target_url = parse_qs(urlparse(url).query)["url"][0]
            if target_url == _url("159915", "2019-01-11"):
                return _response(url, _index_row(timestamp=cutoff))
            return _response(url, b"[]\n")
        pytest.fail("capture at the decision cutoff must never be fetched")

    measured = CommonCrawlSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = _metrics(measured)

    assert metrics["post_cutoff_rows"] == 1
    assert metrics["verified_pre_cutoff_targets"] == 0


def test_commoncrawl_szse_pcf_rejects_wrong_warc_range_length(tmp_path: Path) -> None:
    contract = _contract(tmp_path)

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        if urlparse(url).hostname == INDEX_HOST:
            target_url = parse_qs(urlparse(url).query)["url"][0]
            if target_url == _url("159915", "2019-01-11"):
                return _response(url, _index_row())
            return _response(url, b"[]\n")
        full_record = _warc_record()
        shortened = full_record[:-1]
        return _response(
            url,
            shortened,
            status=206,
            headers={"Content-Range": f"bytes 100-{99 + len(full_record)}/100000"},
        )

    measured = CommonCrawlSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = _metrics(measured)

    assert metrics["warc_requests_failed"] == 1
    assert metrics["verified_pre_cutoff_targets"] == 0


def test_commoncrawl_szse_pcf_rejects_changed_payload_or_identity(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    changed_body = "申购赎回清单 ETF159915 2019-01-12\r\n".encode("gbk")

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        if urlparse(url).hostname == INDEX_HOST:
            target_url = parse_qs(urlparse(url).query)["url"][0]
            if target_url == _url("159915", "2019-01-11"):
                return _response(url, _index_row())
            return _response(url, b"[]\n")
        record = _warc_record(changed_body)
        return _response(
            url,
            record,
            status=206,
            headers={"Content-Range": f"bytes 100-{99 + len(record)}/100000"},
        )

    measured = CommonCrawlSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = _metrics(measured)

    assert metrics["warc_payload_digest_matches_cdx"] == 0
    assert metrics["gbk_identity_matches"] == 0
    assert metrics["verified_pre_cutoff_targets"] == 0


def test_commoncrawl_szse_pcf_requires_gbk_code_and_date_identity(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    wrong_identity = "申购赎回清单 ETF159949 2020-11-30\r\n".encode("gbk")

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        if urlparse(url).hostname == INDEX_HOST:
            target_url = parse_qs(urlparse(url).query)["url"][0]
            if target_url == _url("159915", "2019-01-11"):
                return _response(url, _index_row(body=wrong_identity))
            return _response(url, b"[]\n")
        record = _warc_record(wrong_identity)
        return _response(
            url,
            record,
            status=206,
            headers={"Content-Range": f"bytes 100-{99 + len(record)}/100000"},
        )

    measured = CommonCrawlSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)
    metrics = _metrics(measured)

    assert metrics["warc_payload_digest_matches_cdx"] == 1
    assert metrics["gbk_identity_matches"] == 0
    assert metrics["verified_pre_cutoff_targets"] == 0


def test_commoncrawl_szse_pcf_records_timeout_as_unavailable(tmp_path: Path) -> None:
    contract = _contract(tmp_path)

    def timeout(_: str, __: Mapping[str, str]) -> CommonCrawlResponse:
        raise TimeoutError

    measured = CommonCrawlSZSEPCFComputer(fetcher=timeout).measure(tmp_path, contract)
    metrics = _metrics(measured)

    assert metrics["index_requests_failed"] == 4
    assert metrics["index_empty_responses"] == 0
    assert metrics["verified_pre_cutoff_targets"] == 0


def test_commoncrawl_szse_pcf_rejects_non_pinned_target_before_network(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    manifest_path = tmp_path / "targets.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["targets"][0]["url"] = "https://attacker.invalid/pcf.txt"
    content = json.dumps(manifest, separators=(",", ":")).encode()
    manifest_path.write_bytes(content)
    contract = contract.model_copy(
        update={
            "inputs": (InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),)
        }
    )

    def forbidden_fetch(_: str, __: Mapping[str, str]) -> CommonCrawlResponse:
        pytest.fail("non-pinned URL must be rejected before network access")

    with pytest.raises(ValueError, match="four pinned SZSE PCF URLs"):
        CommonCrawlSZSEPCFComputer(fetcher=forbidden_fetch).measure(tmp_path, contract)


def test_commoncrawl_probe_accepts_one_fixed_target_only(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    manifest_path = tmp_path / "targets.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["schema"] = "szse-pcf-commoncrawl-probe/v1"
    manifest["targets"] = [manifest["targets"][-1]]
    content = json.dumps(manifest, separators=(",", ":")).encode()
    manifest_path.write_bytes(content)
    contract = contract.model_copy(
        update={
            "inputs": (InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),)
        }
    )
    calls: list[str] = []

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        calls.append(url)
        return _response(url, b"[]\n")

    measured = CommonCrawlSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)

    assert measured.samples == 1
    assert len(calls) == 1
    assert "ETF15994920261016.txt" in calls[0]


def test_full_schema_does_not_silently_accept_a_target_subset(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    manifest_path = tmp_path / "targets.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["targets"] = [manifest["targets"][-1]]
    content = json.dumps(manifest, separators=(",", ":")).encode()
    manifest_path.write_bytes(content)
    contract = contract.model_copy(
        update={
            "inputs": (InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),)
        }
    )

    def forbidden_fetch(_: str, __: Mapping[str, str]) -> CommonCrawlResponse:
        pytest.fail("invalid probe schema must be rejected before network access")

    with pytest.raises(ValueError, match="full manifests need four targets"):
        CommonCrawlSZSEPCFComputer(fetcher=forbidden_fetch).measure(tmp_path, contract)


def test_commoncrawl_replays_only_the_latest_pre_cutoff_candidate_per_target(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    earlier = "20190111011458"
    latest = VALID_TIMESTAMP
    replayed: list[str] = []

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        if urlparse(url).hostname == INDEX_HOST:
            target_url = parse_qs(urlparse(url).query)["url"][0]
            if target_url == _url("159915", "2019-01-11"):
                rows = _index_row(timestamp=latest) + _index_row(timestamp=earlier)
                return _response(url, rows)
            return _response(url, b"[]\n")
        replayed.append(url)
        return _response(
            url,
            _warc_record(),
            status=206,
            headers={"Content-Range": "bytes 100-500/100000"},
        )

    CommonCrawlSZSEPCFComputer(fetcher=fetch).measure(tmp_path, contract)

    assert len(replayed) == 1
    assert "CC-MAIN-2018-51" in replayed[0]


def test_commoncrawl_measurement_can_be_independently_recomputed(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    requests: list[str] = []

    def fetch(url: str, _: Mapping[str, str]) -> CommonCrawlResponse:
        requests.append(url)
        return _response(url, b"[]\n")

    computer = CommonCrawlSZSEPCFComputer(fetcher=fetch)
    first = computer.measure(tmp_path, contract)
    second = computer.measure(tmp_path, contract)

    assert first == second
    assert len(requests) == 8
