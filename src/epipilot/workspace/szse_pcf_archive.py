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
SZSE_PCF_CATALOG_ADAPTER_ID = "szse_pcf_catalog_v1"
SZSE_PCF_CATALOG_ADAPTER_V2_ID = "szse_pcf_catalog_v2"
_API_URL = "https://www.szse.cn/api/report/ShowReport/data"
_PCF_HOST = "reportdocs.static.szse.cn"
_PCF_PATH = re.compile(r"^/files/text/etf/ETF(?P<code>159\d{3})(?P<date>\d{8})\.txt$")
_MAX_INPUT_BYTES = 1_000_000
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_TARGETS = 4
_MAX_CATALOG_PAGES = 12
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


def _read_catalog_date(path: Path, *, schema: str) -> date:
    if path.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError("SZSE PCF catalog manifest exceeds the byte limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("SZSE PCF catalog manifest is not valid UTF-8 JSON") from None
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "date"}
        or value.get("schema") != schema
        or not isinstance(value.get("date"), str)
    ):
        raise ValueError("SZSE PCF catalog manifest fields differ from the registered schema")
    try:
        return date.fromisoformat(value["date"])
    except ValueError:
        raise ValueError("SZSE PCF catalog date must be an ISO date") from None


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


class SZSEPCFCatalogComputer(Computer):
    """Read a bounded, date-specific current SZSE catalog without PIT promotion."""

    quality = Quality.MEASUREMENT
    adapter_id = SZSE_PCF_CATALOG_ADAPTER_ID
    manifest_schema = "szse-pcf-catalog-target/v1"
    record_count_is_total = False

    def __init__(self, transport: HTTPGetPort | None = None) -> None:
        self.transport = transport or UrllibHTTPGet()

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != self.adapter_id or len(contract.inputs) != 1:
            raise ValueError("SZSE PCF catalog requires its adapter and one date manifest")
        catalog_date = _read_catalog_date(
            safe_input(root, contract.inputs[0].path), schema=self.manifest_schema
        )
        day = catalog_date.isoformat()
        expected_page_count: int | None = None
        expected_record_count: int | None = None
        expected_page_size: int | None = None
        codes: set[str] = set()
        rows_received = 0
        identity_match_count = 0
        invalid_row_count = 0
        pages_received = 0
        details = [f"catalog_date={day}", f"max_pages={_MAX_CATALOG_PAGES}"]
        failure: str | None = None

        for page_number in range(1, _MAX_CATALOG_PAGES + 1):
            query = urlencode(
                {
                    "SHOWTYPE": "JSON",
                    "CATALOGID": "sgshqd",
                    "TABKEY": "tab1",
                    "PAGENO": str(page_number),
                    "txtStart": day,
                    "txtEnd": day,
                }
            )
            try:
                response = self.transport.get(
                    f"{_API_URL}?{query}",
                    timeout_seconds=_TIMEOUT_SECONDS,
                    max_bytes=_MAX_RESPONSE_BYTES,
                )
            except (TimeoutError, OSError) as error:
                failure = "timeout" if isinstance(error, TimeoutError) else "network_error"
                details.append(f"page={page_number}:{failure}")
                break
            if len(response.body) > _MAX_RESPONSE_BYTES:
                raise ValueError("SZSE PCF catalog response exceeds the byte limit")
            details.append(
                f"page={page_number}:status={response.status}:"
                f"sha256={hashlib.sha256(response.body).hexdigest()}"
            )
            if response.status != 200:
                failure = f"http_{response.status}"
                break
            content_type = _header(response.headers, "Content-Type") or ""
            if "application/json" not in content_type.lower():
                failure = "unexpected_content_type"
                break
            try:
                payload = json.loads(response.body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                failure = "malformed_json"
                break
            if (
                not isinstance(payload, list)
                or len(payload) != 1
                or not isinstance(payload[0], dict)
            ):
                failure = "unexpected_response_shape"
                break
            listing = payload[0]
            metadata, rows = listing.get("metadata"), listing.get("data")
            if (
                not isinstance(metadata, dict)
                or metadata.get("catalogid") != "sgshqd"
                or metadata.get("pageno") != page_number
                or not isinstance(rows, list)
            ):
                failure = "invalid_pagination_metadata"
                break
            record_count = metadata.get("recordcount")
            page_count = metadata.get("pagecount")
            if (
                isinstance(record_count, bool)
                or not isinstance(record_count, int)
                or record_count < 0
                or isinstance(page_count, bool)
                or not isinstance(page_count, int)
                or page_count < 0
                or listing.get("error") not in (None, "")
            ):
                failure = "inconsistent_pagination_metadata"
                break
            page_size = metadata.get("pagesize")
            if self.record_count_is_total:
                if (
                    isinstance(page_size, bool)
                    or not isinstance(page_size, int)
                    or page_size <= 0
                    or len(rows) > page_size
                ):
                    failure = "invalid_page_size"
                    break
                if expected_record_count is None:
                    expected_record_count = record_count
                    expected_page_size = page_size
                elif expected_record_count != record_count or expected_page_size != page_size:
                    failure = "record_count_or_page_size_changed"
                    break
                calculated_pages = (record_count + page_size - 1) // page_size
                if page_count != calculated_pages:
                    failure = "page_count_inconsistent_with_total"
                    break
            elif record_count != len(rows):
                failure = "page_record_count_mismatch"
                break
            if expected_page_count is None:
                expected_page_count = page_count
            elif expected_page_count != page_count:
                failure = "page_count_changed"
                break
            pages_received += 1
            rows_received += len(rows)
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("jjdm"), str):
                    invalid_row_count += 1
                    continue
                parser = _PCFLinkParser()
                parser.feed(row["jjdm"])
                display = "".join(parser.text_parts)
                matches = [
                    match
                    for path_value in parser.paths
                    if (match := _PCF_PATH.fullmatch(path_value)) is not None
                    and match.group("date") == catalog_date.strftime("%Y%m%d")
                    and match.group("code") in display
                    and day in display
                ]
                if len(matches) != 1:
                    invalid_row_count += 1
                    continue
                code = matches[0].group("code")
                if code in codes:
                    invalid_row_count += 1
                    continue
                codes.add(code)
                identity_match_count += 1
            if page_count == 0 or page_number >= page_count:
                break

        complete = (
            expected_page_count is not None
            and expected_page_count <= _MAX_CATALOG_PAGES
            and pages_received == expected_page_count
            and identity_match_count == rows_received
            and invalid_row_count == 0
            and failure is None
        )
        if self.record_count_is_total:
            complete = complete and expected_record_count == rows_received
        if expected_page_count is not None and expected_page_count > _MAX_CATALOG_PAGES:
            details.append("page_limit_reached=1")
        if failure is not None:
            details.append(f"incomplete_reason={failure}")
        details.append("codes=" + ",".join(sorted(codes)))
        details.extend(
            (
                f"adapter_sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}",
                "This is the current SZSE historical catalog response; it does not establish "
                "contemporaneous publication or PIT universe membership.",
            )
        )
        metrics = (
            _metric("record_count", expected_record_count or rows_received),
            _metric("page_size", expected_page_size or 0),
            _metric("page_count", expected_page_count or 0),
            _metric("pages_received", pages_received),
            _metric("rows_received", rows_received),
            _metric("identity_match_count", identity_match_count),
            _metric("invalid_row_count", invalid_row_count),
            _metric("unique_code_count", len(codes)),
            _metric("complete_catalog", int(complete)),
        )
        return Measured(metrics=metrics, samples=rows_received, details=tuple(details))


class SZSEPCFCatalogComputerV2(SZSEPCFCatalogComputer):
    """Versioned catalog reader for SZSE's total-record pagination metadata."""

    adapter_id = SZSE_PCF_CATALOG_ADAPTER_V2_ID
    manifest_schema = "szse-pcf-catalog-target/v2"
    record_count_is_total = True


def szse_pcf_catalog_computers() -> dict[str, Computer]:
    return {
        SZSE_PCF_CATALOG_ADAPTER_ID: SZSEPCFCatalogComputer(),
        SZSE_PCF_CATALOG_ADAPTER_V2_ID: SZSEPCFCatalogComputerV2(),
    }
