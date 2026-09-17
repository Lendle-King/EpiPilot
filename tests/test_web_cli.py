from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from epipilot.web.__main__ import main
from epipilot.web.service import WorkbenchService
from test_web_repository import make_repo


def test_cli_reports_invalid_event_database_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = make_repo(tmp_path / "repo")
    monkeypatch.setattr(sys, "argv", ["epipilot.web", "--repo", str(repo)])

    def broken_snapshot(self: WorkbenchService) -> None:
        raise sqlite3.DatabaseError("sensitive database detail")

    monkeypatch.setattr(WorkbenchService, "snapshot", broken_snapshot)
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    output = capsys.readouterr().err
    assert "Unable to load project data" in output
    assert "sensitive database detail" not in output
