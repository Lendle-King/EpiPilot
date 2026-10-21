"""Measure raw, timestamped SZSE PCF witnesses from Arquivo.pt."""

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
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric

ADAPTER_ID = "arquivo_szse_pcf_v1"
_SCHEMA = "arquivo-szse-pcf-targets/v1"
_CDX_ENDPOINT = "https://arquivo.pt/wayback/cdx"
_REPLAY_PREFIX = "https://arquivo.pt/noFrame/replay/"
_PINNED_TARGETS = (
    (
        "159915",
        "2019-01-11",
        "https://reportdocs.static.szse.cn/files/text/etf/ETF15991520190111.txt",
    ),
    (
        "159915",
        "2020-11-30",
        "https://reportdocs.static.szse.cn/files/text/etf/ETF15991520201130.txt",
    ),
    (
        "159949",
        "2023-01-03",
        "https://reportdocs.static.szse.cn/files/text/etf/ETF15994920230103.txt",
    ),
    (
        "159949",
        "2026-10-16",
        "https://reportdocs.static.szse.cn/files/text/etf/ETF15994920261016.txt",
    ),
)
_MAX_CDX_BYTES = 2 * 1024 * 1024
_MAX_REPLAY_BYTES = 5 * 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 8
_DIGEST = re.compile(r"^[A-Z2-7]{32}$")
_TIMESTAMP = re.compile(r"^\d{14}$")


@dataclass(frozen=True)
class _Target:
    record_id: str
    code: str
    trading_date: date
    url: str
    cutoff_utc: datetime


@dataclass(frozen=True)
class ArquivoResponse:
    status: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes


def _read_targets(path: Path) -> tuple[_Target, ...]:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Arquivo.pt target manifest is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"schema", "targets"}:
        raise ValueError("Arquivo.pt manifest fields differ from the registered schema")
    if payload["schema"] != _SCHEMA:
        raise ValueError("unsupported Arquivo.pt target manifest")

    raw_targets = payload["targets"]
    if not isinstance(raw_targets, list) or len(raw_targets) != len(_PINNED_TARGETS):
        raise ValueError("Arquivo.pt manifest must contain the four pinned targets")

    pinned = {(code, day): url for code, day, url in _PINNED_TARGETS}
    targets: list[_Target] = []
    seen: set[tuple[str, str]] = set()
    expected_fields = {"record_id", "code", "date", "url", "cutoff_utc"}
    for item in raw_targets:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise ValueError("Arquivo.pt target fields differ from the registered schema")
        code, raw_day, url = item["code"], item["date"], item["url"]
        if not isinstance(code, str) or not isinstance(raw_day, str) or not isinstance(url, str):
            raise ValueError("Arquivo.pt target identity fields must be strings")
        identity = (code, raw_day)
        expected_url = pinned.get(identity)
        expected_record_id = f"{code}-{raw_day}"
        expected_cutoff = f"{raw_day}T01:15:00Z"
        if (
            expected_url is None
            or url != expected_url
            or item["record_id"] != expected_record_id
            or item["cutoff_utc"] != expected_cutoff
            or identity in seen
        ):
            raise ValueError("target is not one of the four fixed SZSE PCF URL/cutoff pairs")
        seen.add(identity)
        targets.append(
            _Target(
                record_id=expected_record_id,
                code=code,
                trading_date=date.fromisoformat(raw_day),
                url=expected_url,
                cutoff_utc=datetime.fromisoformat(expected_cutoff.replace("Z", "+00:00")),
            )
        )
    if seen != set(pinned):
        raise ValueError("Arquivo.pt manifest does not contain every fixed target exactly once")
    return tuple(targets)


def _cdx_url(target: _Target) -> str:
    params = (
        ("url", target.url),
        ("matchType", "exact"),
        ("from", "2000"),
        ("to", target.cutoff_utc.strftime("%Y%m%d%H%M%S")),
        ("fields", "url,timestamp,mime,status,digest,length,offset,filename"),
        ("output", "json"),
        ("sort", "reverse"),
        ("limit", "100"),
        ("resolveRevisits", "true"),
    )
    return f"{_CDX_ENDPOINT}?{urlencode(params)}"


