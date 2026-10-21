"""Measure exact Wayback captures of public SSE index notices before a cutoff."""

from __future__ import annotations

import base64
import hashlib
import json
import re
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

ADAPTER_ID = "wayback_sse_index_notice_v1"
_SCHEMA = "wayback-sse-notice-targets/v1"
_CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
_MAX_TARGETS = 20
_MAX_CDX_BYTES = 2 * 1024 * 1024
_MAX_REPLAY_BYTES = 10 * 1024 * 1024
_CDX_ROW_LIMIT = 1000
_REPLAY_CANDIDATE_LIMIT = 3
_NOTICE_PATH = re.compile(r"^/market/sseindex/diclosure/c/c_\d{8}_\d+\.shtml$")
_PAGE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EFFECTIVE_DATE = re.compile(
    r"于\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*"
    r"(?:收盘后\s*)?(?:生效|调整)"
)


class _Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    cutoff_utc: str = Field(min_length=1)
    page_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class _TargetFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str = Field(alias="schema", pattern=f"^{re.escape(_SCHEMA)}$")
    targets: tuple[_Target, ...] = Field(min_length=1, max_length=_MAX_TARGETS)


@dataclass(frozen=True)
class WaybackResponse:
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


def _fetch_wayback(url: str) -> WaybackResponse:
    request = Request(
        url,
        headers={"User-Agent": "EpiPilot public archive verification/1.0"},
        method="GET",
    )
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=12) as response:
            status = response.status
            final_url = response.geturl()
            headers = {str(k).lower(): str(v).strip() for k, v in response.headers.items()}
            body = response.read(_MAX_REPLAY_BYTES + 1)
    except HTTPError as error:
        status = error.code
        final_url = error.geturl()
        headers = {str(k).lower(): str(v).strip() for k, v in error.headers.items()}
        body = error.read(_MAX_REPLAY_BYTES + 1)
    max_bytes = _MAX_CDX_BYTES if urlparse(url).path == "/cdx/search/cdx" else _MAX_REPLAY_BYTES
    if len(body) > max_bytes:
        raise ValueError("Wayback response exceeds the registered byte limit")
    return WaybackResponse(status=status, final_url=final_url, headers=headers, body=body)


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


