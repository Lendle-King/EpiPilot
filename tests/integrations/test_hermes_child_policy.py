from __future__ import annotations

from pathlib import Path

import pytest

from epipilot.integrations.hermes.plugin import (
    HermesFrontendBridge,
    _make_pre_llm_hook,
    _make_pre_tool_hook,
)
from epipilot.integrations.hermes.runtime_contract import (
    EXECUTOR_CHILD_ENV,
    EXECUTOR_GUARD_TOOL,
    EXECUTOR_WORKSPACE_ENV,
)


class _State:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.values: dict[str, object] = {}

    def get(self, key: str, default: object | None = None) -> object:
        return self.values.get(key, default)

    def set(self, key: str, value: object) -> None:
        self.values[key] = value


def _hooks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setenv(EXECUTOR_CHILD_ENV, "1")
    monkeypatch.setenv(EXECUTOR_WORKSPACE_ENV, str(workspace.resolve()))
    bridge = HermesFrontendBridge(_State(tmp_path / "plugin-data"))
    return workspace, _make_pre_llm_hook(bridge), _make_pre_tool_hook(bridge)


def test_executor_child_context_does_not_require_frontend_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, pre_llm, _pre_tool = _hooks(tmp_path, monkeypatch)

    result = pre_llm(user_message="execute")

    assert isinstance(result, dict)
    assert "non-authoritative" in str(result.get("context"))


def test_executor_child_allows_contained_file_reads_and_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _pre_llm, pre_tool = _hooks(tmp_path, monkeypatch)
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    assert pre_tool(tool_name="read_file", args={"path": "src/main.py"}) is None
    assert pre_tool(tool_name="search_files", args={"pattern": "main", "path": "."}) is None
    assert pre_tool(tool_name=EXECUTOR_GUARD_TOOL, args={}) is None


def test_executor_child_blocks_terminal_escape_and_git_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _pre_llm, pre_tool = _hooks(tmp_path, monkeypatch)
    (workspace / ".git").mkdir()

    terminal = pre_tool(tool_name="terminal", args={"command": "rm -rf /"})
    outside = pre_tool(tool_name="write_file", args={"path": "../outside.txt", "content": "x"})
    git_metadata = pre_tool(tool_name="write_file", args={"path": ".git/config", "content": "x"})

    assert isinstance(terminal, dict) and terminal.get("action") == "block"
    assert isinstance(outside, dict) and outside.get("action") == "block"
    assert isinstance(git_metadata, dict) and git_metadata.get("action") == "block"


def test_executor_child_blocks_cross_profile_and_multifile_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _pre_llm, pre_tool = _hooks(tmp_path, monkeypatch)
    (workspace / "a.py").write_text("old\n", encoding="utf-8")

    cross_profile = pre_tool(
        tool_name="write_file",
        args={"path": "a.py", "content": "new", "cross_profile": True},
    )
    multi_patch = pre_tool(
        tool_name="patch",
        args={"mode": "patch", "patch": "*** Begin Patch"},
    )
    replace_patch = pre_tool(
        tool_name="patch",
        args={"mode": "replace", "path": "a.py", "old_string": "old", "new_string": "new"},
    )

    assert isinstance(cross_profile, dict) and cross_profile.get("action") == "block"
    assert isinstance(multi_patch, dict) and multi_patch.get("action") == "block"
    assert replace_patch is None


def test_executor_child_blocks_symlink_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _pre_llm, pre_tool = _hooks(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    result = pre_tool(tool_name="write_file", args={"path": "escape/payload.txt", "content": "x"})

    assert isinstance(result, dict)
    assert result.get("action") == "block"
