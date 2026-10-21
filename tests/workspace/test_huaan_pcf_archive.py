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
    final_url: str
    headers: dict[str, str]
    body: bytes


class StaticTransport:
    def __init__(self, responses: list[HTTPResult]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> HTTPResult:
        self.urls.append(url)
        assert 0 < timeout_seconds <= 8
        assert 0 < max_bytes <= 5_000_000
        return self.responses.pop(0)


def _computer_type():
    computer_type = getattr(api, "HuaanPCFArchiveComputer", None)
    assert computer_type is not None, "Huaan historical PCF adapter is not registered in the API"
    return computer_type


def _manifest(targets: list[tuple[str, str]]) -> bytes:
    value = {
        "schema": "huaan-pcf-targets/v1",
        "targets": [{"code": code, "date": day} for code, day in targets],
    }
    return json.dumps(value, separators=(",", ":")).encode()


def _contract(tmp_path: Path, targets: list[tuple[str, str]]) -> MeasurementContract:
    content = _manifest(targets)
    path = tmp_path / "targets.json"
    path.write_bytes(content)
    return MeasurementContract(
        proposal_id="huaan-pcf-test",
        purpose=Purpose.EXPLORE,
        adapter="huaan_pcf_archive_v1",
        git_revision="a" * 40,
        inputs=(InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),),
        rules=(Rule(metric="xml_identity_match_count", minimum=len(targets)),),
    )


def _page(code: str, day: str, *, linked_day: str | None = None) -> HTTPResult:
    compact = day.replace("-", "")
    link_day = (linked_day or day).replace("-", "")
    url = f"https://www.huaan.com.cn/etf/{code}/sgshqd.jsp?querydate={day}"
    body = (
        "<html><body>"
        f"<div>最新公告日期 {compact}</div><div>证券代码 {code}</div>"
        f'<a href="etffiledownload.jsp?etffilename=pcf_{code}_{link_day}.xml">下载</a>'
        "</body></html>"
    ).encode("gb18030")
    return HTTPResult(200, url, {"Content-Type": "text/html; charset=gb2312"}, body)


def _xml(code: str, day: str, *, xml_code: str | None = None, xml_day: str | None = None) -> bytes:
    trading_day = (xml_day or day).replace("-", "")
    security_id = xml_code or code
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<PCFFile xmlns="http://ts.szse.cn/Fund">'
        f"<SecurityID>{security_id}</SecurityID>"
        f"<TradingDay>{trading_day}</TradingDay>"
        "<RecordNum>2</RecordNum><TotalRecordNum>2</TotalRecordNum>"
        "<Components><Component><ComponentSecurityID>000001</ComponentSecurityID>"
        "</Component><Component><ComponentSecurityID>000002</ComponentSecurityID>"
        "</Component></Components></PCFFile>"
    ).encode()


def _download(code: str, day: str, body: bytes) -> HTTPResult:
    compact = day.replace("-", "")
    url = (
        f"https://www.huaan.com.cn/etf/{code}/etffiledownload.jsp"
        f"?etffilename=pcf_{code}_{compact}.xml"
    )
    return HTTPResult(
        200,
        url,
        {"Content-Type": "application/x-msdownload; charset=GB2312"},
        body,
    )


def _values(measured: object) -> dict[str, float]:
    return {item.name: item.value for item in measured.metrics}  # type: ignore[attr-defined]


def test_retrieves_pinned_huaan_historical_pages_and_validates_original_pcf_xml(
    tmp_path: Path,
) -> None:
    targets = [("159949", "2023-01-03"), ("159949", "2026-10-16")]
    responses: list[HTTPResult] = []
    for code, day in targets:
        responses.extend((_page(code, day), _download(code, day, _xml(code, day))))
    transport = StaticTransport(responses)

    measured = _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path, targets))

    values = _values(measured)
    assert measured.samples == 2
    assert values["page_http_200"] == 2
    assert values["page_link_match_count"] == 2
    assert values["xml_http_200"] == 2
    assert values["xml_identity_match_count"] == 2
    assert values["component_rows_valid_count"] == 2
    assert values["last_modified_present_count"] == 0
    assert len(transport.urls) == 4
    assert "does not establish pre-open availability" in " ".join(measured.details)


def test_does_not_fetch_an_xml_link_for_a_different_historical_date(tmp_path: Path) -> None:
    target = ("159949", "2023-01-03")
    transport = StaticTransport([_page(*target, linked_day="2023-01-04")])

    measured = _computer_type()(transport=transport).measure(
        tmp_path, _contract(tmp_path, [target])
    )

    values = _values(measured)
    assert values["page_link_match_count"] == 0
    assert values["xml_identity_match_count"] == 0
    assert len(transport.urls) == 1


def test_xml_must_match_both_pinned_fund_code_and_trading_day(tmp_path: Path) -> None:
    target = ("159949", "2023-01-03")
    transport = StaticTransport(
        [
            _page(*target),
            _download("159949", "2023-01-03", _xml("159949", "2023-01-03", xml_day="2023-01-04")),
        ]
    )

    measured = _computer_type()(transport=transport).measure(
        tmp_path, _contract(tmp_path, [target])
    )

    assert _values(measured)["xml_identity_match_count"] == 0


def test_rejects_unpinned_huaan_target_before_network_access(tmp_path: Path) -> None:
    transport = StaticTransport([])

    try:
        _computer_type()(transport=transport).measure(
            tmp_path, _contract(tmp_path, [("159949", "2020-11-30")])
        )
    except ValueError as error:
        assert "fixed Huaan" in str(error)
    else:
        raise AssertionError("an unpinned Huaan target was accepted")

    assert transport.urls == []


def test_page_date_header_changes_do_not_change_measurement(tmp_path: Path) -> None:
    target = ("159949", "2023-01-03")
    responses = [_page(*target), _download("159949", target[1], _xml(*target))]
    changed = [
        HTTPResult(
            item.status,
            item.final_url,
            {**item.headers, "Date": "Wed, 21 Oct 2026 16:59:00 GMT"},
            item.body,
        )
        for item in responses
    ]

    first = _computer_type()(transport=StaticTransport(responses)).measure(
        tmp_path, _contract(tmp_path, [target])
    )
    second = _computer_type()(transport=StaticTransport(changed)).measure(
        tmp_path, _contract(tmp_path, [target])
    )

    assert first == second


def test_measurement_rejects_redirected_final_url(tmp_path: Path) -> None:
    target = ("159949", "2023-01-03")
    page = _page(*target)
    redirected = HTTPResult(page.status, "https://example.com/redirected", page.headers, page.body)
    transport = StaticTransport([redirected])

    try:
        _computer_type()(transport=transport).measure(tmp_path, _contract(tmp_path, [target]))
    except ValueError as error:
        assert "redirect" in str(error).lower()
    else:
        raise AssertionError("redirected source response was accepted")
