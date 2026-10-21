from pathlib import Path

from fastapi.testclient import TestClient
from test_engine import Runner, Verifier, setup

from epipilot.runtime.event_store import InMemoryEventStore
from epipilot.workspace import api


def test_configured_project_uses_carts_in_existing_loop_endpoint(tmp_path, monkeypatch):
    engine = setup(InMemoryEventStore(), tmp_path)
    monkeypatch.setattr(api, "_carts_engine", lambda project: engine)
    monkeypatch.setattr(api, "_carts_adapter", lambda project: Both())

    class Both(Runner, Verifier):
        def __init__(self):
            Runner.__init__(self)
            Verifier.__init__(self)

    client = TestClient(api.app)
    result = client.post("/api/projects/project/research-agent/loop", json={"max_rounds": 10})
    assert result.status_code == 200
    assert result.json()["rounds_completed"] == 1
    assert client.get("/api/projects/project/research-agent/loop").json()["scheduler"] == "carts"


def test_agent_frontier_dispatches_registered_adapter_and_requires_discovery(tmp_path, monkeypatch):
    from test_agent_cycle import discovered

    engine, _root, _batch = discovered(tmp_path)
    monkeypatch.setattr(api, "_carts_engine", lambda project: engine)

    def forbidden_adapter(project):
        raise AssertionError("ETF adapter must not execute agent-native contracts")

    monkeypatch.setattr(api, "_carts_adapter", forbidden_adapter)
    client = TestClient(api.app)
    response = client.post("/api/projects/agent/research-tree/run", json={"max_rounds": 10})
    assert response.status_code == 200
    assert response.json()["agent"]["action"] == "discovery_required"
    assert len(engine.state.research.reviews) == 1
    assert client.get("/api/projects/agent/research-agent/loop").json()["agent"]["questions"][0][
        "closed"
    ]


def test_agent_service_registers_configured_etf_algorithm_computers(tmp_path, monkeypatch):
    import sys

    from test_agent_cycle import discovered

    from epipilot.workspace.algorithm_execution import LocalAlgorithmExecutor
    from epipilot.workspace.phase_execution import (
        EXPOSURE_SENSITIVITY_ADAPTER_ID,
        PHASE_COMPARISON_ADAPTER_ID,
        SubprocessExposureSensitivityComputer,
        SubprocessPhaseComparisonComputer,
    )
    from epipilot.workspace.sse_announcement_archive_execution import (
        ADAPTER_ID as SSE_ANNOUNCEMENT_ARCHIVE_ADAPTER_ID,
    )
    from epipilot.workspace.sse_announcement_archive_execution import (
        SubprocessSseAnnouncementArchiveComputer,
    )

    engine, _root, _batch = discovered(tmp_path)
    monkeypatch.setattr(
        api,
        "_local_algorithm_executor",
        lambda repository: LocalAlgorithmExecutor(repository, Path(sys.executable)),
    )

    service = api._agent_service(engine)

    assert "missingness" in service.adapter.computers
    assert "series" in service.adapter.computers
    assert "integrity" in service.adapter.computers
    assert "chinaamc_etf_archive_v1" in service.adapter.computers
    assert SSE_ANNOUNCEMENT_ARCHIVE_ADAPTER_ID in service.adapter.computers
    assert isinstance(
        service.adapter.computers[SSE_ANNOUNCEMENT_ARCHIVE_ADAPTER_ID],
        SubprocessSseAnnouncementArchiveComputer,
    )
    from epipilot.workspace.chinaamc_etf_archive import ChinaAMCEtfArchiveComputer

    assert isinstance(
        service.adapter.computers["chinaamc_etf_archive_v1"], ChinaAMCEtfArchiveComputer
    )
    assert "quant_etf_dual_momentum_126trend_135vol_v1" in service.adapter.computers
    assert "sse_lifecycle_archive_v1" in service.adapter.computers
    assert "commoncrawl_sse_index_notice_v2" in service.adapter.computers
    assert "wayback_sse_index_notice_v3" in service.adapter.computers
    assert "wayback_sse_pcf_archive_v1" in service.adapter.computers
    assert "commoncrawl_sse_pcf_v1" in service.adapter.computers
    assert "commoncrawl_sse_pcf_v2" in service.adapter.computers
    assert "commoncrawl_sse_pcf_v3" in service.adapter.computers
    assert "szse_pcf_wayback_archive_v1" in service.adapter.computers
    assert "arquivo_szse_pcf_v1" in service.adapter.computers
    from epipilot.workspace.arquivo_szse_pcf import ArquivoSZSEPCFComputer

    assert isinstance(service.adapter.computers["arquivo_szse_pcf_v1"], ArquivoSZSEPCFComputer)
    from epipilot.workspace.wayback_sse_pcf_archive import WaybackSSEPCFArchiveComputer

    assert isinstance(
        service.adapter.computers["wayback_sse_pcf_archive_v1"],
        WaybackSSEPCFArchiveComputer,
    )
    from epipilot.workspace.commoncrawl_sse_pcf_archive import (
        CommonCrawlSSEPCFArchiveComputer,
    )

    assert isinstance(
        service.adapter.computers["commoncrawl_sse_pcf_v1"],
        CommonCrawlSSEPCFArchiveComputer,
    )
    from epipilot.workspace.commoncrawl_sse_pcf_archive import (
        CommonCrawlSSEPCFLongIndexTimeoutComputer,
    )

    assert isinstance(
        service.adapter.computers["commoncrawl_sse_pcf_v2"],
        CommonCrawlSSEPCFLongIndexTimeoutComputer,
    )
    from epipilot.workspace.commoncrawl_sse_pcf_archive import (
        CommonCrawlSSEPCFStableLongIndexTimeoutComputer,
    )

    assert isinstance(
        service.adapter.computers["commoncrawl_sse_pcf_v3"],
        CommonCrawlSSEPCFStableLongIndexTimeoutComputer,
    )
    assert "commoncrawl_szse_pcf_v1" in service.adapter.computers
    assert "commoncrawl_szse_pcf_v2" in service.adapter.computers
    assert "commoncrawl_szse_pcf_v3" in service.adapter.computers
    assert isinstance(
        service.adapter.computers[PHASE_COMPARISON_ADAPTER_ID],
        SubprocessPhaseComparisonComputer,
    )
    assert isinstance(
        service.adapter.computers[EXPOSURE_SENSITIVITY_ADAPTER_ID],
        SubprocessExposureSensitivityComputer,
    )
