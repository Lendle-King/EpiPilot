from __future__ import annotations

import subprocess
import sys

import pytest

from epipilot.research.frontier import InputFile, MeasurementContract, Purpose, Rule
from epipilot.research.measurement import Measured
from epipilot.research.models import Metric
from epipilot.workspace.sse_announcement_archive_execution import (
    ADAPTER_ID,
    MAX_RUN_SECONDS,
    SubprocessSseAnnouncementArchiveComputer,
)


def _contract(adapter: str = ADAPTER_ID) -> MeasurementContract:
    return MeasurementContract(
        proposal_id="sse-announcement-r274-fixture",
        purpose=Purpose.EXPLORE,
        adapter=adapter,
        git_revision="a" * 40,
        inputs=(InputFile(path="data/archive_query.json", sha256="b" * 64),),
        rules=(Rule(metric="pcf_related_file_links_in_scope", minimum=1.0),),
    )


def test_sse_announcement_computer_runs_pinned_quant_runner_with_sanitized_env(
    tmp_path, monkeypatch
):
    calls: list[tuple[list[str], dict[str, object]]] = []
    measured = Measured(
        metrics=(Metric(name="pcf_related_file_links_in_scope", value=1.0),),
        samples=1,
        details=("response_sha256=" + "c" * 64,),
    )

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, "", "")
        assert args[1] == "-c"
        assert "SseAnnouncementArchiveRunner" in args[2]
        assert '"adapter":"quant_etf_sse_announcement_archive_v1"' in kwargs["input"]
        return subprocess.CompletedProcess(args, 0, measured.model_dump_json(), "")

    monkeypatch.setattr("epipilot.workspace.sse_announcement_archive_execution.subprocess.run", run)
    monkeypatch.setenv("PRIVATE_TEST_TOKEN", "must-not-be-forwarded")
    computer = SubprocessSseAnnouncementArchiveComputer(sys.executable, tmp_path / "epipilot")

    result = computer.measure(tmp_path, _contract())

    assert result == measured
    assert len(calls) == 2
    environment = calls[1][1]["env"]
    assert "PRIVATE_TEST_TOKEN" not in environment
    assert environment["PYTHONPATH"].split(":") == [
        str(tmp_path / "src"),
        str(tmp_path / "epipilot"),
    ]
    assert calls[1][1]["timeout"] == MAX_RUN_SECONDS


def test_sse_announcement_computer_rejects_wrong_adapter_before_subprocess(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected subprocess")

    monkeypatch.setattr(
        "epipilot.workspace.sse_announcement_archive_execution.subprocess.run", forbidden
    )
    computer = SubprocessSseAnnouncementArchiveComputer(sys.executable, tmp_path)

    with pytest.raises(ValueError, match="another SSE announcement adapter"):
        computer.measure(tmp_path, _contract("some_other_adapter"))


def test_sse_announcement_computer_fails_closed_when_pinned_code_changed(tmp_path, monkeypatch):
    def changed(args, **_kwargs):
        return subprocess.CompletedProcess(args, 1, "", "changed")

    monkeypatch.setattr(
        "epipilot.workspace.sse_announcement_archive_execution.subprocess.run", changed
    )
    computer = SubprocessSseAnnouncementArchiveComputer(sys.executable, tmp_path)

    with pytest.raises(ValueError, match="differs from its pinned revision"):
        computer.measure(tmp_path, _contract())
