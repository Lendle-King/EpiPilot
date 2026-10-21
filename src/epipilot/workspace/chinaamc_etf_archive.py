"""Measure retrieval of a fixed ChinaAMC historical ETF archive item.

The issuer page labels this data supplementary and says it does not replace the
official PCF. Retrieval now is not evidence that the file was available at the
historical decision time.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric

ADAPTER_ID = "chinaamc_etf_archive_v1"
_SCHEMA = "chinaamc-etf-archive-targets/v1"
_HOST = "www.chinaamc.com"
_QUERY_URL = f"https://{_HOST}/front/front/out/etf/tradeList"
_DOWNLOAD_URL = f"https://{_HOST}/front/front/out/etf/query/etfDownload"
_MAX_INPUT_BYTES = 100_000
_MAX_QUERY_BYTES = 2_000_000
_MAX_FILE_BYTES = 5_000_000
_TIMEOUT_SECONDS = 8.0
_PINNED_TARGETS = frozenset({("510050", "2020-11-30")})
_FILENAME = re.compile(r"^fm001etfd(?P<day>\d{8})\d{3}\.txt$")


@dataclass(frozen=True)
class ChinaAMCResponse:
    status: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes


class FormFetcher(Protocol):
    def __call__(
        self,
        url: str,
        fields: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> ChinaAMCResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


class UrllibFormFetcher:
    """Send anonymous bounded form POSTs to the two fixed issuer endpoints."""

    def __call__(
        self,
        url: str,
        fields: Mapping[str, str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> ChinaAMCResponse:
        if url not in {_QUERY_URL, _DOWNLOAD_URL}:
            raise ValueError("ChinaAMC request is outside the fixed endpoint allowlist")
        request = Request(
            url,
            data=urlencode(dict(fields)).encode("ascii"),
            headers={
                "User-Agent": "EpiPilot research archive retrieval/1.0",
                "Accept": "application/json, application/octet-stream",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            method="POST",
        )
        opener = build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                body = response.read(max_bytes + 1)
                return ChinaAMCResponse(
                    status=response.status,
                    final_url=response.geturl(),
                    headers={
                        str(key): str(value).strip() for key, value in response.headers.items()
                    },
                    body=body,
                )
        except HTTPError as error:
            body = error.read(max_bytes + 1)
            return ChinaAMCResponse(
                status=error.code,
                final_url=error.geturl(),
                headers={str(key): str(value).strip() for key, value in error.headers.items()},
                body=body,
            )


@dataclass(frozen=True)
class _Target:
    fund_code: str
    query_date: str

    @property
    def compact_date(self) -> str:
        return self.query_date.replace("-", "")


def _read_target(path: Path) -> _Target:
    if path.stat().st_size > _MAX_INPUT_BYTES:
        raise ValueError("ChinaAMC target manifest exceeds the byte limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("ChinaAMC target manifest is not valid UTF-8 JSON") from None
    if not isinstance(value, dict) or set(value) != {"schema", "targets"}:
        raise ValueError("ChinaAMC target manifest fields differ from the registered schema")
    if value["schema"] != _SCHEMA:
        raise ValueError("unsupported ChinaAMC ETF archive target manifest")
    targets = value["targets"]
    if not isinstance(targets, list) or len(targets) != 1:
        raise ValueError("ChinaAMC manifest must contain exactly one pinned target")
    item = targets[0]
    if not isinstance(item, dict) or set(item) != {"fund_code", "query_date"}:
        raise ValueError("ChinaAMC target fields must be exactly fund_code and query_date")
    code, day = item["fund_code"], item["query_date"]
    if not isinstance(code, str) or not isinstance(day, str):
        raise ValueError("ChinaAMC fund code and query date must be strings")
    try:
        parsed_day = date.fromisoformat(day)
    except ValueError:
        raise ValueError("ChinaAMC query date must be an ISO date") from None
    if parsed_day.isoformat() != day or (code, day) not in _PINNED_TARGETS:
        raise ValueError("target is not the fixed ChinaAMC fund/date pair")
    return _Target(fund_code=code, query_date=day)


def _header(headers: Mapping[str, str], name: str) -> bool:
    return any(
        key.lower() == name.lower() and bool(value.strip()) for key, value in headers.items()
    )


def _extract_label(data: Mapping[str, object], label: str) -> str | None:
    entries = data.get("baseInfoContent")
    if not isinstance(entries, list):
        return None
    matches = [
        item.get("value")
        for item in entries
        if isinstance(item, dict) and item.get("label") == label
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        return None
    return matches[0].strip()


def _query_payload(body: bytes) -> Mapping[str, object] | None:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("ChinaAMC query response is not valid UTF-8 JSON") from None
    if not isinstance(value, dict):
        raise ValueError("ChinaAMC query response has an invalid shape")
    status = value.get("status")
    if status == 0:
        return None
    data = value.get("data")
    if status != 1 or not isinstance(data, dict):
        raise ValueError("ChinaAMC query response has an unsupported status or data shape")
    return data


def _component_counts(data: Mapping[str, object]) -> tuple[int, int, int]:
    rows = data.get("stockResponseList")
    if not isinstance(rows, list):
        return 0, 0, 0
    codes = [
        row.get("stockCode", "").strip()
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("stockCode", ""), str)
    ]
    return len(rows), len(set(codes)), len(codes) - len(set(codes))


def _query_file_matches(
    data: Mapping[str, object], target: _Target
) -> tuple[str | None, str | None, bool]:
    filename = data.get("fileName")
    year = data.get("year")
    if not isinstance(filename, str) or not isinstance(year, (str, int)):
        return None, None, False
    match = _FILENAME.fullmatch(filename)
    matches = (
        match is not None
        and match.group("day") == target.compact_date
        and str(year) == target.query_date[:4]
        and data.get("configType") == "txt"
    )
    return filename, str(year), matches


def _master_identity(text: str, target: _Target) -> tuple[bool, bool, bool]:
    master_match = re.search(
        r"ETFMaster\b[^>]*>(.*?)</ETFMaster\s*>", text, flags=re.IGNORECASE | re.DOTALL
    )
    constituent_present = (
        re.search(
            r"ETFConstituent\b[^>]*>.*?</ETFConstituent\s*>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        is not None
    )
    if master_match is None:
        return False, constituent_present, False
    rows = [
        [field.strip() for field in line.strip().split("|") if field.strip()]
        for line in master_match.group(1).splitlines()
    ]
    identity_match = any(
        row and row[0] == "01" and target.fund_code in row[1:] and target.compact_date in row[1:]
        for row in rows
    )
    return True, constituent_present, identity_match


class ChinaAMCEtfArchiveComputer(Computer):
    """Check current retrieval and internal identity of one fixed issuer archive file."""

    quality = Quality.MEASUREMENT

    def __init__(self, fetcher: FormFetcher | None = None) -> None:
        self.fetcher = fetcher or UrllibFormFetcher()

    def _fetch(self, url: str, fields: Mapping[str, str], max_bytes: int) -> ChinaAMCResponse:
        response = self.fetcher(url, fields, _TIMEOUT_SECONDS, max_bytes)
        if response.final_url != url:
            raise ValueError("ChinaAMC response was redirected")
        if len(response.body) > max_bytes:
            raise ValueError("ChinaAMC response exceeds the byte limit")
        return response

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID or len(contract.inputs) != 1:
            raise ValueError("ChinaAMC archive check requires its adapter and one manifest")
        target = _read_target(safe_input(root, contract.inputs[0].path))
        counts = {
            "query_http_200": 0,
            "query_success": 0,
            "query_code_matches": 0,
            "query_date_matches": 0,
            "query_filename_matches": 0,
            "query_component_rows": 0,
            "query_component_unique_rows": 0,
            "query_component_duplicate_rows": 0,
            "download_http_200": 0,
            "download_decode_success": 0,
            "etf_master_present": 0,
            "etf_constituent_present": 0,
            "download_format_sections_match": 0,
            "download_code_date_matches": 0,
            "verified_code_date_downloads": 0,
            "query_date_header_present": 0,
            "query_last_modified_present": 0,
            "query_etag_present": 0,
            "download_date_header_present": 0,
            "download_last_modified_present": 0,
            "download_etag_present": 0,
        }
        details = [f"target={target.fund_code}/{target.compact_date}"]
        query = self._fetch(
            _QUERY_URL,
            {"fundCode": target.fund_code, "queryDate": target.query_date, "instType": ""},
            _MAX_QUERY_BYTES,
        )
        counts["query_http_200"] = int(query.status == 200)
        counts["query_date_header_present"] = int(_header(query.headers, "Date"))
        counts["query_last_modified_present"] = int(_header(query.headers, "Last-Modified"))
        counts["query_etag_present"] = int(_header(query.headers, "ETag"))
        details.append(
            f"query_status={query.status},query_sha256={hashlib.sha256(query.body).hexdigest()}"
        )

        data: Mapping[str, object] | None = None
        if query.status == 200:
            data = _query_payload(query.body)
        if data is not None:
            counts["query_success"] = 1
            counts["query_code_matches"] = int(_extract_label(data, "基金代码") == target.fund_code)
            counts["query_date_matches"] = int(
                _extract_label(data, "最新公告日期") == target.query_date
            )
            rows, unique_rows, duplicate_rows = _component_counts(data)
            counts["query_component_rows"] = rows
            counts["query_component_unique_rows"] = unique_rows
            counts["query_component_duplicate_rows"] = duplicate_rows
            filename, year, filename_matches = _query_file_matches(data, target)
            counts["query_filename_matches"] = int(filename_matches)
            details.append(
                f"query_identity_code={counts['query_code_matches']},"
                f"query_identity_date={counts['query_date_matches']},"
                f"query_rows={rows},unique_rows={unique_rows},filename_match={int(filename_matches)}"
            )
            if (
                counts["query_code_matches"]
                and counts["query_date_matches"]
                and filename_matches
                and filename is not None
                and year is not None
            ):
                download = self._fetch(
                    _DOWNLOAD_URL,
                    {"fileName": filename, "year": year, "fundCode": target.fund_code},
                    _MAX_FILE_BYTES,
                )
                counts["download_http_200"] = int(download.status == 200)
                counts["download_date_header_present"] = int(_header(download.headers, "Date"))
                counts["download_last_modified_present"] = int(
                    _header(download.headers, "Last-Modified")
                )
                counts["download_etag_present"] = int(_header(download.headers, "ETag"))
                digest = hashlib.sha256(download.body).hexdigest()
                if download.status == 200:
                    try:
                        text = download.body.decode("gb18030", errors="strict")
                    except UnicodeDecodeError:
                        text = None
                    counts["download_decode_success"] = int(text is not None)
                    if text is not None:
                        master_present, constituent_present, identity_match = _master_identity(
                            text, target
                        )
                        counts["etf_master_present"] = int(master_present)
                        counts["etf_constituent_present"] = int(constituent_present)
                        counts["download_format_sections_match"] = int(
                            master_present and constituent_present
                        )
                        counts["download_code_date_matches"] = int(identity_match)
                        counts["verified_code_date_downloads"] = int(
                            identity_match and master_present and constituent_present
                        )
                details.append(
                    f"download_status={download.status},download_sha256={digest},"
                    f"decode={counts['download_decode_success']},"
                    f"master={counts['etf_master_present']},"
                    f"constituent={counts['etf_constituent_present']},"
                    f"identity={counts['download_code_date_matches']}"
                )
            else:
                details.append("download=not_requested_due_to_query_identity_or_filename_mismatch")
        else:
            details.append("query_data=empty_or_unavailable,download=not_requested")

        with Path(__file__).open("rb") as code_file:
            adapter_digest = hashlib.file_digest(code_file, "sha256").hexdigest()
        details.extend(
            (
                f"adapter_sha256={adapter_digest}",
                "scope limitation: verifies only present-day retrieval of a fixed "
                "issuer archive item; the issuer labels the displayed data supplementary and says "
                "it does not replace "
                "the official PCF",
                "source-time limitation: response Date, Last-Modified, or ETag presence does not "
                "establish historical first-publication time or pre-open availability",
                "research limitation: this does not establish historical universe completeness, "
                "point-in-time eligibility, or prospective strategy performance",
            )
        )
        return Measured(
            metrics=tuple(Metric(name=name, value=float(value)) for name, value in counts.items()),
            samples=1,
            details=tuple(details),
        )
