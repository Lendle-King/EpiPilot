"""Project-local Codex/Kimi installation tests with no external agent processes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from epipilot.integrations.agent_installer import (
    AGENTS_PATH,
    END,
    SKILL_PATH,
    START,
    install,
    main,
)


@pytest.mark.parametrize("agent", ["codex", "kimi", "both"])
def test_install_agent_skill_and_idempotent(tmp_path: Path, agent: str) -> None:
    result = install(tmp_path, agent=agent)
    assert result["ok"]
    skill = tmp_path / SKILL_PATH
    assert skill.exists()
    assert "EpiPilot Research Skill" in skill.read_text()
    agents = tmp_path / AGENTS_PATH
    assert agents.exists() == (agent in {"kimi", "both"})
    if agents.exists():
        text = agents.read_text()
        assert text.count(START) == text.count(END) == 1
    repeated = install(tmp_path, agent=agent)
    assert repeated["changed_or_pending"] == []
    assert repeated["unchanged"]


def test_preserve_existing_agents_instructions(tmp_path: Path) -> None:
    path = tmp_path / AGENTS_PATH
    path.write_text("# Existing\nKeep private data private.\n", encoding="utf-8")
    install(tmp_path, agent="kimi")
    assert path.read_text().startswith("# Existing\nKeep private data private.\n")
    before = path.read_text()
    install(tmp_path, agent="both")
    assert path.read_text() == before


def test_dry_run_makes_no_files(tmp_path: Path) -> None:
    outcome = install(tmp_path, agent="both", dry_run=True)
    assert len(outcome["changed_or_pending"]) == 2
    assert not (tmp_path / SKILL_PATH).exists()
    assert not (tmp_path / AGENTS_PATH).exists()


def test_refuse_overwrite_modified_skill_without_force(tmp_path: Path) -> None:
    file = tmp_path / SKILL_PATH
    file.parent.mkdir(parents=True)
    file.write_text("unrelated user instructions", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        install(tmp_path, agent="both")
    assert file.read_text() == "unrelated user instructions"
    assert not (tmp_path / AGENTS_PATH).exists()
    install(tmp_path, agent="codex", force=True)
    assert "EpiPilot Research Skill" in file.read_text()


def test_refuse_path_escape_via_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "project"
    root.mkdir()
    (root / ".agents").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match=r"symlink|escapes"):
        install(root, agent="codex")
    assert not (outside / "skills").exists()


def test_invalid_agents_markers_fail_closed_without_partial_change(tmp_path: Path) -> None:
    agents = tmp_path / AGENTS_PATH
    agents.write_text("## Existing\n" + START + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unbalanced"):
        install(tmp_path, agent="kimi")
    assert not (tmp_path / SKILL_PATH).exists()


def test_cli_json_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--project", str(tmp_path), "--agent", "both", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["native_codex_marketplace_plugin"] is False
    assert len(result["changed_or_pending"]) == 2
    assert main(["--project", str(tmp_path), "--agent", "both", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["changed_or_pending"] == []
