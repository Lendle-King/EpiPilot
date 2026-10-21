"""Verify fixed SZSE PCF witnesses against Common Crawl index and WARC bytes."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.message import Message
from pathlib import Path
from typing import IO
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric
from epipilot.workspace.commoncrawl_sse_notice import (
    CommonCrawlResponse,
    _is_exact_no_captures,
    _parse_warc,
)

ADAPTER_ID = "commoncrawl_szse_pcf_v1"
_SCHEMA = "szse-pcf-commoncrawl-targets/v1"
_PROBE_SCHEMA = "szse-pcf-commoncrawl-probe/v1"
_RESPONSE_PROBE_SCHEMA = "szse-pcf-commoncrawl-response-probe/v1"
_INDEX_HOST = "index.commoncrawl.org"
_DATA_HOST = "data.commoncrawl.org"
_REQUEST_TIMEOUT_SECONDS = 8
_MAX_TARGETS = 4
_MAX_INDEX_BYTES = 1024 * 1024
_MAX_WARC_BYTES = 2 * 1024 * 1024
_MAX_CDX_ROWS = 1000
_DIGEST = re.compile(r"^[A-Z2-7]{32}$")
_TIMESTAMP = re.compile(r"^\d{14}$")
_FILENAME = re.compile(
    r"^crawl-data/(?P<crawl>CC-MAIN-\d{4}-\d{2})/segments/[0-9.]+/warc/"
    r"CC-MAIN-[0-9-]+-\d{5}\.warc\.gz$"
)
_CONTENT_RANGE = re.compile(r"^bytes (?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+)$")
_PINNED_TARGETS = (
    (
        "159915",
        "2019-01-11",
        "https://reportdocs.static.szse.cn/files/text/etf/ETF15991520190111.txt",
        "CC-MAIN-2018-51",
        "2018-12-09T18:55:47Z",
        "2018-12-19T17:31:24Z",
    ),
    (
        "159915",
        "2020-11-30",
        "https://reportdocs.static.szse.cn/files/text/etf/ETF15991520201130.txt",
        "CC-MAIN-2020-50",
        "2020-11-23T15:38:26Z",
        "2020-12-06T03:20:41Z",
    ),
    (
        "159949",
        "2023-01-03",
        "https://reportdocs.static.szse.cn/files/text/etf/ETF15994920230103.txt",
        "CC-MAIN-2022-49",
        "2022-11-26T08:07:25Z",
        "2022-12-10T10:42:42Z",
    ),
    (
        "159949",
        "2026-10-16",
        "https://reportdocs.static.szse.cn/files/text/etf/ETF15994920261016.txt",
        "CC-MAIN-2026-43",
        "2026-10-05T10:56:23Z",
        "2026-10-18T05:34:47Z",
    ),
)


class _TargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    url: str = Field(min_length=1)
    crawl_id: str = Field(pattern=r"^CC-MAIN-\d{4}-\d{2}$")
    crawl_window_start_utc: str = Field(min_length=1)
    crawl_window_end_utc: str = Field(min_length=1)
    cutoff_utc: str = Field(min_length=1)


class _TargetFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str = Field(
        alias="schema",
        pattern=(
            f"^({re.escape(_SCHEMA)}|{re.escape(_PROBE_SCHEMA)}|"
            f"{re.escape(_RESPONSE_PROBE_SCHEMA)})$"
        ),
    )
    targets: tuple[_TargetInput, ...] = Field(min_length=1, max_length=_MAX_TARGETS)


@dataclass(frozen=True)
class _Target:
    record_id: str
    code: str
    trading_date: date
    url: str
    crawl_id: str
    cutoff_utc: datetime


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return


def _fetch_commoncrawl(url: str, request_headers: Mapping[str, str]) -> CommonCrawlResponse:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {_INDEX_HOST, _DATA_HOST}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
    ):
        raise ValueError("Common Crawl request is outside the fixed HTTPS hosts")
    maximum = _MAX_INDEX_BYTES if parsed.hostname == _INDEX_HOST else _MAX_WARC_BYTES
    request = Request(
        url,
        headers={"User-Agent": "EpiPilot SZSE PCF witness verifier/1.0", **request_headers},
        method="GET",
    )
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
            final_url = response.geturl()
            headers = {
                str(key).lower(): str(value).strip() for key, value in response.headers.items()
            }
            body = response.read(maximum + 1)
    except HTTPError as error:
        status = error.code
        final_url = error.geturl()
        headers = {str(key).lower(): str(value).strip() for key, value in error.headers.items()}
        body = error.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError("Common Crawl response exceeds the registered byte limit")
    return CommonCrawlResponse(status=status, final_url=final_url, headers=headers, body=body)


def _read_targets(path: Path) -> tuple[tuple[_Target, ...], str]:
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Common Crawl SZSE target manifest is not valid UTF-8 JSON") from error
    manifest = _TargetFile.model_validate(raw)
    expected_count = (
        1 if manifest.schema_name in {_PROBE_SCHEMA, _RESPONSE_PROBE_SCHEMA} else _MAX_TARGETS
    )
    if len(manifest.targets) != expected_count:
        raise ValueError("full manifests need four targets and bounded probes need one")
    pinned = {
        (code, day): (url, crawl_id, start, end)
        for code, day, url, crawl_id, start, end in _PINNED_TARGETS
    }
    targets: list[_Target] = []
    seen: set[tuple[str, str]] = set()
    for item in manifest.targets:
        identity = (item.code, item.date)
        expected = pinned.get(identity)
        expected_record_id = f"{item.code}-{item.date}"
        expected_cutoff = f"{item.date}T01:15:00Z"
        if (
            expected is None
            or item.record_id != expected_record_id
            or (item.url, item.crawl_id, item.crawl_window_start_utc, item.crawl_window_end_utc)
            != expected
            or item.cutoff_utc != expected_cutoff
            or identity in seen
        ):
            raise ValueError(
                "manifest must use the four pinned SZSE PCF URLs and Common Crawl windows"
            )
        try:
            trading_date = date.fromisoformat(item.date)
            start = datetime.fromisoformat(item.crawl_window_start_utc.replace("Z", "+00:00"))
            end = datetime.fromisoformat(item.crawl_window_end_utc.replace("Z", "+00:00"))
            cutoff = datetime.fromisoformat(item.cutoff_utc.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("pinned Common Crawl dates must be valid ISO-8601 values") from error
        if any(value.tzinfo is None for value in (start, end, cutoff)):
            raise ValueError("pinned Common Crawl dates must include a timezone")
        if start.astimezone(UTC) > end.astimezone(UTC):
            raise ValueError("pinned Common Crawl collection window is reversed")
        seen.add(identity)
        targets.append(
            _Target(
                record_id=expected_record_id,
                code=item.code,
                trading_date=trading_date,
                url=item.url,
                crawl_id=item.crawl_id,
                cutoff_utc=cutoff.astimezone(UTC),
            )
        )
    if manifest.schema_name == _SCHEMA and seen != set(pinned):
        raise ValueError("manifest must contain all four pinned SZSE PCF URLs")
    return tuple(targets), manifest.schema_name


def _index_url(target: _Target) -> str:
    query = urlencode((("url", target.url), ("output", "json")))
    return f"https://{_INDEX_HOST}/{target.crawl_id}-index?{query}"


def _parse_index(body: bytes) -> tuple[tuple[dict[str, object], ...], bool]:
    stripped = body.strip()
    if not stripped or stripped == b"[]":
        return (), False
    rows: list[dict[str, object]] = []
    for line in stripped.splitlines():
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Common Crawl CDX response contains invalid JSON") from error
        if not isinstance(row, dict):
            raise ValueError("Common Crawl CDX row is not an object")
        rows.append(row)
        if len(rows) > _MAX_CDX_ROWS:
            raise ValueError("Common Crawl CDX response exceeds the registered row limit")
    return tuple(rows), len(rows) == _MAX_CDX_ROWS


def _metric(name: str, value: int | float) -> Metric:
    return Metric(name=name, value=float(value))


def _identity_matches(body: bytes, target: _Target) -> bool:
    try:
        text = body.decode("gbk")
    except UnicodeDecodeError:
        return False
    date_iso = target.trading_date.isoformat()
    date_compact = target.trading_date.strftime("%Y%m%d")
    return target.code in text and (date_iso in text or date_compact in text)


class CommonCrawlSZSEPCFComputer(Computer):
    """Verify at most one latest pre-cutoff raw WARC range per fixed SZSE PCF URL."""

    quality = Quality.MEASUREMENT

    def __init__(
        self,
        fetcher: Callable[[str, Mapping[str, str]], CommonCrawlResponse] | None = None,
    ) -> None:
        self._fetcher = fetcher or _fetch_commoncrawl

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID or len(contract.inputs) != 1:
            raise ValueError("Common Crawl SZSE requires one fixed target manifest")
        targets, manifest_schema = _read_targets(safe_input(root, contract.inputs[0].path))
        counts = {
            "index_http_200_responses": 0,
            "index_empty_responses": 0,
            "index_no_captures_responses": 0,
            "index_response_bytes": 0,
            "index_rows": 0,
            "pre_cutoff_rows": 0,
            "post_cutoff_rows": 0,
            "warc_range_requests": 0,
            "warc_requests_failed": 0,
            "warc_payload_digest_matches_cdx": 0,
            "warc_target_uri_matches": 0,
            "warc_timestamps_match_cdx": 0,
            "gbk_identity_matches": 0,
            "verified_pre_cutoff_targets": 0,
            "index_requests_failed": 0,
            "index_queries_incomplete": 0,
        }
        details: list[str] = []
        response_probe = manifest_schema == _RESPONSE_PROBE_SCHEMA
        details.append(
            f"manifest_schema={manifest_schema},registered_targets={len(targets)},"
            f"request_timeout_seconds={_REQUEST_TIMEOUT_SECONDS},"
            f"response_only={int(response_probe)}"
        )
        for target in targets:
            index_url = _index_url(target)
            try:
                response = self._fetcher(index_url, {})
            except (OSError, TimeoutError, ValueError) as error:
                counts["index_requests_failed"] += 1
                details.append(f"{target.record_id}:index=unavailable,error={type(error).__name__}")
                continue
            if response.final_url != index_url:
                counts["index_requests_failed"] += 1
                details.append(
                    f"{target.record_id}:index=unavailable,status={response.status},redirected=1"
                )
                continue
            if _is_exact_no_captures(response, target.url):
                counts["index_no_captures_responses"] += 1
                if response_probe:
                    semantic_message = f"No Captures found for: {target.url}"
                    semantic_sha256 = hashlib.sha256(semantic_message.encode("utf-8")).hexdigest()
                    details.append(
                        f"{target.record_id}:index_status=404,no_captures=1,"
                        f"semantic_message_sha256={semantic_sha256}"
                    )
                else:
                    response_sha256 = hashlib.sha256(response.body).hexdigest()
                    details.append(
                        f"{target.record_id}:index_status=404,no_captures=1,"
                        f"bytes={len(response.body)},sha256={response_sha256}"
                    )
                continue
            if response.status != 200:
                counts["index_requests_failed"] += 1
                details.append(
                    f"{target.record_id}:index=unavailable,status={response.status},redirected=0"
                )
                continue
            counts["index_http_200_responses"] += 1
            counts["index_response_bytes"] += len(response.body)
            index_sha256 = hashlib.sha256(response.body).hexdigest()
            try:
                rows, row_limit_reached = _parse_index(response.body)
            except ValueError:
                counts["index_queries_incomplete"] += 1
                details.append(
                    f"{target.record_id}:index=invalid_or_incomplete,"
                    f"bytes={len(response.body)},sha256={index_sha256}"
                )
                continue
            counts["index_empty_responses"] += int(not rows)
            exact_rows = tuple(row for row in rows if row.get("url") == target.url)
            counts["index_rows"] += len(exact_rows)
            if row_limit_reached or any(row.get("url") != target.url for row in rows):
                counts["index_queries_incomplete"] += 1
                details.append(
                    f"{target.record_id}:index=incomplete,bytes={len(response.body)},"
                    f"sha256={index_sha256},rows={len(rows)},exact_rows={len(exact_rows)}"
                )
                continue
            eligible: list[dict[str, object]] = []
            for row in exact_rows:
                if str(row.get("status")) != "200":
                    continue
                timestamp = str(row.get("timestamp", ""))
                if _TIMESTAMP.fullmatch(timestamp) is None:
                    counts["index_queries_incomplete"] += 1
                    continue
                try:
                    capture_time = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                except ValueError:
                    counts["index_queries_incomplete"] += 1
                    continue
                if capture_time >= target.cutoff_utc:
                    counts["post_cutoff_rows"] += 1
                    continue
                counts["pre_cutoff_rows"] += 1
                eligible.append(row)
            details.append(
                f"{target.record_id}:index_status=200,bytes={len(response.body)},"
                f"sha256={index_sha256},exact_rows={len(exact_rows)},"
                f"pre_cutoff={len(eligible)}"
            )
            if response_probe or not eligible:
                continue
            row = max(eligible, key=lambda item: str(item.get("timestamp", "")))
            timestamp = str(row.get("timestamp", ""))
            digest = str(row.get("digest", "")).upper()
            filename = str(row.get("filename", ""))
            match = _FILENAME.fullmatch(filename)
            try:
                offset = int(str(row.get("offset", "")))
                length = int(str(row.get("length", "")))
            except ValueError:
                counts["index_queries_incomplete"] += 1
                details.append(f"{target.record_id}:capture={timestamp},cdx=invalid_range_metadata")
                continue
            if (
                match is None
                or match.group("crawl") != target.crawl_id
                or _DIGEST.fullmatch(digest) is None
                or offset < 0
                or length <= 0
                or length > _MAX_WARC_BYTES
            ):
                counts["index_queries_incomplete"] += 1
                details.append(f"{target.record_id}:capture={timestamp},cdx=invalid_metadata")
                continue
            range_url = f"https://{_DATA_HOST}/{filename}"
            end = offset + length - 1
            try:
                warc = self._fetcher(range_url, {"Range": f"bytes={offset}-{end}"})
            except (OSError, TimeoutError, ValueError) as error:
                counts["warc_requests_failed"] += 1
                details.append(
                    f"{target.record_id}:capture={timestamp},warc=unavailable,"
                    f"error={type(error).__name__}"
                )
                continue
            counts["warc_range_requests"] += 1
            content_range = next(
                (value for name, value in warc.headers.items() if name.lower() == "content-range"),
                "",
            )
            range_match = _CONTENT_RANGE.fullmatch(content_range)
            if (
                warc.status != 206
                or warc.final_url != range_url
                or len(warc.body) != length
                or range_match is None
                or int(range_match.group("start")) != offset
                or int(range_match.group("end")) != end
                or int(range_match.group("total")) <= end
            ):
                counts["warc_requests_failed"] += 1
                details.append(f"{target.record_id}:capture={timestamp},warc=invalid_range")
                continue
            try:
                status_ok, timestamp_ok, digest_ok, target_ok, payload = _parse_warc(
                    warc.body,
                    target_url=target.url,
                    expected_timestamp=datetime.strptime(timestamp, "%Y%m%d%H%M%S")
                    .replace(tzinfo=UTC)
                    .strftime("%Y%m%d%H%M%S"),
                    expected_digest=digest,
                )
            except (OSError, ValueError):
                counts["index_queries_incomplete"] += 1
                details.append(f"{target.record_id}:capture={timestamp},warc=invalid_record")
                continue
            gbk_identity_ok = _identity_matches(payload, target)
            identity_ok = status_ok and target_ok and digest_ok and timestamp_ok and gbk_identity_ok
            counts["warc_payload_digest_matches_cdx"] += int(digest_ok)
            counts["warc_target_uri_matches"] += int(target_ok)
            counts["warc_timestamps_match_cdx"] += int(timestamp_ok)
            counts["gbk_identity_matches"] += int(gbk_identity_ok)
            counts["verified_pre_cutoff_targets"] += int(identity_ok)
            details.append(
                f"{target.record_id}:capture={timestamp},status_200={int(status_ok)},"
                f"target_match={int(target_ok)},timestamp_match={int(timestamp_ok)},"
                f"digest_match={int(digest_ok)},gbk_identity_match={int(identity_ok)}"
            )
        details.extend(
            (
                f"adapter_sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}",
                (
                    "scope=integrity; response-only probe queried one pinned Common Crawl "
                    "index and did not replay WARC bytes; this can test response semantics but "
                    "cannot verify a historical PCF witness"
                    if response_probe
                    else "scope=integrity; only pinned SZSE PCF URLs were queried through "
                    "Common Crawl collection indexes and at most one latest pre-cutoff WARC "
                    "range per target; a witness requires exact CDX target, HTTP 200, WARC "
                    "timestamp and payload digest agreement, complete byte range, and GBK "
                    "code/date identity; empty or unavailable indexes do not prove historical "
                    "absence, source publication time, PIT universe membership or strategy "
                    "performance"
                ),
            )
        )
        return Measured(
            metrics=tuple(_metric(name, value) for name, value in counts.items()),
            samples=len(targets),
            details=tuple(details),
        )
