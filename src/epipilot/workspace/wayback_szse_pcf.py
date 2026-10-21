"""Measure exact Wayback witnesses for four pinned SZSE ETF PCF files."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlencode

from epipilot.research.discovery import safe_input
from epipilot.research.frontier import MeasurementContract, Quality
from epipilot.research.measurement import Computer, Measured
from epipilot.research.models import Metric
from epipilot.workspace.wayback_sse_notice import WaybackResponse, _fetch_wayback

ADAPTER_ID = "szse_pcf_wayback_archive_v1"
_SCHEMA = "szse-pcf-wayback-targets/v1"
_CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
_MAX_TARGETS = 4
_MAX_CDX_ROWS = 1000
_REPLAY_CANDIDATE_LIMIT = 2
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
_DIGEST = re.compile(r"^[A-Z2-7]{32}$")
_TIMESTAMP = re.compile(r"^\d{14}$")


@dataclass(frozen=True)
class _Target:
    record_id: str
    code: str
    trading_date: date
    url: str
    cutoff_utc: datetime


def _read_targets(path: Path) -> tuple[_Target, ...]:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("SZSE Wayback target manifest is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"schema", "targets"}:
        raise ValueError("SZSE Wayback manifest fields differ from the registered schema")
    if payload["schema"] != _SCHEMA:
        raise ValueError("unsupported SZSE Wayback target manifest")
    raw_targets = payload["targets"]
    if not isinstance(raw_targets, list) or len(raw_targets) != _MAX_TARGETS:
        raise ValueError("SZSE Wayback manifest must contain the four pinned targets")

    pinned = {(code, day): url for code, day, url in _PINNED_TARGETS}
    targets: list[_Target] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_targets:
        if not isinstance(item, dict) or set(item) != {
            "record_id",
            "code",
            "date",
            "url",
            "cutoff_utc",
        }:
            raise ValueError("SZSE Wayback target fields differ from the registered schema")
        code, raw_day, url = item["code"], item["date"], item["url"]
        if not isinstance(code, str) or not isinstance(raw_day, str) or not isinstance(url, str):
            raise ValueError("SZSE Wayback target identity fields must be strings")
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
        raise ValueError("SZSE Wayback manifest does not contain every fixed target exactly once")
    return tuple(targets)


def _cdx_url(target: _Target) -> str:
    params = (
        ("url", target.url),
        ("matchType", "exact"),
        ("output", "json"),
        ("from", "2000"),
        ("to", target.cutoff_utc.strftime("%Y%m%d%H%M%S")),
        ("fl", "timestamp,original,mimetype,statuscode,digest,length"),
        ("filter", "statuscode:200"),
        ("limit", str(_MAX_CDX_ROWS)),
        ("gzip", "false"),
    )
    return f"{_CDX_ENDPOINT}?{urlencode(params)}"


def _parse_cdx(
    body: bytes,
) -> tuple[tuple[tuple[str, str, str, str, str, str], ...], bool]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Wayback CDX response is not valid JSON") from error
    if not isinstance(payload, list):
        raise ValueError("Wayback CDX response is not a row array")
    if not payload:
        return (), False
    header = payload[0]
    required = ("timestamp", "original", "mimetype", "statuscode", "digest", "length")
    if not isinstance(header, list) or not all(name in header for name in required):
        raise ValueError("Wayback CDX response has an unexpected field header")
    indexes = tuple(header.index(name) for name in required)
    rows: list[tuple[str, str, str, str, str, str]] = []
    for raw in payload[1:]:
        if not isinstance(raw, list) or len(raw) != len(header):
            raise ValueError("Wayback CDX response contains a malformed row")
        values = tuple(raw[index] for index in indexes)
        if not all(isinstance(value, str) for value in values):
            raise ValueError("Wayback CDX response contains non-text fields")
        rows.append((values[0], values[1], values[2], values[3], values[4], values[5]))
    return tuple(rows), len(rows) >= _MAX_CDX_ROWS


def _sha1_base32(body: bytes) -> str:
    digest = hashlib.sha1(body, usedforsecurity=False).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")


def _metric(name: str, value: int | float) -> Metric:
    return Metric(name=name, value=float(value))


class WaybackSZSEPCFComputer(Computer):
    """Verify CDX raw bytes and GBK identity for the four preregistered PCF files."""

    quality = Quality.MEASUREMENT

    def __init__(self, fetcher: Callable[[str], WaybackResponse] | None = None) -> None:
        self._fetcher = fetcher or _fetch_wayback

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID or len(contract.inputs) != 1:
            raise ValueError("SZSE Wayback requires its adapter and one fixed target manifest")
        target_path = safe_input(root, contract.inputs[0].path)
        targets = _read_targets(target_path)
        counts = {
            "cdx_exact_rows": 0,
            "raw_replays_fetched": 0,
            "raw_sha1_length_matches_cdx": 0,
            "gbk_identity_matches": 0,
            "verified_pre_cutoff_targets": 0,
            "cutoff_or_later_captures_ignored": 0,
            "empty_cdx_responses": 0,
            "cdx_http_200_responses": 0,
            "cdx_requests_failed": 0,
            "replay_requests_failed": 0,
            "invalid_cdx_responses": 0,
            "candidate_limit_reached": 0,
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
                rows, candidate_limit_reached = _parse_cdx(cdx_response.body)
            except ValueError:
                counts["invalid_cdx_responses"] += 1
                details.append(f"{target.record_id}:cdx=invalid,sha256={cdx_sha256}")
                continue
            counts["cdx_exact_rows"] += len(rows)
            counts["empty_cdx_responses"] += int(not rows)
            counts["candidate_limit_reached"] += int(candidate_limit_reached)
            details.append(
                f"{target.record_id}:cdx_rows={len(rows)},cdx_sha256={cdx_sha256},"
                f"candidate_limit_reached={int(candidate_limit_reached)}"
            )

            eligible: list[tuple[str, str, str]] = []
            for timestamp, original, _mimetype, status, digest, length in rows:
                if original != target.url or status != "200":
                    continue
                if (
                    _TIMESTAMP.fullmatch(timestamp) is None
                    or _DIGEST.fullmatch(digest) is None
                    or not length.isdigit()
                ):
                    counts["invalid_cdx_rows"] += 1
                    continue
                try:
                    captured_at = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
                except ValueError:
                    counts["invalid_cdx_rows"] += 1
                    continue
                if captured_at >= target.cutoff_utc:
                    counts["cutoff_or_later_captures_ignored"] += 1
                    continue
                eligible.append((timestamp, digest, length))

            witnessed = False
            candidates = sorted(eligible, reverse=True)[:_REPLAY_CANDIDATE_LIMIT]
            for timestamp, expected_digest, expected_length in candidates:
                replay_url = f"https://web.archive.org/web/{timestamp}id_/{target.url}"
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
                counts["raw_replays_fetched"] += 1
                digest_match = (
                    len(replay.body) == int(expected_length)
                    and _sha1_base32(replay.body) == expected_digest
                )
                counts["raw_sha1_length_matches_cdx"] += int(digest_match)
                if not digest_match:
                    details.append(f"{target.record_id}:capture={timestamp},cdx_bytes_match=0")
                    continue
                try:
                    text = replay.body.decode("gbk")
                except UnicodeDecodeError:
                    text = ""
                identity_match = target.code in text and target.trading_date.isoformat() in text
                counts["gbk_identity_matches"] += int(identity_match)
                details.append(
                    f"{target.record_id}:capture_utc={timestamp},cdx_bytes_match=1,"
                    f"gbk_identity_match={int(identity_match)}"
                )
                if identity_match:
                    witnessed = True
                    break
            counts["verified_pre_cutoff_targets"] += int(witnessed)

        metrics = tuple(_metric(name, value) for name, value in counts.items())
        details.extend(
            (
                f"adapter_sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}",
                "scope=integrity; only four fixed SZSE PCF URLs were queried through anonymous "
                "Wayback CDX and raw replay GETs; a witness requires capture strictly before "
                "09:15 Asia/Shanghai, CDX SHA-1/length equality and GBK code/date identity; "
                "missing or unverified captures do not prove historical absence, source "
                "publication time, PIT universe membership or strategy performance",
            )
        )
        return Measured(metrics=metrics, samples=len(targets), details=tuple(details))
