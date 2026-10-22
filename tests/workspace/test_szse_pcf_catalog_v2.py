from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.workspace import api


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: dict[str, str]
    body: bytes


class StaticTransport:
    def __init__(self, responses: list[HTTPResult | Exception]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HTTPResult:
        self.urls.append(url)
        assert timeout_seconds == 4.0
        assert max_bytes == 1_000_000
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _row(code: str, day: str = "2020-11-30") -> dict[str, str]:
    return {
        "jjdm": (
            "<a href='javascript:void(0);' "
            f"encode-open='/files/text/etf/ETF{code}{day.replace('-', '')}.txt'>"
            f"ETF{code}申购赎回清单({day})</a>"
        )
    }


def _page(
    page: int,
    codes: list[str],
    *,
    record_count: int,
    page_count: int,
    page_size: int,
) -> HTTPResult:
    body = json.dumps(
        [
            {
                "metadata": {
                    "catalogid": "sgshqd",
                    "recordcount": record_count,
                    "pagesize": page_size,
                    "pagecount": page_count,
                    "pageno": page,
                },
                "data": [_row(code) for code in codes],
                "error": None,
            }
        ]
    ).encode()
    return HTTPResult(200, {"Content-Type": "application/json"}, body)


def _contract(tmp_path: Path) -> MeasurementContract:
    manifest = {"schema": "szse-pcf-catalog-target/v2", "date": "2020-11-30"}
    content = json.dumps(manifest, separators=(",", ":")).encode()
    path = tmp_path / "catalog.json"
    path.write_bytes(content)
    return MeasurementContract(
        proposal_id="szse-pcf-catalog-v2-test",
        purpose=Purpose.EXPLORE,
        adapter="szse_pcf_catalog_v2",
        git_revision="a" * 40,
        inputs=(InputFile(path="catalog.json", sha256=hashlib.sha256(content).hexdigest()),),
        rules=(Rule(metric="complete_catalog", minimum=1),),
    )


def _computer_type():
    computer_type = getattr(api, "SZSEPCFCatalogComputerV2", None)
    assert computer_type is not None, "versioned SZSE PCF catalog adapter is not registered"
    return computer_type


def _values(measured: object) -> dict[str, float]:
    return {item.name: item.value for item in measured.metrics}  # type: ignore[attr-defined]


def test_treats_recordcount_as_total_rows_across_all_pages(tmp_path: Path) -> None:
    transport = StaticTransport(
        [
            _page(1, ["159919"], record_count=2, page_count=2, page_size=1),
            _page(2, ["159915"], record_count=2, page_count=2, page_size=1),
        ]
    )

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    values = _values(measured)
    assert values["record_count"] == 2
    assert values["page_count"] == 2
    assert values["pages_received"] == 2
    assert values["rows_received"] == 2
    assert values["identity_match_count"] == 2
    assert values["unique_code_count"] == 2
    assert values["complete_catalog"] == 1
    assert measured.samples == 2
    assert len(transport.urls) == 2
    for page_number, url in enumerate(transport.urls, start=1):
        query = parse_qs(urlsplit(url).query)
        assert query["PAGENO"] == [str(page_number)]
        assert query["txtStart"] == ["2020-11-30"]
        assert query["txtEnd"] == ["2020-11-30"]
        assert "txtJCorDH" not in query


def test_preserves_incomplete_status_for_code_or_date_identity_mismatch(tmp_path: Path) -> None:
    transport = StaticTransport([_page(1, ["159919"], record_count=1, page_count=1, page_size=20)])
    response = transport.responses[0]
    payload = json.loads(response.body)
    payload[0]["data"] = [_row("159920", "2020-12-01")]
    transport.responses[0] = HTTPResult(200, response.headers, json.dumps(payload).encode())

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    values = _values(measured)
    assert values["identity_match_count"] == 0
    assert values["invalid_row_count"] == 1
    assert values["complete_catalog"] == 0


def test_records_timeout_as_incomplete_without_claiming_empty_catalog(tmp_path: Path) -> None:
    transport = StaticTransport(
        [
            _page(1, ["159919"], record_count=2, page_count=2, page_size=1),
            TimeoutError("timed out"),
        ]
    )

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    values = _values(measured)
    assert values["rows_received"] == 1
    assert values["complete_catalog"] == 0
    assert "timeout" in " ".join(measured.details).lower()


def test_stops_after_twelve_pages_when_catalog_exceeds_the_bound(tmp_path: Path) -> None:
    transport = StaticTransport(
        [
            _page(page, [f"159{page:03d}"], record_count=13, page_count=13, page_size=1)
            for page in range(1, 13)
        ]
    )

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    values = _values(measured)
    assert values["record_count"] == 13
    assert values["pages_received"] == 12
    assert values["complete_catalog"] == 0
    assert len(transport.urls) == 12
    assert "page_limit_reached=1" in measured.details
