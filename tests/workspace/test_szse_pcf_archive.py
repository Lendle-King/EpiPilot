from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.workspace import api


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: dict[str, str]
    body: bytes


class StaticTransport:
    def __init__(self, responses: list[HTTPResult]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HTTPResult:
        self.urls.append(url)
        assert timeout_seconds > 0
        assert max_bytes > 0
        return self.responses.pop(0)


def _contract(tmp_path: Path, *, code: str = "159919") -> MeasurementContract:
    manifest = {"schema": "szse-pcf-targets/v1", "targets": [{"code": code, "date": "2020-11-30"}]}
    path = tmp_path / "targets.json"
    content = json.dumps(manifest, separators=(",", ":")).encode()
    path.write_bytes(content)
    return MeasurementContract(
        proposal_id="szse-pcf-test",
        purpose=Purpose.EXPLORE,
        adapter="szse_pcf_archive_v1",
        git_revision="a" * 40,
        inputs=(InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),),
        rules=(Rule(metric="file_identity_match_count", minimum=1),),
    )


def _fixture_responses(*, link_code: str = "159919") -> list[HTTPResult]:
    query_body = json.dumps(
        [
            {
                "metadata": {
                    "catalogid": "sgshqd",
                    "recordcount": 1,
                    "pagecount": 1,
                    "pageno": 1,
                },
                "data": [
                    {
                        "jjdm": (
                            "<a href='javascript:void(0);' "
                            f"encode-open='/files/text/etf/ETF{link_code}20201130.txt'>"
                            f"ETF{link_code}申购赎回清单(2020-11-30)</a>"
                        )
                    }
                ],
                "error": None,
            }
        ]
    ).encode()
    raw_body = "300ETF申购赎回清单 (2020-11-30) 159919\r\n".encode("gbk")
    return [
        HTTPResult(
            200,
            {"Content-Type": "application/json", "Date": "Wed, 21 Oct 2026 12:00:00 GMT"},
            query_body,
        ),
        HTTPResult(
            200,
            {
                "Content-Type": "text/plain; charset=GBK",
                "Date": "Wed, 21 Oct 2026 12:00:01 GMT",
                "Last-Modified": "Mon, 30 Nov 2020 00:10:36 GMT",
                "ETag": '"5fc4387c-be18"',
            },
            raw_body,
        ),
    ]


def _measurement_values(measured: object) -> dict[str, float]:
    return {item.name: item.value for item in measured.metrics}  # type: ignore[attr-defined]


def _computer_type():
    computer_type = getattr(api, "SZSEPCFArchiveComputer", None)
    assert computer_type is not None, "SZSE PCF retrieval adapter is not registered in the API"
    return computer_type


def test_retrieves_matching_szse_pcf_and_reports_server_timestamp_without_promoting_it(
    tmp_path: Path,
) -> None:
    transport = StaticTransport(_fixture_responses())
    computer = _computer_type()(transport=transport)

    measured = computer.measure(
        tmp_path,
        _contract(tmp_path),
    )

    values = _measurement_values(measured)
    assert measured.samples == 1
    assert values["query_status_200"] == 1
    assert values["file_identity_match_count"] == 1
    assert values["last_modified_present_count"] == 1
    assert values["server_last_modified_before_open_count"] == 1
    assert values["identity_and_preopen_count"] == 1
    assert "first-publication time is not established" in " ".join(measured.details)
    assert len(transport.urls) == 2
    assert transport.urls[0].startswith("https://www.szse.cn/api/report/ShowReport/data?")
    assert transport.urls[1] == (
        "https://reportdocs.static.szse.cn/files/text/etf/ETF15991920201130.txt"
    )


def test_measurement_is_stable_when_http_date_headers_change(tmp_path: Path) -> None:
    first_responses = _fixture_responses()
    second_responses = _fixture_responses()
    for index, response in enumerate(second_responses):
        headers = dict(response.headers)
        headers["Date"] = f"Wed, 21 Oct 2026 13:0{index}:00 GMT"
        second_responses[index] = HTTPResult(response.status, headers, response.body)

    first = _computer_type()(transport=StaticTransport(first_responses)).measure(
        tmp_path,
        _contract(tmp_path),
    )
    second = _computer_type()(transport=StaticTransport(second_responses)).measure(
        tmp_path,
        _contract(tmp_path),
    )

    assert first == second


def test_rejects_code_mismatch_in_szse_pcf_listing_without_fetching_file(tmp_path: Path) -> None:
    transport = StaticTransport(_fixture_responses(link_code="159920")[:1])

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    assert _measurement_values(measured)["file_identity_match_count"] == 0
    assert len(transport.urls) == 1


def test_does_not_treat_error_response_timestamp_as_pcf_metadata(tmp_path: Path) -> None:
    responses = _fixture_responses()
    responses[1] = HTTPResult(
        404,
        {
            "Content-Type": "text/html",
            "Date": "Wed, 21 Oct 2026 12:00:01 GMT",
            "Last-Modified": "Mon, 30 Nov 2020 00:10:36 GMT",
        },
        b"not found",
    )
    transport = StaticTransport(responses)

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path))

    values = _measurement_values(measured)
    assert values["file_identity_match_count"] == 0
    assert values["last_modified_present_count"] == 0
    assert values["server_last_modified_before_open_count"] == 0
    assert values["identity_and_preopen_count"] == 0


def test_rejects_non_etf_codes_in_szse_target_manifest(tmp_path: Path) -> None:
    transport = StaticTransport([])

    try:
        _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path, code="000001"))
    except ValueError as error:
        assert "ETF code" in str(error)
    else:
        raise AssertionError("non-ETF code was accepted")

    assert transport.urls == []
