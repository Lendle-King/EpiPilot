"""Use Wayback Availability only to locate candidates for pinned SZSE PCF files."""

from __future__ import annotations

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
from epipilot.workspace.wayback_szse_pcf import _parse_cdx, _sha1_base32

ADAPTER_ID = "szse_pcf_wayback_availability_v1"
_SCHEMA = "szse-pcf-wayback-availability-targets/v1"
_AVAILABILITY_ENDPOINT = "https://archive.org/wayback/available"
_CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
_MAX_TARGETS = 4
_MAX_AVAILABILITY_BYTES = 256 * 1024
_CDX_ROW_LIMIT = 5
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
_TIMESTAMP = re.compile(r"^\d{14}$")
_DIGEST = re.compile(r"^[A-Z2-7]{32}$")


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
        raise ValueError("Wayback availability manifest is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"schema", "targets"}:
        raise ValueError("Wayback availability manifest fields differ from the registered schema")
    if payload["schema"] != _SCHEMA:
        raise ValueError("unsupported Wayback availability manifest")
    raw_targets = payload["targets"]
    if not isinstance(raw_targets, list) or len(raw_targets) != _MAX_TARGETS:
        raise ValueError("Wayback availability manifest must contain the four pinned targets")

    pinned = {(code, day): url for code, day, url in _PINNED_TARGETS}
    targets: list[_Target] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_targets:
        fields = {"record_id", "code", "date", "url", "cutoff_utc"}
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("Wayback availability target fields differ from the schema")
        code, raw_day, url = item["code"], item["date"], item["url"]
        if not all(isinstance(value, str) for value in (code, raw_day, url)):
            raise ValueError("Wayback availability target identity fields must be strings")
        identity = (code, raw_day)
        expected_url = pinned.get(identity)
        expected_cutoff = f"{raw_day}T01:15:00Z"
        if (
            expected_url is None
            or url != expected_url
            or item["record_id"] != f"{code}-{raw_day}"
            or item["cutoff_utc"] != expected_cutoff
            or identity in seen
        ):
            raise ValueError("target is not one of the four fixed SZSE URL/cutoff pairs")
        seen.add(identity)
        targets.append(
            _Target(
                record_id=f"{code}-{raw_day}",
                code=code,
                trading_date=date.fromisoformat(raw_day),
                url=expected_url,
                cutoff_utc=datetime.fromisoformat(expected_cutoff.replace("Z", "+00:00")),
            )
        )
    if seen != set(pinned):
        raise ValueError("Wayback availability manifest does not contain all four targets")
    return tuple(targets)


def _availability_url(target: _Target) -> str:
    params = (("url", target.url), ("timestamp", target.cutoff_utc.strftime("%Y%m%d%H%M%S")))
    return f"{_AVAILABILITY_ENDPOINT}?{urlencode(params)}"


def _cdx_url(target: _Target, timestamp: str) -> str:
    params = (
        ("url", target.url),
        ("matchType", "exact"),
        ("output", "json"),
        ("from", timestamp),
        ("to", timestamp),
        ("fl", "timestamp,original,mimetype,statuscode,digest,length"),
        ("filter", "statuscode:200"),
        ("limit", str(_CDX_ROW_LIMIT)),
        ("gzip", "false"),
    )
    return f"{_CDX_ENDPOINT}?{urlencode(params)}"


def _candidate_timestamp(body: bytes, expected_url: str) -> tuple[str | None, bool]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Wayback availability response is not valid JSON") from error
    if not isinstance(payload, dict) or payload.get("url") != expected_url:
        raise ValueError("Wayback availability response is not bound to the pinned URL")
    snapshots = payload.get("archived_snapshots")
    if not isinstance(snapshots, dict):
        raise ValueError("Wayback availability response lacks an archived_snapshots object")
    closest = snapshots.get("closest")
    if closest is None:
        return None, True
    if not isinstance(closest, dict):
        raise ValueError("Wayback availability closest snapshot is malformed")
    if closest.get("available") is not True or closest.get("status") != "200":
        return None, False
    timestamp = closest.get("timestamp")
    if not isinstance(timestamp, str) or _TIMESTAMP.fullmatch(timestamp) is None:
        raise ValueError("Wayback availability timestamp is malformed")
    try:
        datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("Wayback availability timestamp is invalid") from error
    return timestamp, False


def _metric(name: str, value: int | float) -> Metric:
    return Metric(name=name, value=float(value))


class WaybackSZSEPCFAvailabilityComputer(Computer):
    """Verify a Wayback availability candidate through exact CDX and raw replay bytes."""

    quality = Quality.MEASUREMENT

    def __init__(self, fetcher: Callable[[str], WaybackResponse] | None = None) -> None:
        self._fetcher = fetcher or _fetch_wayback

    def measure(self, root: Path, contract: MeasurementContract) -> Measured:
        if contract.adapter != ADAPTER_ID or len(contract.inputs) != 1:
            raise ValueError("Wayback availability requires one pinned target manifest")
        targets = _read_targets(safe_input(root, contract.inputs[0].path))
        counts = {
            "availability_requests": 0,
            "availability_http_200": 0,
            "availability_empty_responses": 0,
            "availability_no_200_candidate": 0,
            "availability_requests_failed": 0,
            "invalid_availability_responses": 0,
            "cutoff_or_later_candidates": 0,
            "cdx_requests": 0,
            "cdx_rows": 0,
            "cdx_requests_failed": 0,
            "invalid_cdx_responses": 0,
            "raw_replays_fetched": 0,
            "raw_sha1_length_matches_cdx": 0,
            "gbk_identity_matches": 0,
            "replay_requests_failed": 0,
            "verified_pre_cutoff_targets": 0,
        }
        details = [f"fixed_target_count={len(targets)}"]

        for target in targets:
            availability_request = _availability_url(target)
            counts["availability_requests"] += 1
            try:
                availability = self._fetcher(availability_request)
            except (OSError, TimeoutError, ValueError) as error:
                counts["availability_requests_failed"] += 1
                details.append(
                    f"{target.record_id}:availability=unavailable,error={type(error).__name__}"
                )
                continue
            if (
                availability.status != 200
                or availability.final_url != availability_request
                or len(availability.body) > _MAX_AVAILABILITY_BYTES
            ):
                counts["availability_requests_failed"] += 1
                details.append(f"{target.record_id}:availability=unavailable")
                continue
            counts["availability_http_200"] += 1
            availability_sha256 = hashlib.sha256(availability.body).hexdigest()
            try:
                timestamp, empty = _candidate_timestamp(availability.body, target.url)
            except ValueError:
                counts["invalid_availability_responses"] += 1
                details.append(
                    f"{target.record_id}:availability=invalid,sha256={availability_sha256}"
                )
                continue
            counts["availability_empty_responses"] += int(empty)
            if timestamp is None:
                counts["availability_no_200_candidate"] += int(not empty)
                details.append(
                    f"{target.record_id}:availability_candidate=none,sha256={availability_sha256}"
                )
                continue
            captured_at = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            if captured_at >= target.cutoff_utc:
                counts["cutoff_or_later_candidates"] += 1
                details.append(f"{target.record_id}:candidate={timestamp},pre_cutoff=0")
                continue

            cdx_request = _cdx_url(target, timestamp)
            counts["cdx_requests"] += 1
            try:
                cdx_response = self._fetcher(cdx_request)
            except (OSError, TimeoutError, ValueError) as error:
                counts["cdx_requests_failed"] += 1
                details.append(
                    f"{target.record_id}:candidate={timestamp},cdx=unavailable,"
                    f"error={type(error).__name__}"
                )
                continue
            if cdx_response.status != 200 or cdx_response.final_url != cdx_request:
                counts["cdx_requests_failed"] += 1
                details.append(f"{target.record_id}:candidate={timestamp},cdx=unavailable")
                continue
            cdx_sha256 = hashlib.sha256(cdx_response.body).hexdigest()
            try:
                rows, limit_reached = _parse_cdx(cdx_response.body)
            except ValueError:
                counts["invalid_cdx_responses"] += 1
                details.append(f"{target.record_id}:candidate={timestamp},cdx=invalid")
                continue
            counts["cdx_rows"] += len(rows)
            if limit_reached or len(rows) >= _CDX_ROW_LIMIT:
                counts["invalid_cdx_responses"] += 1
                details.append(f"{target.record_id}:candidate={timestamp},cdx=incomplete")
                continue
            matching = [
                (row_timestamp, digest, length)
                for row_timestamp, original, _mimetype, status, digest, length in rows
                if row_timestamp == timestamp and original == target.url and status == "200"
            ]
            if len(matching) != 1:
                details.append(
                    f"{target.record_id}:candidate={timestamp},cdx_rows={len(rows)},"
                    f"matching_rows={len(matching)},cdx_sha256={cdx_sha256}"
                )
                continue
            _, expected_digest, expected_length = matching[0]
            if _DIGEST.fullmatch(expected_digest) is None or not expected_length.isdigit():
                counts["invalid_cdx_responses"] += 1
                details.append(f"{target.record_id}:candidate={timestamp},cdx_identity=invalid")
                continue

            replay_request = f"https://web.archive.org/web/{timestamp}id_/{target.url}"
            try:
                replay = self._fetcher(replay_request)
            except (OSError, TimeoutError, ValueError) as error:
                counts["replay_requests_failed"] += 1
                details.append(
                    f"{target.record_id}:candidate={timestamp},replay=unavailable,"
                    f"error={type(error).__name__}"
                )
                continue
            if replay.status != 200 or replay.final_url != replay_request:
                counts["replay_requests_failed"] += 1
                details.append(f"{target.record_id}:candidate={timestamp},replay=unavailable")
                continue
            counts["raw_replays_fetched"] += 1
            bytes_match = (
                len(replay.body) == int(expected_length)
                and _sha1_base32(replay.body) == expected_digest
            )
            counts["raw_sha1_length_matches_cdx"] += int(bytes_match)
            if not bytes_match:
                details.append(f"{target.record_id}:candidate={timestamp},cdx_bytes_match=0")
                continue
            try:
                text = replay.body.decode("gbk")
            except UnicodeDecodeError:
                text = ""
            identity_match = target.code in text and target.trading_date.isoformat() in text
            counts["gbk_identity_matches"] += int(identity_match)
            counts["verified_pre_cutoff_targets"] += int(identity_match)
            details.append(
                f"{target.record_id}:candidate={timestamp},cdx_bytes_match=1,"
                f"gbk_identity_match={int(identity_match)}"
            )

        return Measured(
            metrics=tuple(_metric(name, value) for name, value in counts.items()),
            samples=len(targets),
            details=(
                f"adapter_source_sha256={hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}",
                "Wayback Availability is candidate discovery only; each witness also requires "
                "the exact CDX timestamp row and independently replayed raw bytes.",
                "Only the four pinned SZSE URLs are queried; no redirects are followed.",
                "A missing, unavailable or late candidate is inconclusive and does not establish "
                "historical source absence or first-publication time.",
                *details,
            ),
        )
