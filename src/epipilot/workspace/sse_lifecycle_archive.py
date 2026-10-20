"""Verify reachability and byte stability of archived SSE ETF disclosures."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import IO, Literal
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric

ADAPTER_ID = "sse_lifecycle_archive_v1"
_SCHEMA = "sse-lifecycle-archive-capture/v1"
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_CAPTURE_MAX_AGE_SECONDS = 24 * 60 * 60
_FUND_ARCHIVE_PATH = re.compile(
    r"^/disclosure/fund/announcement/c/\d{4}-\d{2}-\d{2}/"
    r"\d{6}_\d{8}_[A-Za-z0-9_-]+\.pdf$"
)
_LISTING_ARCHIVE_PATH = re.compile(r"^/disclosure/announcement/listing/c/c_\d{8}_\d+\.shtml$")


class ArchiveRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    body_path: str = Field(min_length=1)
    status: Literal[200]
    content_type: Literal["application/pdf", "text/html"]
    server_date: str = Field(min_length=1)
    captured_at_utc: datetime
    byte_count: int = Field(gt=0, le=_MAX_RESPONSE_BYTES)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ArchiveCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: Literal["sse-lifecycle-archive-capture/v1"] = Field(alias="schema")
    records: tuple[ArchiveRecord, ...] = Field(min_length=1)


@dataclass(frozen=True)
class SSEArchiveResponse:
    status: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes


def _fetch_sse_archive(url: str) -> SSEArchiveResponse:
    """Perform one anonymous GET without following redirects or storing cookies."""

    request = Request(
        url,
        headers={"User-Agent": "EpiPilot public archive integrity check/1.0"},
        method="GET",
    )
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=8) as response:
            status = response.status
            final_url = response.geturl()
            headers = {str(k).lower(): str(v).strip() for k, v in response.headers.items()}
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        final_url = error.geturl()
        headers = {str(k).lower(): str(v).strip() for k, v in error.headers.items()}
        body = error.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ValueError("SSE archive response exceeds the registered byte limit")
    return SSEArchiveResponse(status=status, final_url=final_url, headers=headers, body=body)


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


def _is_allowlisted_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.sse.com.cn"
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and not parsed.query
        and not parsed.fragment
        and (
            _FUND_ARCHIVE_PATH.fullmatch(parsed.path) is not None
            or _LISTING_ARCHIVE_PATH.fullmatch(parsed.path) is not None
        )
    )


def _content_type(headers: Mapping[str, str]) -> str:
    value = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
    return value.split(";", maxsplit=1)[0].strip().lower()


def _valid_http_date(value: str) -> bool:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return parsed.tzinfo is not None


class SSELifecycleArchiveComputer(Computer):
    """Re-fetch exact SSE archive URLs and verify captured response identity."""

    quality = Quality.MEASUREMENT

    def __init__(self, fetcher: Callable[[str], SSEArchiveResponse] | None = None) -> None:
        self._fetcher = fetcher or _fetch_sse_archive

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID:
            raise ValueError("contract uses another SSE lifecycle archive adapter")
        input_bytes = {
            item.path: safe_input(root, item.path).read_bytes() for item in contract.inputs
        }
        plans: list[tuple[str, ArchiveCapture]] = []
        for path, raw in input_bytes.items():
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("schema") == _SCHEMA:
                plans.append((path, ArchiveCapture.model_validate(payload)))
        if len(plans) != 1:
            raise ValueError("contract must bind exactly one SSE archive capture manifest")
        plan_path, plan = plans[0]
        records = plan.records
        record_ids = [record.record_id for record in records]
        response_paths = [record.body_path for record in records]
        if len(set(record_ids)) != len(records) or len(set(response_paths)) != len(records):
            raise ValueError("SSE archive manifest repeats a record ID or body path")
        if set(input_bytes) != {plan_path, *response_paths}:
            raise ValueError("every SSE archive response must have exactly one manifest role")

        now = datetime.now(UTC)
        url_count = status_count = mime_count = digest_count = date_count = 0
        total_bytes = 0
        observations: list[str] = []
        for record in records:
            if not _is_allowlisted_url(record.url):
                raise ValueError("URL is not an allowlisted SSE archive URL")
            captured_at = record.captured_at_utc
            if captured_at.tzinfo is None or captured_at > now:
                raise ValueError("SSE archive capture time must be a non-future aware timestamp")
            if (now - captured_at).total_seconds() > _CAPTURE_MAX_AGE_SECONDS:
                raise ValueError("SSE archive capture is older than the permitted 24-hour window")
            if not _valid_http_date(record.server_date):
                raise ValueError("captured SSE archive response has an invalid HTTP Date")

            captured_body = input_bytes[record.body_path]
            captured_digest = hashlib.sha256(captured_body).hexdigest()
            if len(captured_body) != record.byte_count or captured_digest != record.sha256:
                raise ValueError("captured SSE archive bytes differ from the manifest digest")

            response = self._fetcher(record.url)
            if len(response.body) > _MAX_RESPONSE_BYTES:
                raise ValueError("live SSE archive response exceeds the registered byte limit")
            live_digest = hashlib.sha256(response.body).hexdigest()
            same_url = response.final_url == record.url
            successful = same_url and response.status == 200
            url_count += int(same_url)
            status_count += int(successful)
            mime_matches = successful and _content_type(response.headers) == record.content_type
            mime_count += int(mime_matches)
            date_valid = _valid_http_date(
                next((v for k, v in response.headers.items() if k.lower() == "date"), "")
            )
            date_count += int(date_valid)
            digest_matches = (
                successful
                and len(response.body) == record.byte_count
                and live_digest == record.sha256
            )
            digest_count += int(digest_matches)
            total_bytes += len(response.body)
            observations.append(
                f"{record.record_id}:status={response.status},url_match={int(same_url)},"
                f"mime_match={int(mime_matches)},date_valid={int(date_valid)},"
                f"body_match={int(digest_matches)}"
            )

        metrics = (
            Metric(name="registered_url_matches", value=float(url_count)),
            Metric(name="reachable_http_200", value=float(status_count)),
            Metric(name="content_type_matches", value=float(mime_count)),
            Metric(name="server_date_headers_valid", value=float(date_count)),
            Metric(name="body_sha256_matches", value=float(digest_count)),
            Metric(name="response_bytes", value=float(total_bytes)),
        )
        return Measured(
            metrics=metrics,
            samples=len(records),
            details=(
                "scope=integrity; exact SSE archive URL re-fetched over HTTPS; "
                "current body matched captured bytes; no inference about original release time, "
                "historical universe completeness or point-in-time qualification",
                *observations,
            ),
        )