def _cutoff(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Wayback cutoff must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("Wayback cutoff must include a timezone")
    parsed = parsed.astimezone(UTC)
    if parsed > datetime.now(UTC):
        raise ValueError("Wayback cutoff cannot be in the future")
    return parsed


def _cdx_url(target: _Target, cutoff: datetime) -> str:
    params = (
        ("url", target.url),
        ("matchType", "exact"),
        ("output", "json"),
        ("from", "2000"),
        ("to", cutoff.strftime("%Y%m%d%H%M%S")),
        ("fl", "timestamp,original,mimetype,statuscode,digest,length"),
        ("filter", "statuscode:200"),
        ("filter", "mimetype:text/html"),
        ("limit", str(_CDX_ROW_LIMIT)),
        ("gzip", "false"),
    )
    return f"{_CDX_ENDPOINT}?{urlencode(params)}"


def _parse_cdx(body: bytes) -> tuple[tuple[str, str, str, str, str, str], ...]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Wayback CDX response is not valid JSON") from error
    if not isinstance(payload, list) or not payload:
        raise ValueError("Wayback CDX response is missing its format header")
    header = payload[0]
    required = ("timestamp", "original", "mimetype", "statuscode", "digest", "length")
    if not isinstance(header, list) or not all(name in header for name in required):
        raise ValueError("Wayback CDX response has an unexpected field header")
    indexes = tuple(header.index(name) for name in required)
    rows: list[tuple[str, str, str, str, str, str]] = []
    for raw in payload[1:]:
        if not isinstance(raw, list) or len(raw) != len(header):
            raise ValueError("Wayback CDX response contains a malformed row")
        values = tuple(str(raw[index]) for index in indexes)
        rows.append((values[0], values[1], values[2], values[3], values[4], values[5]))
    if len(rows) >= _CDX_ROW_LIMIT:
        raise ValueError("Wayback CDX result limit reached; completeness is unknown")
    return tuple(rows)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.italic_text: list[str] = []
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
            self.italic_text.append(data)


def _notice_dates(body: bytes) -> tuple[str | None, str | None]:
    parser = _TextParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    page_candidates: set[date] = set()
    for value in parser.italic_text:
        candidate = value.strip()
        if not _PAGE_DATE.fullmatch(candidate):
            continue
        try:
            page_candidates.add(date.fromisoformat(candidate))
        except ValueError:
            continue
    content = " ".join(" ".join(parser.text).split())
    effective_candidates: set[date] = set()
    for match in _EFFECTIVE_DATE.finditer(content):
        year, month, day = (int(part) for part in match.groups())
        try:
            effective_candidates.add(date(year, month, day))
        except ValueError:
            continue
    page_date = next(iter(page_candidates)).isoformat() if len(page_candidates) == 1 else None
    effective_date = (
        next(iter(effective_candidates)).isoformat() if len(effective_candidates) == 1 else None
    )
    return page_date, effective_date


def _sha1_base32(body: bytes) -> str:
    return base64.b32encode(hashlib.sha1(body).digest()).decode("ascii").rstrip("=")


class WaybackSseNoticeComputer(Computer):
    """Verify exact archive bytes and dated content as available by a pinned UTC cutoff."""

    quality = Quality.MEASUREMENT

    def __init__(self, fetcher: Callable[[str], WaybackResponse] | None = None) -> None:
        self._fetcher = fetcher or _fetch_wayback

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID:
            raise ValueError("contract uses another Wayback SSE notice adapter")
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
            raise ValueError("contract must bind exactly one Wayback SSE target manifest")

        targets = manifests[0].targets
        if len({item.record_id for item in targets}) != len(targets):
            raise ValueError("Wayback target manifest repeats a record ID")
        if len({item.url for item in targets}) != len(targets):
            raise ValueError("Wayback target manifest repeats an original URL")

        cdx_count = snapshots_accessed = digest_matches = page_matches = effective_matches = 0
        witnesses = post_cutoff = 0
        observations: list[str] = []
        for target in targets:
            if not _allowlisted_notice(target.url):
                raise ValueError("target is not an allowlisted SSE index notice URL")
            cutoff = _cutoff(target.cutoff_utc)
            try:
                date.fromisoformat(target.page_date)
                date.fromisoformat(target.effective_date)
            except ValueError as error:
                raise ValueError("notice dates must be valid calendar dates") from error

            cdx_request = _cdx_url(target, cutoff)
            cdx_response = self._fetcher(cdx_request)
            if cdx_response.status != 200 or cdx_response.final_url != cdx_request:
                raise ValueError("Wayback CDX request failed or redirected")
            rows = _parse_cdx(cdx_response.body)
            cdx_count += len(rows)
            eligible: list[tuple[str, str, str]] = []
            for timestamp, original, mimetype, status, digest, length in rows:
                if (
                    original != target.url
                    or status != "200"
                    or mimetype.lower() != "text/html"
                    or not re.fullmatch(r"\d{14}", timestamp)
                    or not re.fullmatch(r"[A-Z2-7]{32}", digest)
                    or not length.isdigit()
                ):
                    continue
                capture_time = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                if capture_time > cutoff:
                    post_cutoff += 1
                    continue
                eligible.append((timestamp, digest, length))

            target_witness = False
            for timestamp, expected_digest, expected_length in sorted(eligible, reverse=True)[
                :_REPLAY_CANDIDATE_LIMIT
            ]:
                replay_url = f"https://web.archive.org/web/{timestamp}id_/{target.url}"
                replay = self._fetcher(replay_url)
                if replay.status != 200 or replay.final_url != replay_url:
                    observations.append(
                        f"{target.record_id}:capture={timestamp},replay=unavailable"
                    )
                    continue
                snapshots_accessed += 1
                bytes_match = (
                    len(replay.body) == int(expected_length)
                    and _sha1_base32(replay.body) == expected_digest
                )
                digest_matches += int(bytes_match)
                if not bytes_match:
                    observations.append(f"{target.record_id}:capture={timestamp},digest_match=0")
                    continue
                page_date, effective_date = _notice_dates(replay.body)
                page_match = page_date == target.page_date
                effective_match = effective_date == target.effective_date
                page_matches += int(page_match)
                effective_matches += int(effective_match)
                observations.append(
                    f"{target.record_id}:capture_utc={timestamp},sha1_match=1,"
                    f"page_date_match={int(page_match)},"
                    f"effective_date_match={int(effective_match)}"
                )
                if page_match and effective_match:
                    target_witness = True
                    break
            witnesses += int(target_witness)

        return Measured(
            metrics=(
                Metric(name="exact_cdx_rows", value=float(cdx_count)),
                Metric(name="snapshots_accessed", value=float(snapshots_accessed)),
                Metric(name="snapshot_sha1_matches_cdx", value=float(digest_matches)),
                Metric(name="snapshot_page_dates_match", value=float(page_matches)),
                Metric(name="snapshot_effective_dates_match", value=float(effective_matches)),
                Metric(name="verified_pre_cutoff_snapshots", value=float(witnesses)),
                Metric(name="post_cutoff_rows_ignored", value=float(post_cutoff)),
            ),
            samples=len(targets),
            details=(
                "scope=integrity; Wayback CDX exact-URL index and raw replay were re-fetched; "
                "matching bytes and dated page content prove an archive witness no later than "
                "the pinned cutoff only; missing captures do not prove historical absence or "
                "complete archive coverage",
                *observations,
            ),
        )
