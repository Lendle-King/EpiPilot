"""Measure a pinned Common Crawl WARC witness for official SSE index notices."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.message import Message
from html.parser import HTMLParser
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

ADAPTER_ID = "commoncrawl_sse_index_notice_v2"
_SCHEMA = "commoncrawl-sse-notice-targets/v1"
_INDEX_HOST = "index.commoncrawl.org"
_DATA_HOST = "data.commoncrawl.org"
_REQUEST_TIMEOUT_SECONDS = 8
_MAX_TARGETS = 20
_MAX_INDEX_BYTES = 1024 * 1024
_MAX_WARC_BYTES = 10 * 1024 * 1024
_MAX_WARC_DECOMPRESSED_BYTES = 16 * 1024 * 1024
_MAX_CDX_ROWS = 1000
_MAX_REPLAY_CANDIDATES = 2
_NOTICE_PATH = re.compile(r"^/market/sseindex/diclosure/c/c_\d{8}_\d+\.shtml$")
_CRAWL_ID = re.compile(r"^CC-MAIN-\d{4}-\d{2}$")
_FILENAME = re.compile(
    r"^crawl-data/(?P<crawl>CC-MAIN-\d{4}-\d{2})/segments/[0-9.]+/warc/"
    r"CC-MAIN-[0-9-]+-\d{5}\.warc\.gz$"
)
_PAGE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EFFECTIVE_DATE = re.compile(
    r"于\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*"
    r"(?:收盘后\s*)?(?:生效|调整)"
)


class _Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    crawl_id: str = Field(pattern=r"^CC-MAIN-\d{4}-\d{2}$")
    crawl_window_start_utc: str = Field(min_length=1)
    crawl_window_end_utc: str = Field(min_length=1)
    url: str = Field(min_length=1)
    cutoff_utc: str = Field(min_length=1)
    page_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class _TargetFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str = Field(alias="schema", pattern=f"^{re.escape(_SCHEMA)}$")
    targets: tuple[_Target, ...] = Field(min_length=1, max_length=_MAX_TARGETS)


@dataclass(frozen=True)
class CommonCrawlResponse:
    status: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes


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
        return None


def _fetch_commoncrawl(url: str, request_headers: Mapping[str, str]) -> CommonCrawlResponse:
    request = Request(
        url,
        headers={"User-Agent": "EpiPilot Common Crawl SSE notice verifier/1.0", **request_headers},
        method="GET",
    )
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
            final_url = response.geturl()
            headers = {str(k).lower(): str(v).strip() for k, v in response.headers.items()}
            maximum = _MAX_INDEX_BYTES if urlparse(url).hostname == _INDEX_HOST else _MAX_WARC_BYTES
            body = response.read(maximum + 1)
    except HTTPError as error:
        status = error.code
        final_url = error.geturl()
        headers = {str(k).lower(): str(v).strip() for k, v in error.headers.items()}
        body = error.read(_MAX_INDEX_BYTES + 1)
    maximum = _MAX_INDEX_BYTES if urlparse(url).hostname == _INDEX_HOST else _MAX_WARC_BYTES
    if len(body) > maximum:
        raise ValueError("Common Crawl response exceeds the registered byte limit")
    return CommonCrawlResponse(status=status, final_url=final_url, headers=headers, body=body)


def _allowlisted_notice(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.sse.com.cn"
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.query
        and not parsed.fragment
        and _NOTICE_PATH.fullmatch(parsed.path) is not None
    )


def _utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _index_url(target: _Target) -> str:
    query = urlencode((("url", target.url), ("output", "json")))
    return f"https://{_INDEX_HOST}/{target.crawl_id}-index?{query}"


def _parse_index(body: bytes) -> tuple[dict[str, object], ...]:
    stripped = body.strip()
    if not stripped or stripped == b"[]":
        return ()
    rows: list[dict[str, object]] = []
    for line in stripped.splitlines():
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Common Crawl index response contains invalid JSON") from error
        if not isinstance(row, dict):
            raise ValueError("Common Crawl index response row is not an object")
        rows.append(row)
        if len(rows) > _MAX_CDX_ROWS:
            raise ValueError("Common Crawl index result limit reached")
    return tuple(rows)


def _is_exact_no_captures(response: CommonCrawlResponse, target_url: str) -> bool:
    if response.status != 404:
        return False
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or set(payload) != {"message"}:
        return False
    message = payload["message"]
    return isinstance(message, str) and message == f"No Captures found for: {target_url}"


def _headers(raw: bytes) -> tuple[str, dict[str, str]]:
    text = raw.decode("latin-1", errors="replace")
    lines = text.splitlines()
    first = lines[0] if lines else ""
    values: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator:
            values[name.strip().lower()] = value.strip()
    return first, values


def _notice_dates(body: bytes) -> tuple[str | None, str | None]:
    parser = _NoticeParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    page_dates: set[date] = set()
    for text in parser.italic:
        value = text.strip()
        if not _PAGE_DATE.fullmatch(value):
            continue
        try:
            page_dates.add(date.fromisoformat(value))
        except ValueError:
            continue
    plain_text = " ".join(" ".join(parser.text).split())
    effective_dates: set[date] = set()
    for match in _EFFECTIVE_DATE.finditer(plain_text):
        year, month, day = (int(value) for value in match.groups())
        try:
            effective_dates.add(date(year, month, day))
        except ValueError:
            continue
    return (
        next(iter(page_dates)).isoformat() if len(page_dates) == 1 else None,
        next(iter(effective_dates)).isoformat() if len(effective_dates) == 1 else None,
    )


class _NoticeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.italic: list[str] = []
        self._inside_italic = False
        self._ignored: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style"}:
            self._ignored.append(tag)
        elif tag == "i" and not self._ignored:
            self._inside_italic = True

    def handle_endtag(self, tag: str) -> None:
        if self._ignored and tag == self._ignored[-1]:
            self._ignored.pop()
        elif tag == "i":
            self._inside_italic = False

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        self.text.append(data)
        if self._inside_italic:
            self.italic.append(data)


def _parse_warc(
    compressed: bytes, *, target_url: str, expected_timestamp: str, expected_digest: str
) -> tuple[bool, bool, bool, bool, bytes]:
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    decoded = decompressor.decompress(compressed, _MAX_WARC_DECOMPRESSED_BYTES + 1)
    if (
        len(decoded) > _MAX_WARC_DECOMPRESSED_BYTES
        or decompressor.unconsumed_tail
        or not decompressor.eof
    ):
        raise ValueError("Common Crawl WARC record is truncated or exceeds the decompressed limit")
    separator = b"\r\n\r\n"
    header_raw, found, block = decoded.partition(separator)
    if not found:
        separator = b"\n\n"
        header_raw, found, block = decoded.partition(separator)
    if not found:
        raise ValueError("Common Crawl WARC record has no header boundary")
    _, warc_headers = _headers(header_raw)
    if warc_headers.get("warc-type", "").lower() != "response":
        return False, False, False, False, b""
    if warc_headers.get("warc-target-uri") != target_url:
        return False, False, False, False, b""
    date_value = warc_headers.get("warc-date", "")
    try:
        warc_timestamp = datetime.fromisoformat(date_value.replace("Z", "+00:00")).strftime(
            "%Y%m%d%H%M%S"
        )
    except ValueError:
        return False, False, False, False, b""
    timestamp_match = warc_timestamp == expected_timestamp
    try:
        block_length = int(warc_headers.get("content-length", ""))
    except ValueError:
        return False, False, timestamp_match, False, b""
    if block_length < 0 or block_length > len(block):
        return False, False, timestamp_match, False, b""
    http_block = block[:block_length]
    http_head, found, payload = http_block.partition(separator)
    if not found:
        other_separator = b"\n\n" if separator == b"\r\n\r\n" else b"\r\n\r\n"
        http_head, found, payload = http_block.partition(other_separator)
    if not found:
        return False, False, timestamp_match, False, b""
    status_line, http_headers = _headers(http_head)
    status_match = bool(re.match(r"^HTTP/\d(?:\.\d)?\s+200\b", status_line))
    payload_digest = warc_headers.get("warc-payload-digest", "").lower()
    actual_digest = base64.b32encode(hashlib.sha1(payload).digest()).decode("ascii").rstrip("=")
    digest_match = (
        payload_digest == f"sha1:{expected_digest.lower()}"
        and actual_digest == expected_digest.upper()
    )
    encoding = http_headers.get("content-encoding", "").lower()
    if encoding == "gzip":
        decoder = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
        decoded_payload = decoder.decompress(payload, _MAX_WARC_DECOMPRESSED_BYTES + 1)
        if len(decoded_payload) > _MAX_WARC_DECOMPRESSED_BYTES or decoder.unconsumed_tail:
            decoded_payload = b""
    elif encoding in {"", "identity"}:
        decoded_payload = payload
    else:
        decoded_payload = b""
    return status_match, timestamp_match, digest_match, True, decoded_payload


class CommonCrawlSseNoticeComputer(Computer):
    """Re-fetch one pinned Common Crawl index and verify raw pre-cutoff WARC payloads."""

    quality = Quality.MEASUREMENT

    def __init__(
        self,
        fetcher: Callable[[str, Mapping[str, str]], CommonCrawlResponse] | None = None,
    ) -> None:
        self._fetcher = fetcher or _fetch_commoncrawl

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID:
            raise ValueError("contract uses another Common Crawl SSE notice adapter")
        inputs = {item.path: safe_input(root, item.path).read_bytes() for item in contract.inputs}
        manifests: list[_TargetFile] = []
        for raw in inputs.values():
            try:
                data = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("schema") == _SCHEMA:
                manifests.append(_TargetFile.model_validate(data))
        if len(manifests) != 1 or len(inputs) != 1:
            raise ValueError("contract must bind exactly one Common Crawl SSE target manifest")
        targets = manifests[0].targets
        if len({item.record_id for item in targets}) != len(targets):
            raise ValueError("Common Crawl target manifest repeats a record ID")
        if len({item.url for item in targets}) != len(targets):
            raise ValueError("Common Crawl target manifest repeats an original URL")

        index_200 = index_empty = index_no_captures = index_rows = 0
        pre_cutoff = post_cutoff = 0
        warc_requests = warc_failed = payload_digest_matches = target_matches = 0
        timestamp_matches = notice_dates_match = witnesses = index_failed = index_incomplete = 0
        index_bytes = 0
        observations: list[str] = []
        for target in targets:
            if not _allowlisted_notice(target.url):
                raise ValueError("target is not an allowlisted SSE index notice URL")
            cutoff = _utc(target.cutoff_utc, "cutoff_utc")
            crawl_start = _utc(target.crawl_window_start_utc, "crawl_window_start_utc")
            crawl_end = _utc(target.crawl_window_end_utc, "crawl_window_end_utc")
            if not crawl_start <= cutoff <= crawl_end:
                raise ValueError("cutoff must fall within the pinned Common Crawl window")
            if (
                target.crawl_id
                != f"CC-MAIN-{crawl_start.year}-{target.crawl_id.rsplit('-', 1)[-1]}"
            ):
                raise ValueError("crawl ID year must match its pinned crawl window")
            try:
                date.fromisoformat(target.page_date)
                date.fromisoformat(target.effective_date)
            except ValueError as error:
                raise ValueError("notice dates must be valid calendar dates") from error

            index_url = _index_url(target)
            try:
                response = self._fetcher(index_url, {})
            except (OSError, TimeoutError, ValueError) as error:
                index_failed += 1
                observations.append(
                    f"{target.record_id}:index=unavailable,error={type(error).__name__}"
                )
                continue
            if response.final_url != index_url:
                index_failed += 1
                observations.append(
                    f"{target.record_id}:index=unavailable,status={response.status},redirected=1"
                )
                continue
            index_bytes += len(response.body)
            response_sha256 = hashlib.sha256(response.body).hexdigest()
            if _is_exact_no_captures(response, target.url):
                index_no_captures += 1
                observations.append(
                    f"{target.record_id}:index_status=404,index_no_captures=1,"
                    f"index_bytes={len(response.body)},index_sha256={response_sha256}"
                )
                continue
            if response.status != 200:
                index_failed += 1
                observations.append(
                    f"{target.record_id}:index=unavailable,status={response.status},"
                    f"index_bytes={len(response.body)},index_sha256={response_sha256}"
                )
                continue
            index_200 += 1
            try:
                rows = _parse_index(response.body)
            except ValueError:
                index_incomplete += 1
                observations.append(
                    f"{target.record_id}:index=invalid_or_incomplete,"
                    f"bytes={len(response.body)},sha256={response_sha256}"
                )
                continue
            index_empty += int(not rows)
            exact_rows = [row for row in rows if row.get("url") == target.url]
            index_rows += len(exact_rows)
            observations.append(
                f"{target.record_id}:index_status=200,index_bytes={len(response.body)},"
                f"index_sha256={response_sha256},exact_rows={len(exact_rows)}"
            )
            eligible: list[dict[str, object]] = []
            for row in exact_rows:
                if (
                    str(row.get("status")) != "200"
                    or str(row.get("mime", "")).split(";")[0].lower() != "text/html"
                ):
                    continue
                timestamp = str(row.get("timestamp", ""))
                if not re.fullmatch(r"\d{14}", timestamp):
                    index_incomplete += 1
                    continue
                try:
                    capture_time = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                except ValueError:
                    index_incomplete += 1
                    continue
                if capture_time > cutoff:
                    post_cutoff += 1
                    continue
                pre_cutoff += 1
                eligible.append(row)

            target_witness = False
            for row in sorted(
                eligible, key=lambda value: str(value.get("timestamp", "")), reverse=True
            )[:_MAX_REPLAY_CANDIDATES]:
                timestamp = str(row.get("timestamp", ""))
                filename = str(row.get("filename", ""))
                match = _FILENAME.fullmatch(filename)
                if match is None or match.group("crawl") != target.crawl_id:
                    index_incomplete += 1
                    observations.append(f"{target.record_id}:capture={timestamp},warc_path=invalid")
                    continue
                try:
                    offset = int(str(row.get("offset", "")))
                    length = int(str(row.get("length", "")))
                except ValueError:
                    index_incomplete += 1
                    continue
                if offset < 0 or length <= 0 or length > _MAX_WARC_BYTES:
                    index_incomplete += 1
                    continue
                expected_digest = str(row.get("digest", "")).upper()
                if not re.fullmatch(r"[A-Z2-7]{32}", expected_digest):
                    index_incomplete += 1
                    continue
                range_url = f"https://{_DATA_HOST}/{filename}"
                end = offset + length - 1
                try:
                    warc_response = self._fetcher(range_url, {"Range": f"bytes={offset}-{end}"})
                except (OSError, TimeoutError, ValueError) as error:
                    warc_failed += 1
                    observations.append(
                        f"{target.record_id}:capture={timestamp},warc=unavailable,"
                        f"error={type(error).__name__}"
                    )
                    continue
                warc_requests += 1
                content_range = next(
                    (
                        value
                        for name, value in warc_response.headers.items()
                        if name.lower() == "content-range"
                    ),
                    "",
                )
                if (
                    warc_response.status != 206
                    or warc_response.final_url != range_url
                    or len(warc_response.body) != length
                    or not content_range.startswith(f"bytes {offset}-{end}/")
                ):
                    warc_failed += 1
                    observations.append(
                        f"{target.record_id}:capture={timestamp},warc=invalid_range"
                    )
                    continue
                try:
                    (
                        status_match,
                        timestamp_match,
                        digest_match,
                        target_present,
                        notice_body,
                    ) = _parse_warc(
                        warc_response.body,
                        target_url=target.url,
                        expected_timestamp=timestamp,
                        expected_digest=expected_digest,
                    )
                except (OSError, ValueError, zlib.error):
                    index_incomplete += 1
                    observations.append(
                        f"{target.record_id}:capture={timestamp},warc=invalid_record"
                    )
                    continue
                payload_digest_matches += int(digest_match)
                target_matches += int(target_present)
                timestamp_matches += int(timestamp_match)
                if (
                    not status_match
                    or not target_present
                    or not digest_match
                    or not timestamp_match
                ):
                    observations.append(
                        f"{target.record_id}:capture={timestamp},status_200={int(status_match)},"
                        f"target_match={int(target_present)},timestamp_match={int(timestamp_match)},"
                        f"digest_match={int(digest_match)}"
                    )
                    continue
                page_date, effective_date = _notice_dates(notice_body)
                dates_match = (
                    page_date == target.page_date and effective_date == target.effective_date
                )
                notice_dates_match += int(dates_match)
                observations.append(
                    f"{target.record_id}:capture={timestamp},status_200=1,target_match=1,"
                    f"timestamp_match=1,digest_match=1,notice_dates_match={int(dates_match)}"
                )
                if dates_match:
                    target_witness = True
                    break
            witnesses += int(target_witness)

        return Measured(
            metrics=(
                Metric(name="index_http_200_responses", value=float(index_200)),
                Metric(name="index_empty_responses", value=float(index_empty)),
                Metric(name="index_no_captures_responses", value=float(index_no_captures)),
                Metric(name="index_response_bytes", value=float(index_bytes)),
                Metric(name="index_rows", value=float(index_rows)),
                Metric(name="pre_cutoff_rows", value=float(pre_cutoff)),
                Metric(name="post_cutoff_rows", value=float(post_cutoff)),
                Metric(name="warc_range_requests", value=float(warc_requests)),
                Metric(name="warc_requests_failed", value=float(warc_failed)),
                Metric(name="warc_payload_digest_matches_cdx", value=float(payload_digest_matches)),
                Metric(name="warc_target_uri_matches", value=float(target_matches)),
                Metric(name="warc_timestamps_match_cdx", value=float(timestamp_matches)),
                Metric(name="notice_dates_match", value=float(notice_dates_match)),
                Metric(name="verified_pre_cutoff_snapshots", value=float(witnesses)),
                Metric(name="index_requests_failed", value=float(index_failed)),
                Metric(name="index_queries_incomplete", value=float(index_incomplete)),
            ),
            samples=len(targets),
            details=(
                "scope=integrity; exact Common Crawl collection index and WARC byte range were "
                "re-fetched; verified capture means matching WARC URL/time/payload digest and "
                "notice dates before the pinned cutoff; missing index rows do not prove page "
                "unavailability or complete crawl coverage",
                *observations,
            ),
        )
