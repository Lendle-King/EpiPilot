import contextlib
import io
import json
from pathlib import Path

import pytest
from test_agent_discovery import batch_for, make_agent


def test_cli_uses_same_canonical_database_and_discovery_protocol(tmp_path):
    from epipilot.research.agent_cli import main
    from epipilot.research.discovery import enable_frontier
    from epipilot.research.engine import ResearchEngine
    from epipilot.research.models import Cost, ResearchGoal
    from epipilot.runtime.sqlite_event_store import SqliteEventStore

    _, root, revision = make_agent(tmp_path)
    db = tmp_path / "actual.sqlite3"
    engine = ResearchEngine(SqliteEventStore(db), "agent", tmp_path / "out")
    engine.initialize(
        ResearchGoal(
            statement="Explicit synthetic research",
            budgets=(Cost(unit="wall_seconds", amount=30.0),),
        ),
        (),
        (),
    )
    enable_frontier(engine, root)
    batch = batch_for(engine, root, revision)
    source = tmp_path / "discovery.json"
    source.write_text(batch.model_dump_json(), encoding="utf8")

    def call(*args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(
                [
                    "--root",
                    str(root),
                    "--db",
                    str(db),
                    "--project",
                    "agent",
                    "--output",
                    str(tmp_path / "out"),
                    *args,
                ]
            )
        assert code == 0
        return json.loads(out.getvalue())

    packet = call("iterate")
    assert packet["action"] == "discovery_required"
    assert packet["authority"][0]["id"] == batch.basis_ref
    packet = call("discover", "--file", str(source))
    assert packet["action"] == "ready_to_execute"
    assert packet["selected_contract"]["proposal_id"] == "profile"
    assert packet["selected_proposal"]["id"] == "profile"
    assert packet["artifact_path"].endswith(".json")
    assert len(packet["questions"][0]["alternatives"]) == 2
    result = call("run")
    assert result["action"] == "discovery_required"
    assert result["questions"][0]["closed"] is True
    assert call("status")["budget"]["spent"]["wall_seconds"] > 0
    assert not (root / ".research").exists()


def test_cli_help_loads_managed_work_commands(capsys):
    from epipilot.research.agent_cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--project", "agent", "--help"])

    assert exc_info.value.code == 0
    assert "work" in capsys.readouterr().out


def test_validate_code_is_read_only_and_does_not_require_database(tmp_path, capsys):
    from epipilot.research.agent_cli import main

    _, root, _revision = make_agent(tmp_path)
    db = tmp_path / "not-created.sqlite3"
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert main(["--root", str(root), "--db", str(db), "--project", "agent", "validate-code"]) == 0
    result = json.loads(capsys.readouterr().out)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert result["revision"] == _revision
    assert result["clean"] is True
    assert result["changed_paths"] == []
    assert result["mergeable"] is False
    assert result["working_tree_clean"] is True
    assert result["ci_verified"] is False
    assert result["merge_ready"] is False
    assert result["complete_ci"] is False
    assert result["scientific_acceptance"] is False
    assert not db.exists()
    assert after == before


def test_validate_code_reports_porcelain_paths(tmp_path, capsys):
    from epipilot.research.agent_cli import main

    _, root, _revision = make_agent(tmp_path)
    changed = root / "data.csv"
    changed.write_text(changed.read_text(encoding="utf8") + "2022-01-01,1\n", encoding="utf8")
    assert main(["--root", str(root), "--project", "agent", "validate-code"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["clean"] is False
    assert result["mergeable"] is False
    assert result["changed_paths"] == ["data.csv"]


def test_default_artifacts_match_workspace_api_location(tmp_path, capsys):
    from epipilot.research.agent_cli import main
    from epipilot.research.discovery import apply_discovery
    from epipilot.research.engine import ResearchEngine, stable_id
    from epipilot.runtime.sqlite_event_store import SqliteEventStore

    _, root, revision = make_agent(tmp_path)
    db = tmp_path / "workspace.sqlite3"
    args = ["--root", str(root), "--db", str(db), "--project", "artifact-project"]
    assert main([*args, "init", "--goal", "Synthetic artifact protocol"]) == 0
    output = db.parent / "carts" / str(stable_id("artifact-project", "artifacts"))
    engine = ResearchEngine(SqliteEventStore(db), "artifact-project", output)
    apply_discovery(engine, root, batch_for(engine, root, revision))
    capsys.readouterr()
    assert main([*args, "iterate"]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert Path(packet["artifact_path"]).parent == output
