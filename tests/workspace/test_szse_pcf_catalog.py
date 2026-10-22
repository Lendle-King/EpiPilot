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
        assert timeout_seconds > 0
        assert max_bytes > 0
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _row(code: str, day: str = "2020-11-30") -> dict[str, str]:
    compact_day = day.replace("-", "")
    return {
        "jjdm": (
            "<a href='javascript:void(0);' "
            f"encode-open='/files/text/etf/ETF{code}{compact_day}.txt'>"
            f"ETF{code}申购赎回清单({day})</a>"
        )
    }


def _page(page: int, codes: list[str], *, total_pages: int = 2) -> HTTPResult:
    body = json.dumps(
        [
            {
                "metadata": {
                    "catalogid": "sgshqd",
                    "recordcount": len(codes),
                    "pagecount": total_pages,
                    "pageno": page,
                },
                "data": [_row(code) for code in codes],
                "error": None,
            }
        ]
    ).encode()
    return HTTPResult(200, {"Content-Type": "application/json"}, body)


def _contract(tmp_path: Path) -> MeasurementContract:
    manifest = {"schema": "szse-pcf-catalog-target/v1", "date": "2020-11-30"}
    content = json.dumps(manifest, separators=(",", ":")).encode()
    path = tmp_path / "catalog.json"
    path.write_bytes(content)
    return MeasurementContract(
        proposal_id="szse-pcf-catalog-test",
        purpose=Purpose.EXPLORE,
        adapter="szse_pcf_catalog_v1",
        git_revision="a" * 40,
        inputs=(InputFile(path="catalog.json", sha256=hashlib.sha256(content).hexdigest()),),
        rules=(Rule(metric="complete_catalog", minimum=1),),
    )


def _computer_type():
    computer_type = getattr(api, "SZSEPCFCatalogComputer", None)
    assert computer_type is not None, "SZSE PCF catalog adapter is not registered in the API"
    return computer_type


def _values(measured: object) -> dict[str, float]:
    return {item.name: item.value for item in measured.metrics}  # type: ignore[attr-defined]


def test_reads_every_pinned_catalog_page_without_a_code_filter(tmp_path: Path) -> None:
    transport = StaticTransport([_page(1, ["159919"]), _page(2, ["159915"])])

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    values = _values(measured)
    assert values["page_count"] == 2
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
    assert "does not establish contemporaneous" in " ".join(measured.details)


def test_marks_catalog_incomplete_when_a_row_identity_does_not_match(tmp_path: Path) -> None:
    wrong_date = HTTPResult(
        200,
        {"Content-Type": "application/json"},
        json.dumps(
            [
                {
                    "metadata": {
                        "catalogid": "sgshqd",
                        "recordcount": 1,
                        "pagecount": 1,
                        "pageno": 1,
                    },
                    "data": [_row("159919", "2020-12-01")],
                    "error": None,
                }
            ]
        ).encode(),
    )
    transport = StaticTransport([wrong_date])

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    values = _values(measured)
    assert values["identity_match_count"] == 0
    assert values["invalid_row_count"] == 1
    assert values["complete_catalog"] == 0


def test_retains_partial_measurement_on_timeout_without_claiming_completeness(
    tmp_path: Path,
) -> None:
    transport = StaticTransport([_page(1, ["159919"], total_pages=2), TimeoutError("timed out")])

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    values = _values(measured)
    assert values["rows_received"] == 1
    assert values["complete_catalog"] == 0
    assert "timeout" in " ".join(measured.details).lower()


def test_stops_at_the_twelve_page_bound_and_marks_catalog_incomplete(tmp_path: Path) -> None:
    responses = [_page(page, [f"159{page:03d}"], total_pages=13) for page in range(1, 13)]
    transport = StaticTransport(responses)

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    values = _values(measured)
    assert values["page_count"] == 13
    assert values["pages_received"] == 12
    assert values["complete_catalog"] == 0
    assert len(transport.urls) == 12
    assert "page_limit_reached=1" in measured.details


def test_rejects_unbounded_or_extra_manifest_fields_before_network_access(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    manifest = {"schema": "szse-pcf-catalog-target/v1", "date": "2020-11-30", "codes": []}
    content = json.dumps(manifest, separators=(",", ":")).encode()
    path = tmp_path / "catalog.json"
    path.write_bytes(content)
    contract = contract.model_copy(
        update={
            "inputs": (InputFile(path="catalog.json", sha256=hashlib.sha256(content).hexdigest()),)
        }
    )
    transport = StaticTransport([])

    try:
        _computer_type()(transport=transport).measure(tmp_path, contract)
    except ValueError as error:
        assert "manifest" in str(error).lower()
    else:
        raise AssertionError("extra manifest field was accepted")
    assert not transport.urls
