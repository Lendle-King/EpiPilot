"""Verify exact Wayback captures of official SSE ETF PCF downloads."""

from __future__ import annotations

import base64
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
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric

ADAPTER_ID = "wayback_sse_pcf_archive_v1"
_SCHEMA = "wayback-sse-pcf-archive-targets/v1"
_CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
_OFFICIAL_ENDPOINT = "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do"
_MAX_TARGETS = 20
_REQUEST_TIMEOUT_SECONDS = 4
_MAX_CDX_BYTES = 2 * 1024 * 1024
_MAX_PCF_BYTES = 25 * 1024 * 1024
_CDX_ROW_LIMIT = 1000
_REPLAY_CANDIDATE_LIMIT = 2
_BASE32_SHA1 = re.compile(r"^[A-Z2-7]{32}$")


class _Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    fund_code: str = Field(pattern=r"^\d{6}$")
    trading_day: str = Field(pattern=r"^\d{8}$")
    cutoff_utc: str = Field(min_length=1)


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
    request = Request(url, headers={"User-Agent": "EpiPilot public archive verification/1.0"})
    opener = build_opener(_NoRedirect())
    limit = _MAX_CDX_BYTES if urlparse(url).path == "/cdx/search/cdx" else _MAX_PCF_BYTES
    try:
        with opener.open(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
            final_url = response.geturl()
            headers = {
                str(key).lower(): str(value).strip() for key, value in response.headers.items()
            }
            body = response.read(limit + 1)
    except HTTPError as error:
        status = error.code
        final_url = error.geturl()
        headers = {str(key).lower(): str(value).strip() for key, value in error.headers.items()}
        body = error.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Wayback response exceeds the registered byte limit")
    return WaybackResponse(status=status, final_url=final_url, headers=headers, body=body)


def _cutoff(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("SSE PCF archive cutoff must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("SSE PCF archive cutoff must include a timezone")
    parsed = parsed.astimezone(UTC)
    if parsed > datetime.now(UTC):
        raise ValueError("SSE PCF archive cutoff cannot be in the future")
    return parsed


def _target_identity(target: _Target) -> tuple[date, datetime]:
    if target.url != f"{_OFFICIAL_ENDPOINT}?fundCode={target.fund_code}":
        raise ValueError("target must use the exact SSE PCF download URL")
    parsed_url = urlparse(target.url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "query.sse.com.cn"
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.port is not None
        or parsed_url.path != "/etfDownload/downloadETF2Bulletin.do"
        or parsed_url.fragment
    ):
        raise ValueError("target must use the exact SSE PCF download URL")
    try:
        trading_day = date.fromisoformat(
            f"{target.trading_day[:4]}-{target.trading_day[4:6]}-{target.trading_day[6:8]}"
        )
    except ValueError as error:
        raise ValueError("SSE PCF trading day must be a valid calendar date") from error
    cutoff = _cutoff(target.cutoff_utc)
    shanghai = cutoff.astimezone(ZoneInfo("Asia/Shanghai"))
    if shanghai.date() != trading_day or (
        shanghai.hour,
        shanghai.minute,
        shanghai.second,
        shanghai.microsecond,
    ) != (9, 15, 0, 0):
        raise ValueError("cutoff must be 09:15 Asia/Shanghai on the pinned PCF trading day")
    return trading_day, cutoff


def _cdx_url(target: _Target, cutoff: datetime) -> str:
    params = (
        ("url", target.url),
        ("matchType", "exact"),
        ("output", "json"),
        ("from", "2000"),
        ("to", cutoff.strftime("%Y%m%d%H%M%S")),
        ("fl", "timestamp,original,mimetype,statuscode,digest,length"),
        ("filter", "statuscode:200"),
        ("limit", str(_CDX_ROW_LIMIT)),
        ("gzip", "false"),
    )
    return f"{_CDX_ENDPOINT}?{urlencode(params)}"


def _parse_cdx(body: bytes) -> tuple[tuple[str, str, str, str, str, str], ...]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Wayback CDX response is not valid JSON") from error
    if not isinstance(payload, list):
        raise ValueError("Wayback CDX response is not a row array")
    if not payload:
        return ()
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


def _sha1_base32(body: bytes) -> str:
    return base64.b32encode(hashlib.sha1(body).digest()).decode("ascii").rstrip("=")


def _content_type(headers: Mapping[str, str]) -> str:
    value = next((item for key, item in headers.items() if key.lower() == "content-type"), "")
    return value.split(";", maxsplit=1)[0].strip().lower()


def _parse_pcf_identity(body: bytes) -> tuple[str, str]:
    normalized = body.lstrip(b"\xef\xbb\xbf \r\n\t")
    if normalized.startswith(b"<"):
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as error:
            raise ValueError("SSE PCF XML is malformed") from error
        fields: dict[str, list[str]] = {"FundInstrumentID": [], "TradingDay": []}
        for element in root.iter():
            name = element.tag.rsplit("}", maxsplit=1)[-1]
            if name in fields and element.text is not None:
                fields[name].append(element.text.strip())
        fund_codes = fields["FundInstrumentID"]
        trading_days = fields["TradingDay"]
    else:
        try:
            lines = normalized.splitlines()
            fund_codes = [
                line.partition(b"=")[2].decode("ascii", "strict")
                for line in lines
                if line.startswith(b"Fundid1=")
            ]
            trading_days = [
                line.partition(b"=")[2].decode("ascii", "strict")
                for line in lines
                if line.startswith(b"TradingDay=")
            ]
        except UnicodeDecodeError as error:
            raise ValueError("SSE PCF text identity is not ASCII encoded") from error
    if (
        len(fund_codes) != 1
        or re.fullmatch(r"\d{6}", fund_codes[0]) is None
        or len(trading_days) != 1
        or re.fullmatch(r"\d{8}", trading_days[0]) is None
    ):
        raise ValueError("SSE PCF must contain one valid fund code and trading day")
    return fund_codes[0], trading_days[0]


class WaybackSSEPCFArchiveComputer(Computer):
    """Verify archived SSE PCF bytes and their requested fund/date identity."""

    quality = Quality.MEASUREMENT

    def __init__(self, fetcher: Callable[[str], WaybackResponse] | None = None) -> None:
        self._fetcher = fetcher or _fetch_wayback

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID:
            raise ValueError("contract uses another Wayback SSE PCF archive adapter")
        if len(contract.inputs) != 1:
            raise ValueError("contract must bind exactly one Wayback SSE PCF target manifest")
        raw = safe_input(root, contract.inputs[0].path).read_bytes()
        try:
            manifest = _TargetFile.model_validate_json(raw)
        except ValueError as error:
            raise ValueError("Wayback SSE PCF target manifest is invalid") from error
        targets = manifest.targets
        if len({target.record_id for target in targets}) != len(targets):
            raise ValueError("Wayback SSE PCF manifest repeats a record ID")
        if len({target.url for target in targets}) != len(targets):
            raise ValueError("Wayback SSE PCF manifest repeats a download URL")

        cdx_rows = replay_count = digest_matches = identity_matches = witnesses = 0
        post_cutoff = cdx_200 = empty_cdx = cdx_bytes = 0
        cdx_failed = replay_failed = cdx_incomplete = identity_parse_failures = 0
        details: list[str] = []
        for target in targets:
            trading_day, cutoff = _target_identity(target)
            if (
                contract.boundary_date != trading_day.isoformat()
                or contract.cutoff_date != cutoff.date().isoformat()
            ):
                raise ValueError("contract dates do not match the pinned SSE PCF target")
            cdx_request = _cdx_url(target, cutoff)
            try:
                cdx_response = self._fetcher(cdx_request)
            except (OSError, TimeoutError, ValueError) as error:
                cdx_failed += 1
                details.append(f"{target.record_id}:cdx=unavailable,error={type(error).__name__}")
                continue
            if cdx_response.status != 200 or cdx_response.final_url != cdx_request:
                cdx_failed += 1
                details.append(
                    f"{target.record_id}:cdx=unavailable,status={cdx_response.status},"
                    f"redirected={int(cdx_response.final_url != cdx_request)}"
                )
                continue
            cdx_200 += 1
            cdx_bytes += len(cdx_response.body)
            cdx_hash = hashlib.sha256(cdx_response.body).hexdigest()
            try:
                rows = _parse_cdx(cdx_response.body)
            except ValueError:
                cdx_incomplete += 1
                details.append(
                    f"{target.record_id}:cdx=invalid_or_incomplete,bytes={len(cdx_response.body)},"
                    f"sha256={cdx_hash}"
                )
                continue
            cdx_rows += len(rows)
            empty_cdx += int(not rows)
            details.append(
                f"{target.record_id}:cdx_status=200,cdx_bytes={len(cdx_response.body)},"
                f"cdx_sha256={cdx_hash},cdx_rows={len(rows)}"
            )

            eligible: list[tuple[str, str, str]] = []
            for timestamp, original, mimetype, status, digest, length in rows:
                if (
                    original != target.url
                    or status != "200"
                    or mimetype.lower() in {"text/html", "application/xhtml+xml"}
                    or re.fullmatch(r"\d{14}", timestamp) is None
                    or _BASE32_SHA1.fullmatch(digest) is None
                    or not length.isdigit()
                ):
                    cdx_incomplete += 1
                    continue
                try:
                    captured_at = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                except ValueError:
                    cdx_incomplete += 1
                    continue
                if captured_at > cutoff:
                    post_cutoff += 1
                    continue
                eligible.append((timestamp, digest, length))

            for timestamp, expected_digest, expected_length in sorted(eligible, reverse=True)[
                :_REPLAY_CANDIDATE_LIMIT
            ]:
                replay_url = f"https://web.archive.org/web/{timestamp}id_/{target.url}"
                try:
                    replay = self._fetcher(replay_url)
                except (OSError, TimeoutError, ValueError) as error:
                    replay_failed += 1
                    details.append(
                        f"{target.record_id}:replay={timestamp},error={type(error).__name__}"
                    )
                    continue
                replay_count += 1
                matched_digest = (
                    replay.status == 200
                    and replay.final_url == replay_url
                    and str(len(replay.body)) == expected_length
                    and _sha1_base32(replay.body) == expected_digest
                )
                digest_matches += int(matched_digest)
                if not matched_digest:
                    details.append(
                        f"{target.record_id}:replay={timestamp},status={replay.status},"
                        f"length_match={int(str(len(replay.body)) == expected_length)},sha1_match=0"
                    )
                    continue
                if _content_type(replay.headers) in {"text/html", "application/xhtml+xml"}:
                    identity_parse_failures += 1
                    details.append(f"{target.record_id}:replay={timestamp},identity_parse=html")
                    continue
                try:
                    observed_code, observed_day = _parse_pcf_identity(replay.body)
                except ValueError as error:
                    identity_parse_failures += 1
                    details.append(
                        f"{target.record_id}:replay={timestamp},"
                        f"identity_parse=invalid,error={type(error).__name__}"
                    )
                    continue
                identity_matches_target = (
                    observed_code == target.fund_code and observed_day == target.trading_day
                )
                identity_matches += int(identity_matches_target)
                if identity_matches_target:
                    witnesses += 1
                    details.append(
                        f"{target.record_id}:verified_capture={timestamp},sha1={expected_digest},"
                        f"length={expected_length},fund_code={observed_code},trading_day={observed_day}"
                    )
                    break
                details.append(
                    f"{target.record_id}:replay={timestamp},identity_match=0,"
                    f"observed_code={observed_code},observed_day={observed_day}"
                )

        values = {
            "exact_cdx_rows": cdx_rows,
            "pcf_replay_attempts": replay_count,
            "pcf_sha1_matches_cdx": digest_matches,
            "pcf_identity_matches": identity_matches,
            "verified_pre_cutoff_pcf_captures": witnesses,
            "post_cutoff_rows_ignored": post_cutoff,
            "cdx_http_200_responses": cdx_200,
            "empty_cdx_responses": empty_cdx,
            "cdx_response_bytes": cdx_bytes,
            "cdx_requests_failed": cdx_failed,
            "replay_requests_failed": replay_failed,
            "cdx_queries_incomplete": cdx_incomplete,
            "identity_parse_failures": identity_parse_failures,
        }
        return Measured(
            metrics=tuple(Metric(name=name, value=float(value)) for name, value in values.items()),
            samples=len(targets),
            details=tuple(details),
        )