def _parse_cdx(body: bytes) -> tuple[tuple[dict[str, object], ...], bool]:
    if not body.strip():
        return (), True
    if len(body) > _MAX_CDX_BYTES:
        raise ValueError("Arquivo.pt CDX response exceeds the registered byte limit")
    try:
        lines = body.decode("utf-8").splitlines()
        rows = tuple(json.loads(line) for line in lines if line.strip())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Arquivo.pt CDX response is not valid UTF-8 NDJSON") from error
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Arquivo.pt CDX response contains a non-object row")
    return tuple(rows), False


def _record_digest(body: bytes) -> str:
    digest = hashlib.sha1(body, usedforsecurity=False).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")


def _fetch_archivo(url: str) -> ArquivoResponse:
    request = Request(
        url,
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "EpiPilot public archive integrity check/1.0",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
            final_url = response.geturl()
            headers = {
                str(key).lower(): str(value).strip() for key, value in response.headers.items()
            }
            limit = _MAX_CDX_BYTES if url.startswith(_CDX_ENDPOINT) else _MAX_REPLAY_BYTES
            body = response.read(limit + 1)
    except HTTPError as error:
        status = error.code
        final_url = error.geturl()
        headers = {str(key).lower(): str(value).strip() for key, value in error.headers.items()}
        body = error.read(4096)
    limit = _MAX_CDX_BYTES if url.startswith(_CDX_ENDPOINT) else _MAX_REPLAY_BYTES
    if len(body) > limit:
        raise ValueError("Arquivo.pt response exceeds the registered byte limit")
    return ArquivoResponse(status=status, final_url=final_url, headers=headers, body=body)


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


def _metric(name: str, value: int | float) -> Metric:
    return Metric(name=name, value=float(value))


class ArquivoSZSEPCFComputer(Computer):
    """Verify fixed PCF payloads in Arquivo.pt captures before their open cutoff."""

    quality = Quality.MEASUREMENT

    def __init__(self, fetcher: Callable[[str], ArquivoResponse] | None = None) -> None:
        self._fetcher = fetcher or _fetch_archivo

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID or len(contract.inputs) != 1:
            raise ValueError("Arquivo.pt requires its adapter and one fixed target manifest")
        target_path = safe_input(root, contract.inputs[0].path)
        targets = _read_targets(target_path)
        counts = {
            "cdx_exact_rows": 0,
            "cdx_compressed_record_length_bytes": 0,
            "raw_replay_body_bytes": 0,
            "raw_replays_fetched": 0,
            "raw_sha1_matches_cdx": 0,
            "gbk_identity_matches": 0,
            "verified_pre_cutoff_targets": 0,
            "cutoff_or_later_captures_ignored": 0,
            "empty_cdx_responses": 0,
            "cdx_http_200_responses": 0,
            "cdx_requests_failed": 0,
            "replay_requests_failed": 0,
            "invalid_cdx_responses": 0,
            "invalid_cdx_rows": 0,
        }
        details = [f"fixed_target_count={len(targets)}"]

        for target in targets:
            cdx_request = _cdx_url(target)
            try:
                cdx_response = self._fetcher(cdx_request)
            except (OSError, TimeoutError, ValueError) as error:
                counts["cdx_requests_failed"] += 1
                details.append(f"{target.record_id}:cdx=unavailable,error={type(error).__name__}")
                continue
            if cdx_response.status != 200 or cdx_response.final_url != cdx_request:
                counts["cdx_requests_failed"] += 1
                details.append(
                    f"{target.record_id}:cdx=unavailable,status={cdx_response.status},"
                    f"redirected={int(cdx_response.final_url != cdx_request)}"
                )
                continue
            counts["cdx_http_200_responses"] += 1
            cdx_sha256 = hashlib.sha256(cdx_response.body).hexdigest()
            try:
                rows, empty = _parse_cdx(cdx_response.body)
            except ValueError:
                counts["invalid_cdx_responses"] += 1
                details.append(f"{target.record_id}:cdx=invalid,sha256={cdx_sha256}")
                continue
            counts["empty_cdx_responses"] += int(empty)
            counts["cdx_exact_rows"] += len(rows)
            details.append(
                f"{target.record_id}:cdx_rows={len(rows)},cdx_sha256={cdx_sha256},empty={int(empty)}"
            )

            eligible: list[tuple[str, str, int]] = []
            for row in rows:
                timestamp = row.get("timestamp")
                original = row.get("url")
                status = row.get("status")
                digest = row.get("digest")
                raw_length = row.get("length")
                if isinstance(raw_length, int):
                    record_length = raw_length
                elif isinstance(raw_length, str) and raw_length.isdigit():
                    record_length = int(raw_length)
                else:
                    counts["invalid_cdx_rows"] += 1
                    continue
                if (
                    not isinstance(timestamp, str)
                    or _TIMESTAMP.fullmatch(timestamp) is None
                    or not isinstance(original, str)
                    or not isinstance(status, (str, int))
                    or str(status) != "200"
                    or not isinstance(digest, str)
                    or _DIGEST.fullmatch(digest) is None
                    or record_length <= 0
                ):
                    counts["invalid_cdx_rows"] += 1
                    continue
                if original != target.url:
                    continue
                try:
                    captured_at = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                except ValueError:
                    counts["invalid_cdx_rows"] += 1
                    continue
                if captured_at >= target.cutoff_utc:
                    counts["cutoff_or_later_captures_ignored"] += 1
                    continue
                eligible.append((timestamp, digest, record_length))

            witnessed = False
            for timestamp, expected_digest, record_length in sorted(eligible, reverse=True)[:1]:
                replay_url = f"{_REPLAY_PREFIX}{timestamp}id_/{target.url}"
                try:
                    replay = self._fetcher(replay_url)
                except (OSError, TimeoutError, ValueError) as error:
                    counts["replay_requests_failed"] += 1
                    details.append(
                        f"{target.record_id}:capture={timestamp},replay=unavailable,"
                        f"error={type(error).__name__}"
                    )
                    continue
                if replay.status != 200 or replay.final_url != replay_url:
                    counts["replay_requests_failed"] += 1
                    details.append(f"{target.record_id}:capture={timestamp},replay=unavailable")
                    continue
                if len(replay.body) > _MAX_REPLAY_BYTES:
                    counts["replay_requests_failed"] += 1
                    details.append(f"{target.record_id}:capture={timestamp},replay=too_large")
                    continue
                counts["raw_replays_fetched"] += 1
                counts["cdx_compressed_record_length_bytes"] += record_length
                counts["raw_replay_body_bytes"] += len(replay.body)
                digest_match = _record_digest(replay.body) == expected_digest
                counts["raw_sha1_matches_cdx"] += int(digest_match)
                if not digest_match:
                    details.append(f"{target.record_id}:capture={timestamp},raw_sha1_matches_cdx=0")
                    continue
                try:
                    text = replay.body.decode("gbk")
                except UnicodeDecodeError:
                    text = ""
                identity_match = target.code in text and target.trading_date.isoformat() in text
                counts["gbk_identity_matches"] += int(identity_match)
                details.append(
                    f"{target.record_id}:capture_utc={timestamp},raw_sha1_matches_cdx=1,"
                    f"gbk_identity_match={int(identity_match)},"
                    f"cdx_compressed_record_length_bytes={record_length},"
                    f"raw_replay_body_bytes={len(replay.body)}"
                )
                witnessed = identity_match
            counts["verified_pre_cutoff_targets"] += int(witnessed)

        metrics = tuple(_metric(name, value) for name, value in counts.items())
        details.extend(
            (
                f"adapter_sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}",
                "scope=integrity; only four fixed SZSE PCF URLs were queried through anonymous "
                "Arquivo.pt CDX and noFrame raw replay GETs; a witness requires a capture strictly "
                "before 09:15 Asia/Shanghai, SHA-1 equality and GBK code/date identity; CDX length "
                "is the compressed WARC/ARC record length and is not compared to the raw replay "
                "payload length; missing or unverified captures do not prove historical absence, "
                "source first-publication time, PIT universe membership or strategy performance",
            )
        )
        return Measured(metrics=metrics, samples=len(targets), details=tuple(details))
