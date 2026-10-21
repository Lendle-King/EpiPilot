"""Verify exact Common Crawl WARC captures of official SSE ETF PCF downloads."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric
from epipilot.workspace.commoncrawl_sse_notice import (
    CommonCrawlResponse,
    _is_exact_no_captures,
    _parse_index,
    _parse_warc,
)
from epipilot.workspace.commoncrawl_szse_pcf import _fetch_commoncrawl
from epipilot.workspace.wayback_sse_pcf_archive import _parse_pcf_identity

ADAPTER_ID = "commoncrawl_sse_pcf_v1"
ADAPTER_ID_LONG_INDEX_TIMEOUT = "commoncrawl_sse_pcf_v2"
ADAPTER_ID_STABLE_LONG_INDEX_TIMEOUT = "commoncrawl_sse_pcf_v3"
_SCHEMA = "commoncrawl-sse-pcf-targets/v1"
_INDEX_HOST = "index.commoncrawl.org"
_DATA_HOST = "data.commoncrawl.org"
_OFFICIAL_ENDPOINT = "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do"
_REQUEST_TIMEOUT_SECONDS = 8
_LONG_INDEX_TIMEOUT_SECONDS = 16
_MAX_TARGETS = 20
_MAX_REPLAY_CANDIDATES = 2
_MAX_WARC_RANGE_BYTES = 2 * 1024 * 1024
_CDX_DIGEST = re.compile(r"^[A-Z2-7]{32}$")
_TIMESTAMP = re.compile(r"^\d{14}$")
_FILENAME = re.compile(
    r"^crawl-data/(?P<crawl>CC-MAIN-\d{4}-\d{2})/segments/[0-9.]+/warc/"
    r"CC-MAIN-[0-9-]+-\d{5}\.warc\.gz$"
)
_CONTENT_RANGE = re.compile(r"^bytes (?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+)$")


class _Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    fund_code: str = Field(pattern=r"^\d{6}$")
    trading_day: str = Field(pattern=r"^\d{8}$")
    crawl_id: str = Field(pattern=r"^CC-MAIN-\d{4}-\d{2}$")
    crawl_window_start_utc: str = Field(min_length=1)
    crawl_window_end_utc: str = Field(min_length=1)
    cutoff_utc: str = Field(min_length=1)


class _TargetFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str = Field(alias="schema", pattern=f"^{re.escape(_SCHEMA)}$")
    targets: tuple[_Target, ...] = Field(min_length=1, max_length=_MAX_TARGETS)


def _utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    parsed = parsed.astimezone(UTC)
    if parsed > datetime.now(UTC):
        raise ValueError(f"{name} cannot be in the future")
    return parsed


def _validate_target(target: _Target) -> tuple[date, datetime, datetime, datetime]:
    if target.url != f"{_OFFICIAL_ENDPOINT}?fundCode={target.fund_code}":
        raise ValueError("target must use the exact SSE PCF download URL")
    parsed = urlparse(target.url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "query.sse.com.cn"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path != "/etfDownload/downloadETF2Bulletin.do"
        or parsed.fragment
    ):
        raise ValueError("target must use the exact SSE PCF download URL")
    try:
        trading_day = date.fromisoformat(
            f"{target.trading_day[:4]}-{target.trading_day[4:6]}-{target.trading_day[6:8]}"
        )
    except ValueError as error:
        raise ValueError("SSE PCF trading day must be a valid calendar date") from error
    crawl_start = _utc(target.crawl_window_start_utc, "crawl_window_start_utc")
    crawl_end = _utc(target.crawl_window_end_utc, "crawl_window_end_utc")
    cutoff = _utc(target.cutoff_utc, "cutoff_utc")
    if crawl_start > crawl_end or not crawl_start <= cutoff <= crawl_end:
        raise ValueError("cutoff must fall within the pinned Common Crawl window")
    crawl_year = int(target.crawl_id.split("-")[2])
    if crawl_start.year != crawl_year:
        raise ValueError("Common Crawl ID year must match its pinned collection window")
    shanghai = cutoff.astimezone(ZoneInfo("Asia/Shanghai"))
    if shanghai.date() != trading_day or (
        shanghai.hour,
        shanghai.minute,
        shanghai.second,
        shanghai.microsecond,
    ) != (9, 15, 0, 0):
        raise ValueError("cutoff must be 09:15 Asia/Shanghai on the pinned PCF trading day")
    return trading_day, crawl_start, crawl_end, cutoff


def _index_url(target: _Target) -> str:
    query = urlencode((("url", target.url), ("output", "json")))
    return f"https://{_INDEX_HOST}/{target.crawl_id}-index?{query}"


class CommonCrawlSSEPCFArchiveComputer(Computer):
    """Verify an exact pre-open SSE PCF capture from Common Crawl index and WARC bytes."""

    quality = Quality.MEASUREMENT
    adapter_id: ClassVar[str] = ADAPTER_ID
    index_timeout_seconds: ClassVar[int] = _REQUEST_TIMEOUT_SECONDS
    warc_timeout_seconds: ClassVar[int] = _REQUEST_TIMEOUT_SECONDS
    canonicalize_unavailable: ClassVar[bool] = False

    def __init__(
        self,
        fetcher: Callable[[str, Mapping[str, str]], CommonCrawlResponse] | None = None,
    ) -> None:
        self._fetcher = fetcher

    def _fetch(self, url: str, headers: Mapping[str, str]) -> CommonCrawlResponse:
        if self._fetcher is not None:
            return self._fetcher(url, headers)
        timeout = (
            self.index_timeout_seconds
            if urlparse(url).hostname == _INDEX_HOST
            else self.warc_timeout_seconds
        )
        return _fetch_commoncrawl(url, headers, timeout)

    def _unavailable_detail(self, record_id: str, stage: str, reason: str) -> str:
        if self.canonicalize_unavailable:
            return f"{record_id}:{stage}=unavailable"
        return f"{record_id}:{stage}=unavailable,{reason}"

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != self.adapter_id:
            raise ValueError("contract uses another Common Crawl SSE PCF adapter")
        if len(contract.inputs) != 1:
            raise ValueError("contract must bind exactly one Common Crawl SSE PCF target manifest")
        raw = safe_input(root, contract.inputs[0].path).read_bytes()
        try:
            manifest = _TargetFile.model_validate_json(raw)
        except ValueError as error:
            raise ValueError("Common Crawl SSE PCF target manifest is invalid") from error
        targets = manifest.targets
        if len({target.record_id for target in targets}) != len(targets):
            raise ValueError("Common Crawl SSE PCF manifest repeats a record ID")
        if len({target.url for target in targets}) != len(targets):
            raise ValueError("Common Crawl SSE PCF manifest repeats a target URL")

        counts = {
            "index_http_200_responses": 0,
            "index_empty_responses": 0,
            "index_no_captures_responses": 0,
            "index_response_bytes": 0,
            "index_rows": 0,
            "pre_cutoff_rows": 0,
            "post_cutoff_rows_ignored": 0,
            "warc_range_requests": 0,
            "warc_requests_failed": 0,
            "warc_payload_digest_matches_cdx": 0,
            "warc_target_uri_matches": 0,
            "warc_timestamps_match_cdx": 0,
            "pcf_identity_matches": 0,
            "verified_pre_cutoff_pcf_captures": 0,
            "index_requests_failed": 0,
            "index_queries_incomplete": 0,
        }
        details: list[str] = []
        for target in targets:
            trading_day, crawl_start, crawl_end, cutoff = _validate_target(target)
            if (
                contract.boundary_date != trading_day.isoformat()
                or contract.cutoff_date != cutoff.date().isoformat()
            ):
                raise ValueError("contract dates do not match the pinned SSE PCF target")
            index_url = _index_url(target)
            try:
                response = self._fetch(index_url, {})
            except (OSError, TimeoutError, ValueError) as error:
                counts["index_requests_failed"] += 1
                details.append(
                    self._unavailable_detail(
                        target.record_id, "index", f"error={type(error).__name__}"
                    )
                )
                continue
            if response.final_url != index_url:
                counts["index_requests_failed"] += 1
                details.append(
                    self._unavailable_detail(
                        target.record_id,
                        "index",
                        f"status={response.status},redirected=1",
                    )
                )
                continue
            if _is_exact_no_captures(response, target.url):
                counts["index_no_captures_responses"] += 1
                semantic = f"No Captures found for: {target.url}"
                details.append(
                    f"{target.record_id}:index_status=404,no_captures=1,"
                    f"semantic_sha256={hashlib.sha256(semantic.encode('utf-8')).hexdigest()}"
                )
                continue
            if response.status != 200:
                counts["index_requests_failed"] += 1
                details.append(
                    self._unavailable_detail(
                        target.record_id,
                        "index",
                        f"status={response.status},redirected=0",
                    )
                )
                continue
            counts["index_http_200_responses"] += 1
            counts["index_response_bytes"] += len(response.body)
            index_sha256 = hashlib.sha256(response.body).hexdigest()
            try:
                rows = _parse_index(response.body)
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
            if any(row.get("url") != target.url for row in rows):
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
                    captured_at = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                except ValueError:
                    counts["index_queries_incomplete"] += 1
                    continue
                if not crawl_start <= captured_at <= crawl_end:
                    counts["index_queries_incomplete"] += 1
                    continue
                if captured_at > cutoff:
                    counts["post_cutoff_rows_ignored"] += 1
                    continue
                counts["pre_cutoff_rows"] += 1
                eligible.append(row)
            details.append(
                f"{target.record_id}:index_status=200,bytes={len(response.body)},"
                f"sha256={index_sha256},exact_rows={len(exact_rows)},"
                f"pre_cutoff={len(eligible)}"
            )

            for row in sorted(
                eligible, key=lambda item: str(item.get("timestamp", "")), reverse=True
            )[:_MAX_REPLAY_CANDIDATES]:
                timestamp = str(row.get("timestamp", ""))
                digest = str(row.get("digest", "")).upper()
                filename = str(row.get("filename", ""))
                filename_match = _FILENAME.fullmatch(filename)
                try:
                    offset = int(str(row.get("offset", "")))
                    length = int(str(row.get("length", "")))
                except ValueError:
                    counts["index_queries_incomplete"] += 1
                    details.append(f"{target.record_id}:capture={timestamp},cdx=invalid_range")
                    continue
                if (
                    filename_match is None
                    or filename_match.group("crawl") != target.crawl_id
                    or _CDX_DIGEST.fullmatch(digest) is None
                    or offset < 0
                    or length <= 0
                    or length > _MAX_WARC_RANGE_BYTES
                    or str(row.get("mime", "")).lower() in {"text/html", "application/xhtml+xml"}
                ):
                    counts["index_queries_incomplete"] += 1
                    details.append(f"{target.record_id}:capture={timestamp},cdx=invalid_metadata")
                    continue
                range_url = f"https://{_DATA_HOST}/{filename}"
                end = offset + length - 1
                if self.canonicalize_unavailable:
                    counts["warc_range_requests"] += 1
                try:
                    warc = self._fetch(range_url, {"Range": f"bytes={offset}-{end}"})
                except (OSError, TimeoutError, ValueError) as error:
                    counts["warc_requests_failed"] += 1
                    details.append(
                        self._unavailable_detail(
                            f"{target.record_id}:capture={timestamp}",
                            "warc",
                            f"error={type(error).__name__}",
                        )
                    )
                    continue
                content_range = next(
                    (
                        value
                        for name, value in warc.headers.items()
                        if name.lower() == "content-range"
                    ),
                    "",
                )
                range_match = _CONTENT_RANGE.fullmatch(content_range)
                if not self.canonicalize_unavailable:
                    counts["warc_range_requests"] += 1
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
                    details.append(
                        self._unavailable_detail(
                            f"{target.record_id}:capture={timestamp}",
                            "warc",
                            "invalid_range",
                        )
                        if self.canonicalize_unavailable
                        else f"{target.record_id}:capture={timestamp},warc=invalid_range"
                    )
                    continue
                try:
                    status_ok, timestamp_ok, digest_ok, target_ok, payload = _parse_warc(
                        warc.body,
                        target_url=target.url,
                        expected_timestamp=timestamp,
                        expected_digest=digest,
                    )
                except (OSError, ValueError):
                    counts["index_queries_incomplete"] += 1
                    details.append(f"{target.record_id}:capture={timestamp},warc=invalid_record")
                    continue
                try:
                    observed_code, observed_day = _parse_pcf_identity(payload)
                except ValueError:
                    observed_code, observed_day = "", ""
                identity_ok = (
                    observed_code == target.fund_code and observed_day == target.trading_day
                )
                verified = status_ok and timestamp_ok and digest_ok and target_ok and identity_ok
                counts["warc_payload_digest_matches_cdx"] += int(digest_ok)
                counts["warc_target_uri_matches"] += int(target_ok)
                counts["warc_timestamps_match_cdx"] += int(timestamp_ok)
                counts["pcf_identity_matches"] += int(identity_ok)
                counts["verified_pre_cutoff_pcf_captures"] += int(verified)
                details.append(
                    f"{target.record_id}:capture={timestamp},status_200={int(status_ok)},"
                    f"target_match={int(target_ok)},timestamp_match={int(timestamp_ok)},"
                    f"digest_match={int(digest_ok)},identity_match={int(identity_ok)}"
                )
                if verified:
                    break

        details.append(
            "scope=integrity; only pinned SSE PCF download URLs were queried through the "
            "registered Common Crawl collection and at most two pre-cutoff WARC ranges per "
            "target; a witness requires exact CDX URL, HTTP 200, complete WARC byte range, "
            "timestamp and payload digest agreement, and internal PCF code/date identity; "
            "empty or unavailable results do not prove source absence, publication time, PIT "
            "universe completeness or strategy performance"
        )
        return Measured(
            metrics=tuple(Metric(name=name, value=float(value)) for name, value in counts.items()),
            samples=len(targets),
            details=tuple(details),
        )


class CommonCrawlSSEPCFLongIndexTimeoutComputer(CommonCrawlSSEPCFArchiveComputer):
    """Allow more time for CDX index lookups while keeping WARC requests bounded."""

    adapter_id: ClassVar[str] = ADAPTER_ID_LONG_INDEX_TIMEOUT
    index_timeout_seconds: ClassVar[int] = _LONG_INDEX_TIMEOUT_SECONDS


class CommonCrawlSSEPCFStableLongIndexTimeoutComputer(CommonCrawlSSEPCFLongIndexTimeoutComputer):
    """Canonicalize transient unavailable outcomes for stable independent replay."""

    adapter_id: ClassVar[str] = ADAPTER_ID_STABLE_LONG_INDEX_TIMEOUT
    canonicalize_unavailable: ClassVar[bool] = True
