"""Verify exact Wayback captures of official SSE index-notice PDF attachments."""

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

from pydantic import BaseModel, ConfigDict, Field

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric

ADAPTER_ID = "wayback_sse_pdf_attachment_v1"
_SCHEMA = "wayback-sse-pdf-attachment-targets/v1"
_CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
_NOTICE_PATH = re.compile(r"^/market/sseindex/diclosure/c/c_\d{8}_(?P<id>\d+)\.shtml$")
_ATTACHMENT_PATH = re.compile(r"^/market/sseindex/diclosure/c/(?P<id>\d+)/files/[a-f0-9]{32}\.pdf$")
_MAX_TARGETS = 20
_REQUEST_TIMEOUT_SECONDS = 4
_MAX_CDX_BYTES = 2 * 1024 * 1024
_MAX_PDF_BYTES = 25 * 1024 * 1024
_CDX_ROW_LIMIT = 1000
_REPLAY_CANDIDATE_LIMIT = 2


class _Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    notice_url: str = Field(min_length=1)
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
    request = Request(url, headers={"User-Agent": "EpiPilot public archive verification/1.0"})
    opener = build_opener(_NoRedirect())
    limit = _MAX_CDX_BYTES if urlparse(url).path == "/cdx/search/cdx" else _MAX_PDF_BYTES
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


def _sse_url(value: str, path: re.Pattern[str]) -> re.Match[str] | None:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.sse.com.cn"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return path.fullmatch(parsed.path)


def _cutoff(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("PDF archive cutoff must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("PDF archive cutoff must include a timezone")
    parsed = parsed.astimezone(UTC)
    if parsed > datetime.now(UTC):
        raise ValueError("PDF archive cutoff cannot be in the future")
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
        ("filter", "mimetype:application/pdf"),
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


class WaybackSsePDFAttachmentComputer(Computer):
    """Verify a pinned official SSE PDF URL against exact CDX replay bytes."""

    quality = Quality.MEASUREMENT

    def __init__(self, fetcher: Callable[[str], WaybackResponse] | None = None) -> None:
        self._fetcher = fetcher or _fetch_wayback

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID:
            raise ValueError("contract uses another Wayback SSE PDF attachment adapter")
        if len(contract.inputs) != 1:
            raise ValueError("contract must bind exactly one Wayback SSE PDF target manifest")
        raw = safe_input(root, contract.inputs[0].path).read_bytes()
        try:
            manifest = _TargetFile.model_validate_json(raw)
        except ValueError as error:
            raise ValueError("Wayback SSE PDF target manifest is invalid") from error
        targets = manifest.targets
        if len({target.record_id for target in targets}) != len(targets):
            raise ValueError("Wayback SSE PDF manifest repeats a record ID")
        if len({target.url for target in targets}) != len(targets):
            raise ValueError("Wayback SSE PDF manifest repeats an attachment URL")

        exact_rows = replay_count = digest_matches = signature_matches = witnesses = 0
        post_cutoff = cdx_200 = empty_cdx = cdx_bytes = 0
        cdx_failed = replay_failed = cdx_incomplete = 0
        details: list[str] = []
        for target in targets:
            page_match = _sse_url(target.notice_url, _NOTICE_PATH)
            attachment_match = _sse_url(target.url, _ATTACHMENT_PATH)
            if (
                page_match is None
                or attachment_match is None
                or page_match.group("id") != attachment_match.group("id")
            ):
                raise ValueError("target is not an allowlisted SSE PDF attachment URL")
            try:
                page_date = date.fromisoformat(target.page_date)
                effective_date = date.fromisoformat(target.effective_date)
            except ValueError as error:
                raise ValueError("SSE notice dates must be valid calendar dates") from error
            cutoff = _cutoff(target.cutoff_utc)
            if (
                target.notice_url.rstrip("/").split("/")[-1]
                != f"c_{page_date.strftime('%Y%m%d')}_{page_match.group('id')}.shtml"
                or page_date >= cutoff.date()
                or effective_date <= page_date
                or contract.boundary_date != page_date.isoformat()
                or contract.cutoff_date != cutoff.date().isoformat()
            ):
                raise ValueError("contract dates do not match the pinned SSE notice target")

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
            exact_rows += len(rows)
            empty_cdx += int(not rows)
            details.append(
                f"{target.record_id}:cdx_status=200,cdx_bytes={len(cdx_response.body)},"
                f"cdx_sha256={cdx_hash},cdx_rows={len(rows)}"
            )

            eligible: list[tuple[str, str, str]] = []
            for timestamp, original, mimetype, status, digest, length in rows:
                if (
                    original != target.url
                    or mimetype.lower() != "application/pdf"
                    or status != "200"
                    or not re.fullmatch(r"\d{14}", timestamp)
                    or not re.fullmatch(r"[A-Z2-7]{32}", digest)
                    or not length.isdigit()
                ):
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
                valid_pdf = (
                    _content_type(replay.headers) == "application/pdf"
                    and replay.body.startswith(b"%PDF-")
                    and b"%%EOF" in replay.body[-1024:]
                )
                digest_matches += int(matched_digest)
                signature_matches += int(valid_pdf)
                if matched_digest and valid_pdf:
                    witnesses += 1
                    details.append(
                        f"{target.record_id}:verified_capture={timestamp},"
                        f"sha1={expected_digest},length={expected_length},pdf_signature=1"
                    )
                    break
                details.append(
                    f"{target.record_id}:replay={timestamp},status={replay.status},"
                    f"length_match={int(str(len(replay.body)) == expected_length)},"
                    f"sha1_match={int(matched_digest)},pdf_signature={int(valid_pdf)}"
                )
        values = {
            "exact_cdx_rows": exact_rows,
            "pdf_replay_attempts": replay_count,
            "pdf_sha1_matches_cdx": digest_matches,
            "pdf_signature_valid": signature_matches,
            "verified_pre_cutoff_pdf_captures": witnesses,
            "post_cutoff_rows_ignored": post_cutoff,
            "cdx_http_200_responses": cdx_200,
            "empty_cdx_responses": empty_cdx,
            "cdx_response_bytes": cdx_bytes,
            "cdx_requests_failed": cdx_failed,
            "replay_requests_failed": replay_failed,
            "cdx_queries_incomplete": cdx_incomplete,
        }
        return Measured(
            metrics=tuple(Metric(name=name, value=float(value)) for name, value in values.items()),
            samples=len(targets),
            details=tuple(details),
        )
