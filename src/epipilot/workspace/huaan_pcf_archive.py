"""Measure retrieval of fixed historical PCF XML files from Huaan's issuer archive."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric

ADAPTER_ID = "huaan_pcf_archive_v1"
_SCHEMA = "huaan-pcf-targets/v1"
_HOST = "www.huaan.com.cn"
_PAGE_PATH = "/etf/159949/sgshqd.jsp"
_DOWNLOAD_PATH = "/etf/159949/etffiledownload.jsp"
_MAX_INPUT_BYTES = 1_000_000
_MAX_PAGE_BYTES = 1_000_000
_MAX_XML_BYTES = 5_000_000
_TIMEOUT_SECONDS = 4.0
_PINNED_TARGETS = frozenset({("159949", "2023-01-03"), ("159949", "2026-10-16")})
_FILENAME = re.compile(r"^pcf_159949_(?P<date>\d{8})\.xml$")


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes


class HTTPGetPort(Protocol):
    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HTTPResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def _allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != _HOST or parsed.port is not None:
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path == _PAGE_PATH:
        return (
            set(query) == {"querydate"}
            and len(query["querydate"]) == 1
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", query["querydate"][0]) is not None
        )
    if parsed.path == _DOWNLOAD_PATH:
        return (
            set(query) == {"etffilename"}
            and len(query["etffilename"]) == 1
            and _FILENAME.fullmatch(query["etffilename"][0]) is not None
        )
    return False


class UrllibHTTPGet:
    """Issue anonymous HTTPS GETs to the two pinned Huaan archive paths."""

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HTTPResponse:
        if not _allowed_url(url):
            raise ValueError("Huaan PCF request URL is outside the fixed source allowlist")
        request = Request(
            url,
            headers={
                "User-Agent": "EpiPilot research PCF retrieval/1.0",
                "Accept": "text/html, application/xml, text/xml, */*",
            },
            method="GET",
        )
        opener = build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(max_bytes + 1)
                return HTTPResponse(
                    status=response.status,
                    final_url=response.geturl(),
                    headers={
                        str(key): str(value).strip() for key, value in response.headers.items()
                    },
                    body=body,
                )
        except HTTPError as error:
            body = error.read(max_bytes + 1)
            return HTTPResponse(
                status=error.code,
                final_url=error.geturl(),
                headers={str(key): str(value).strip() for key, value in error.headers.items()},
                body=body,
            )


@dataclass(frozen=True)
class _Target:
    code: str
    trading_date: str

    @property
    def compact_date(self) -> str:
        return self.trading_date.replace("-", "")


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href is not None:
                self.hrefs.append(href)

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next((value for key, value in headers.items() if key.lower() == name.lower()), None)


def _read_targets(path: Path) -> tuple[_Target, ...]:
    if path.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError("Huaan target manifest exceeds the byte limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Huaan target manifest is not valid UTF-8 JSON") from None
    if not isinstance(value, dict) or set(value) != {"schema", "targets"}:
        raise ValueError("Huaan target manifest fields differ from the registered schema")
    if value["schema"] != _SCHEMA:
        raise ValueError("unsupported Huaan PCF target manifest")
    raw_targets = value["targets"]
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= len(_PINNED_TARGETS):
        raise ValueError("Huaan manifest must contain one or two fixed targets")
    targets: list[_Target] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_targets:
        if not isinstance(item, dict) or set(item) != {"code", "date"}:
            raise ValueError("Huaan target fields must be exactly code and date")
        code, day = item["code"], item["date"]
        if not isinstance(code, str) or not isinstance(day, str):
            raise ValueError("Huaan code and date must be strings")
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            raise ValueError("Huaan target date must be an ISO date") from None
        identity = (code, day)
        if parsed_day.isoformat() != day or identity not in _PINNED_TARGETS or identity in seen:
            raise ValueError("target is not one of the fixed Huaan PCF code/date pairs")
        seen.add(identity)
        targets.append(_Target(code=code, trading_date=day))
    return tuple(targets)


def _page_url(target: _Target) -> str:
    return f"https://{_HOST}{_PAGE_PATH}?{urlencode({'querydate': target.trading_date})}"


def _download_url(target: _Target) -> str:
    filename = f"pcf_{target.code}_{target.compact_date}.xml"
    return f"https://{_HOST}{_DOWNLOAD_PATH}?{urlencode({'etffilename': filename})}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_identity(body: bytes, target: _Target) -> tuple[bool, bool, int]:
    if len(body) > _MAX_XML_BYTES:
        raise ValueError("Huaan PCF XML exceeds the byte limit")
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return False, False, 0
    if _local_name(root.tag) != "PCFFile":
        return False, False, 0
    fields = {
        _local_name(child.tag): (child.text or "").strip() for child in root if len(child) == 0
    }
    identity_matches = (
        fields.get("SecurityID") == target.code and fields.get("TradingDay") == target.compact_date
    )
    components_nodes = [child for child in root if _local_name(child.tag) == "Components"]
    components = (
        [child for child in components_nodes[0] if _local_name(child.tag) == "Component"]
        if len(components_nodes) == 1
        else []
    )
    try:
        record_count = int(fields.get("RecordNum", ""))
        total_record_count = int(fields.get("TotalRecordNum", ""))
    except ValueError:
        return identity_matches, False, len(components)
    rows_valid = (
        bool(components)
        and record_count == total_record_count == len(components)
        and all(len(list(component)) > 0 for component in components)
    )
    return identity_matches, rows_valid, len(components)


class HuaanPCFArchiveComputer(Computer):
    """Verify exact historical file retrieval without treating retrieval as PIT evidence."""

    quality = Quality.MEASUREMENT

    def __init__(self, transport: HTTPGetPort | None = None) -> None:
        self.transport = transport or UrllibHTTPGet()

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID or len(contract.inputs) != 1:
            raise ValueError("Huaan PCF retrieval requires its adapter and one manifest")
        targets = _read_targets(safe_input(root, contract.inputs[0].path))
        counts = {
            "page_http_200": 0,
            "page_identity_match_count": 0,
            "page_link_match_count": 0,
            "xml_http_200": 0,
            "xml_identity_match_count": 0,
            "component_rows_valid_count": 0,
            "component_rows_total": 0,
            "last_modified_present_count": 0,
        }
        details = [f"target_count={len(targets)}"]
        with Path(__file__).open("rb") as code_file:
            adapter_digest = hashlib.file_digest(code_file, "sha256").hexdigest()

        for target in targets:
            page_url = _page_url(target)
            page = self.transport.get(
                page_url, timeout_seconds=_TIMEOUT_SECONDS, max_bytes=_MAX_PAGE_BYTES
            )
            if page.final_url != page_url:
                raise ValueError("Huaan page response was redirected")
            counts["last_modified_present_count"] += int(
                _header(page.headers, "Last-Modified") is not None
            )
            page_identity = False
            link_match = False
            if page.status == 200:
                counts["page_http_200"] += 1
                if len(page.body) > _MAX_PAGE_BYTES:
                    raise ValueError("Huaan historical page exceeds the byte limit")
                try:
                    html = page.body.decode("gb18030")
                except UnicodeDecodeError:
                    raise ValueError("Huaan historical page is not decodable GB18030") from None
                parser = _PageParser()
                parser.feed(html)
                compact_text = re.sub(r"\s+", "", "".join(parser.text_parts))
                page_identity = target.code in compact_text and target.compact_date in compact_text
                expected_link = _download_url(target)
                matching_links = [urljoin(page_url, href) for href in parser.hrefs]
                link_match = expected_link in matching_links
                counts["page_identity_match_count"] += int(page_identity)
                counts["page_link_match_count"] += int(page_identity and link_match)
            if not page_identity or not link_match:
                details.append(
                    f"{target.code}-{target.compact_date}:page_status={page.status},"
                    f"page_identity={int(page_identity)},link_match={int(link_match)},xml=not_fetched"
                )
                continue

            xml_url = _download_url(target)
            xml = self.transport.get(
                xml_url, timeout_seconds=_TIMEOUT_SECONDS, max_bytes=_MAX_XML_BYTES
            )
            if xml.final_url != xml_url:
                raise ValueError("Huaan XML response was redirected")
            counts["last_modified_present_count"] += int(
                _header(xml.headers, "Last-Modified") is not None
            )
            xml_match = False
            rows_valid = False
            component_count = 0
            digest = hashlib.sha256(xml.body).hexdigest()
            if xml.status == 200:
                counts["xml_http_200"] += 1
                xml_match, rows_valid, component_count = _xml_identity(xml.body, target)
                counts["xml_identity_match_count"] += int(xml_match)
                counts["component_rows_valid_count"] += int(rows_valid)
                counts["component_rows_total"] += component_count
            details.append(
                f"{target.code}-{target.compact_date}:page_status={page.status},"
                f"page_identity={int(page_identity)},link_match={int(link_match)},"
                f"xml_status={xml.status},xml_sha256={digest},xml_identity={int(xml_match)},"
                f"component_rows_valid={int(rows_valid)},component_count={component_count}"
            )

        details.extend(
            (
                f"adapter_sha256={adapter_digest}",
                "source-time limitation: this measures historical retrieval now and does not "
                "establish pre-open availability or first-publication time",
            )
        )
        return Measured(
            metrics=tuple(_metric(name, value) for name, value in counts.items()),
            samples=len(targets),
            details=tuple(details),
        )


def _metric(name: str, value: int | float) -> Metric:
    return Metric(name=name, value=float(value))
