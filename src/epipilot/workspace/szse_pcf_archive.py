"""Bounded read-only retrieval of historical SZSE ETF PCF files."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric

SZSE_PCF_ARCHIVE_ADAPTER_ID = "szse_pcf_archive_v1"
_API_URL = "https://www.szse.cn/api/report/ShowReport/data"
_PCF_HOST = "reportdocs.static.szse.cn"
_PCF_PATH = re.compile(r"^/files/text/etf/ETF(?P<code>159\d{3})(?P<date>\d{8})\.txt$")
_MAX_INPUT_BYTES = 1_000_000
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_TARGETS = 4
_TIMEOUT_SECONDS = 4.0
_PREOPEN_CUTOFF_UTC = time(1, 15)
_LIMITATION = "first-publication time is not established"


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HTTPGetPort(Protocol):
    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HTTPResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


class UrllibHTTPGet:
    """Issue anonymous allowlisted GET requests without redirect or cookie handling."""

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HTTPResponse:
        if not url.startswith((_API_URL + "?", f"https://{_PCF_HOST}/files/text/etf/")):
            raise ValueError("SZSE PCF request URL is outside the fixed source allowlist")
        request = Request(
            url,
            headers={
                "User-Agent": "EpiPilot-research-source-check/1.0",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.szse.cn/www/disclosure/fund/currency/index.html",
            },
        )
        opener = build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(max_bytes + 1)
                return HTTPResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=body,
                )
        except HTTPError as error:
            body = error.read(max_bytes + 1)
            return HTTPResponse(
                status=error.code,
                headers=dict(error.headers.items()),
                body=body,
            )


@dataclass(frozen=True)
class _Target:
    code: str
    trading_date: date


class _PCFLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paths: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        path = values.get("encode-open")
        if path is not None:
            self.paths.append(path)

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next((value for key, value in headers.items() if key.lower() == name.lower()), None)


def _read_targets(path: Path) -> tuple[_Target, ...]:
    if path.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError("SZSE target manifest exceeds the byte limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("SZSE target manifest is not valid UTF-8 JSON") from None
    if not isinstance(value, dict) or set(value) != {"schema", "targets"}:
        raise ValueError("SZSE target manifest fields differ from the registered schema")
    if value["schema"] != "szse-pcf-targets/v1":
        raise ValueError("unsupported SZSE PCF target manifest")
    raw_targets = value["targets"]
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= _MAX_TARGETS:
        raise ValueError("SZSE target manifest must contain one to four targets")
    targets: list[_Target] = []
    for item in raw_targets:
        if not isinstance(item, dict) or set(item) != {"code", "date"}:
            raise ValueError("SZSE target fields must be exactly code and date")
        code, raw_date = item["code"], item["date"]
        if not isinstance(code, str) or re.fullmatch(r"159\d{3}", code) is None:
            raise ValueError("SZSE target must use a six-digit ETF code")
        if not isinstance(raw_date, str):
            raise ValueError("SZSE target date must be an ISO date")
        try:
            trading_date = date.fromisoformat(raw_date)
        except ValueError:
            raise ValueError("SZSE target date must be an ISO date") from None
        targets.append(_Target(code=code, trading_date=trading_date))
    if len({(target.code, target.trading_date) for target in targets}) != len(targets):
        raise ValueError("SZSE target manifest contains duplicate code/date pairs")
    return tuple(targets)


def _metric(name: str, value: int | float) -> Metric:
    return Metric(name=name, value=float(value))


class SZSEPCFArchiveComputer(Computer):
    """Measure exact-date PCF retrieval and report server metadata without PIT promotion."""

    quality = Quality.MEASUREMENT

    def __init__(self, transport: HTTPGetPort | None = None) -> None:
        self.transport = transport or UrllibHTTPGet()

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != SZSE_PCF_ARCHIVE_ADAPTER_ID or len(contract.inputs) != 1:
            raise ValueError("SZSE PCF retrieval requires its adapter and one target manifest")
        targets = _read_targets(safe_input(root, contract.inputs[0].path))
        counts = {
            "query_status_200": 0,
            "listing_match_count": 0,
            "file_identity_match_count": 0,
            "last_modified_present_count": 0,
            "server_last_modified_before_open_count": 0,
            "identity_and_preopen_count": 0,
        }
        details = [f"target_count={len(targets)}"]

        for target in targets:
            day = target.trading_date.isoformat()
            query = urlencode(
                {
                    "SHOWTYPE": "JSON",
                    "CATALOGID": "sgshqd",
                    "TABKEY": "tab1",
                    "PAGENO": "1",
                    "txtJCorDH": target.code,
                    "txtStart": day,
                    "txtEnd": day,
                }
            )
            query_url = f"{_API_URL}?{query}"
            query_response = self.transport.get(
                query_url, timeout_seconds=_TIMEOUT_SECONDS, max_bytes=_MAX_RESPONSE_BYTES
            )
            query_body = query_response.body
            if len(query_body) > _MAX_RESPONSE_BYTES:
                raise ValueError("SZSE PCF query response exceeds the byte limit")
            details.append(
                f"query={target.code}:{day}:status={query_response.status}:"
                f"sha256={hashlib.sha256(query_body).hexdigest()}"
            )
            if query_response.status != 200:
                continue
            counts["query_status_200"] += 1
            content_type = _header(query_response.headers, "Content-Type") or ""
            if "application/json" not in content_type.lower():
                raise ValueError("SZSE PCF listing did not return JSON")
            try:
                payload = json.loads(query_body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("SZSE PCF listing returned malformed JSON") from None
            if (
                not isinstance(payload, list)
                or len(payload) != 1
                or not isinstance(payload[0], dict)
            ):
                raise ValueError(
                    "SZSE PCF listing response shape differs from the registered route"
                )
            listing = payload[0]
            metadata, rows = listing.get("metadata"), listing.get("data")
            if (
                not isinstance(metadata, dict)
                or metadata.get("catalogid") != "sgshqd"
                or metadata.get("pageno") != 1
                or not isinstance(rows, list)
            ):
                raise ValueError("SZSE PCF listing metadata does not match the exact query")
            record_count = metadata.get("recordcount")
            page_count = metadata.get("pagecount")
            if (
                isinstance(record_count, bool)
                or not isinstance(record_count, int)
                or record_count != len(rows)
                or isinstance(page_count, bool)
                or not isinstance(page_count, int)
                or page_count not in (0, 1)
            ):
                raise ValueError("SZSE PCF listing pagination counts are inconsistent")
            if listing.get("error") not in (None, ""):
                details.append(f"listing_error={str(listing['error'])[:120]}")
                continue

            matching_paths: list[str] = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("jjdm"), str):
                    raise ValueError("SZSE PCF listing row has no display field")
                parser = _PCFLinkParser()
                parser.feed(row["jjdm"])
                display = "".join(parser.text_parts)
                for path_value in parser.paths:
                    match = _PCF_PATH.fullmatch(path_value)
                    if (
                        match is not None
                        and match.group("code") == target.code
                        and match.group("date") == target.trading_date.strftime("%Y%m%d")
                        and target.code in display
                        and day in display
                    ):
                        matching_paths.append(path_value)
            if len(matching_paths) > 1:
                raise ValueError("SZSE PCF listing contains duplicate exact-date files")
            if not matching_paths:
                details.append(f"file={target.code}:{day}:listing_match=0")
                continue
            counts["listing_match_count"] += 1

            file_url = f"https://{_PCF_HOST}{matching_paths[0]}"
            file_response = self.transport.get(
                file_url, timeout_seconds=_TIMEOUT_SECONDS, max_bytes=_MAX_RESPONSE_BYTES
            )
            raw_body = file_response.body
            if len(raw_body) > _MAX_RESPONSE_BYTES:
                raise ValueError("SZSE PCF raw file exceeds the byte limit")
            file_headers = file_response.headers
            last_modified = _header(file_headers, "Last-Modified")
            etag = _header(file_headers, "ETag") or "missing"
            body_sha = hashlib.sha256(raw_body).hexdigest()
            identity_match = False
            if (
                file_response.status == 200
                and "text/plain" in (_header(file_headers, "Content-Type") or "").lower()
            ):
                try:
                    text = raw_body.decode("gbk")
                except UnicodeDecodeError:
                    text = ""
                identity_match = target.code in text and day in text
            if identity_match:
                counts["file_identity_match_count"] += 1
            last_modified_before_open = False
            if last_modified and identity_match:
                try:
                    timestamp = parsedate_to_datetime(last_modified)
                except (TypeError, ValueError, OverflowError):
                    timestamp = None
                if timestamp is not None and timestamp.tzinfo is not None:
                    counts["last_modified_present_count"] += 1
                    cutoff = datetime.combine(target.trading_date, _PREOPEN_CUTOFF_UTC, tzinfo=UTC)
                    last_modified_before_open = timestamp.astimezone(UTC) < cutoff
                    if last_modified_before_open:
                        counts["server_last_modified_before_open_count"] += 1
                        if identity_match:
                            counts["identity_and_preopen_count"] += 1
            details.append(
                f"file={target.code}:{day}:status={file_response.status}:"
                f"identity_match={int(identity_match)}:body_sha256={body_sha}:"
                f"last_modified={last_modified or 'missing'}:"
                f"etag={etag}:"
                f"server_last_modified_before_open={int(last_modified_before_open)}"
            )

        metrics = (
            _metric("target_count", len(targets)),
            _metric("query_status_200", counts["query_status_200"]),
            _metric("listing_match_count", counts["listing_match_count"]),
            _metric("file_identity_match_count", counts["file_identity_match_count"]),
            _metric("last_modified_present_count", counts["last_modified_present_count"]),
            _metric(
                "server_last_modified_before_open_count",
                counts["server_last_modified_before_open_count"],
            ),
            _metric("identity_and_preopen_count", counts["identity_and_preopen_count"]),
        )
        details.extend(
            (
                f"adapter_sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}",
                "Scope is current retrieval and server-reported metadata only; " + _LIMITATION,
            )
        )
        return Measured(metrics=metrics, samples=len(targets), details=tuple(details))


def szse_pcf_archive_computers() -> dict[str, Computer]:
    return {SZSE_PCF_ARCHIVE_ADAPTER_ID: SZSEPCFArchiveComputer()}
