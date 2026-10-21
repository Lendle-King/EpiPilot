from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.workspace.chinaamc_etf_archive import (
    ADAPTER_ID,
    ChinaAMCEtfArchiveComputer,
    ChinaAMCResponse,
)

FUND_CODE = "510050"
QUERY_DATE = "2020-11-30"
QUERY_URL = "https://www.chinaamc.com/front/front/out/etf/tradeList"
DOWNLOAD_URL = "https://www.chinaamc.com/front/front/out/etf/query/etfDownload"


def _download_body(*, code: str = FUND_CODE, day: str = "20201130") -> bytes:
    text = (
        '|\\\\\\<ETFMaster Version="2.1">\\\\\\|\n'
        f"|01|            |510051|{code}|D890728191|62055|{day}|20201127|\n"
        "|\\\\\\</ETFMaster>\\\\\\|\n"
        '|\\\\\\<ETFConstituent Version="2.1">\\\\\\|\n'
        "|01|600000|100|\n"
        "|\\\\\\</ETFConstituent>\\\\\\|\n"
    )
    return text.encode("gb18030")


def _query_body(*, code: str = FUND_CODE, day: str = QUERY_DATE, rows: int = 50) -> bytes:
    payload = {
        "status": 1,
        "message": "请求成功",
        "data": {
            "baseInfoContent": [
                {"label": "最新公告日期", "value": day},
                {"label": "基金代码", "value": code},
            ],
            "fileName": "fm001etfd20201130001.txt",
            "year": "2020",
            "configType": "txt",
            "stockResponseList": [
                {"stockCode": f"{600000 + index:06d}", "stockName": f"stock-{index}"}
                for index in range(rows)
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _contract(root: Path) -> MeasurementContract:
    manifest = {
        "schema": "chinaamc-etf-archive-targets/v1",
        "targets": [{"fund_code": FUND_CODE, "query_date": QUERY_DATE}],
    }
    path = root / "targets.json"
    content = json.dumps(manifest, separators=(",", ":")).encode()
    path.write_bytes(content)
    return MeasurementContract(
        proposal_id="chinaamc-etf-archive-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        boundary_date=QUERY_DATE,
        cutoff_date=QUERY_DATE,
        inputs=(InputFile(path="targets.json", sha256=hashlib.sha256(content).hexdigest()),),
        rules=(Rule(metric="verified_code_date_downloads", minimum=1),),
    )


def _response(
    url: str,
    body: bytes,
    *,
    status: int = 200,
    final_url: str | None = None,
    headers: dict[str, str] | None = None,
) -> ChinaAMCResponse:
    return ChinaAMCResponse(
        status=status,
        final_url=final_url or url,
        headers=headers or {"content-type": "application/json"},
        body=body,
    )


def _metrics(measured: object) -> dict[str, float]:
    return {metric.name: metric.value for metric in measured.metrics}  # type: ignore[attr-defined]


def test_public_historical_query_and_download_verify_exact_fund_date(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    calls: list[tuple[str, dict[str, str]]] = []

    def fetch(url: str, fields: dict[str, str], _timeout: float, _max_bytes: int):
        calls.append((url, fields))
        if url == QUERY_URL:
            return _response(url, _query_body())
        assert url == DOWNLOAD_URL
        return _response(
            url, _download_body(), headers={"content-type": "application/octet-stream"}
        )

    measured = ChinaAMCEtfArchiveComputer(fetcher=fetch).measure(tmp_path, contract)

    values = _metrics(measured)
    assert measured.samples == 1
    assert values["query_success"] == 1
    assert values["query_code_matches"] == 1
    assert values["query_date_matches"] == 1
    assert values["query_component_rows"] == 50
    assert values["download_code_date_matches"] == 1
    assert values["verified_code_date_downloads"] == 1
    assert calls == [
        (QUERY_URL, {"fundCode": FUND_CODE, "queryDate": QUERY_DATE, "instType": ""}),
        (
            DOWNLOAD_URL,
            {"fileName": "fm001etfd20201130001.txt", "year": "2020", "fundCode": FUND_CODE},
        ),
    ]
    assert all(urlparse(url).hostname == "www.chinaamc.com" for url, _ in calls)


def test_empty_historical_query_is_a_zero_observation_without_download(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str, _fields: dict[str, str], _timeout: float, _max_bytes: int):
        calls.append(url)
        return _response(url, '{"status":0,"message":"暂无内容","data":null}'.encode())

    measured = ChinaAMCEtfArchiveComputer(fetcher=fetch).measure(tmp_path, contract)

    values = _metrics(measured)
    assert measured.samples == 1
    assert values["query_success"] == 0
    assert values["query_date_matches"] == 0
    assert values["verified_code_date_downloads"] == 0
    assert calls == [QUERY_URL]


def test_query_with_wrong_announcement_date_is_not_downloaded(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    calls: list[str] = []

    def fetch(url: str, _fields: dict[str, str], _timeout: float, _max_bytes: int):
        calls.append(url)
        return _response(url, _query_body(day="2020-11-27"))

    measured = ChinaAMCEtfArchiveComputer(fetcher=fetch).measure(tmp_path, contract)

    values = _metrics(measured)
    assert values["query_success"] == 1
    assert values["query_date_matches"] == 0
    assert values["verified_code_date_downloads"] == 0
    assert calls == [QUERY_URL]


def test_download_with_wrong_code_or_date_is_not_a_witness(tmp_path: Path) -> None:
    contract = _contract(tmp_path)

    def fetch(url: str, _fields: dict[str, str], _timeout: float, _max_bytes: int):
        body = _query_body() if url == QUERY_URL else _download_body(code="510300", day="20201129")
        return _response(url, body)

    measured = ChinaAMCEtfArchiveComputer(fetcher=fetch).measure(tmp_path, contract)

    values = _metrics(measured)
    assert values["query_date_matches"] == 1
    assert values["download_http_200"] == 1
    assert values["download_code_date_matches"] == 0
    assert values["verified_code_date_downloads"] == 0


def test_download_with_invalid_gb18030_bytes_is_not_a_witness(tmp_path: Path) -> None:
    contract = _contract(tmp_path)

    def fetch(url: str, _fields: dict[str, str], _timeout: float, _max_bytes: int):
        body = _query_body() if url == QUERY_URL else b"\x81"
        return _response(url, body)

    measured = ChinaAMCEtfArchiveComputer(fetcher=fetch).measure(tmp_path, contract)

    values = _metrics(measured)
    assert values["download_http_200"] == 1
    assert values["download_decode_success"] == 0
    assert values["verified_code_date_downloads"] == 0


def test_manifest_rejects_unpinned_fund_or_date(tmp_path: Path) -> None:
    _contract(tmp_path)
    manifest_path = tmp_path / "targets.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "chinaamc-etf-archive-targets/v1",
                "targets": [{"fund_code": "510300", "query_date": QUERY_DATE}],
            }
        ),
        encoding="utf-8",
    )
    raw = manifest_path.read_bytes()
    contract = MeasurementContract(
        proposal_id="chinaamc-etf-archive-test",
        purpose=Purpose.EXPLORE,
        adapter=ADAPTER_ID,
        git_revision="a" * 40,
        boundary_date=QUERY_DATE,
        cutoff_date=QUERY_DATE,
        inputs=(InputFile(path="targets.json", sha256=hashlib.sha256(raw).hexdigest()),),
        rules=(Rule(metric="verified_code_date_downloads", minimum=1),),
    )

    with pytest.raises(ValueError, match="fixed ChinaAMC"):
        ChinaAMCEtfArchiveComputer(fetcher=lambda *_args: pytest.fail("must not fetch")).measure(
            tmp_path, contract
        )
