from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from epipilot.workspace.onboarding import WorkspaceStore


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _fixture_repo(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    (root / "docs").mkdir()
    (root / "README.md").write_text("# Fictional ETF Experiment\n", encoding="utf-8")
    (root / "docs" / "trial.md").write_text(
        "# Trial 1\n"
        "- SUCCESS: The GitHub workflow completed and generated a summary.\n"
        "- BLOCKED: Historical first-observed timestamps remain unverified.\n"
        "- DO NOT promote the result as forward portfolio performance.\n"
        "TUSHARE_TOKEN = dont-save-this-in-proposed-context\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "synthetic baseline")
    return root


def test_start_new_project_without_repository(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "workspace.sqlite", roots=(tmp_path,))
    item = store.create("Project A", "Find falsifiable hypotheses", "create")
    assert item["mode"] == "create"
    assert item["git"]["attached"] is False
    assert item["candidates"] == []
    assert item["events"][0]["kind"] == "project_created"
    assert len(store.projects()) == 1


def test_adopt_extracts_provenanced_notes_but_never_verified_evidence(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path / "research")
    store = WorkspaceStore(tmp_path / "workspace.sqlite", roots=(tmp_path,))
    item = store.create("ETF", "Diagnose alpha", "adopt", str(repo))
    assert item["git"]["attached"] is True
    notes = item["candidates"]
    assert len(notes) == 3
    assert any(note["kind"] == "lesson" for note in notes)
    assert any("BLOCKED" in note["text"] for note in notes)
    assert all(note["review"] == "pending" for note in notes)
    assert all(note["source_path"] == "docs/trial.md" for note in notes)
    assert all(len(note["source_sha256"]) == 64 for note in notes)
    assert all("verified" not in note for note in notes)
    assert not any("dont-save-this" in note["text"] for note in notes)
    assert "(no reviewed notes)" in store.chat_prompt(item["id"])

    reviewed = store.review(item["id"], notes[0]["id"], "accepted_note")
    assert reviewed["candidates"][0]["review"] == "accepted_note"
    assert "not verified scientific facts" in store.chat_prompt(item["id"])
    assert reviewed["events"][0]["kind"] == "candidate_review"
    assert store.get(item["id"])["git"]["changed_paths"] == 0


def test_adoption_is_pinned_to_commit_not_modified_worktree(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path / "research")
    (repo / "docs" / "trial.md").write_text(
        "A made-up SUCCESS from uncommitted research data should not appear.\n"
    )
    store = WorkspaceStore(tmp_path / "workspace.sqlite", roots=(tmp_path,))
    item = store.create("ETF", "Audit historical claims", "adopt", str(repo))
    assert len(item["candidates"]) == 3
    assert all("made-up" not in n["text"] for n in item["candidates"])
    assert item["git"]["changed_paths"] == 1


def test_multiple_projects_can_adopt_same_snapshot(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path / "research")
    store = WorkspaceStore(tmp_path / "workspace.sqlite", roots=(tmp_path,))
    first = store.create("A", "Research A", "adopt", str(repo))
    second = store.create("B", "Research B", "adopt", str(repo))
    assert len(first["candidates"]) == len(second["candidates"]) == 3


def test_intent_is_a_proposal_not_authoritative_plan_change(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "store.sqlite", roots=(tmp_path,))
    item = store.create("A", "Original goal", "create")
    later = store.intent(item["id"], "Prefer less GPU spend")
    assert later["goal"] == "Original goal"
    assert later["events"][0]["kind"] == "user_intent_proposed"
    assert later["events"][0]["payload"]["message"] == "Prefer less GPU spend"


def test_out_of_scope_and_invalid_review_rejected(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    outside = _fixture_repo(tmp_path / "outside")
    store = WorkspaceStore(tmp_path / "workspace.sqlite", roots=(root,))
    with pytest.raises(ValueError, match="allowed roots"):
        store.create("Outside", "Test scope", "adopt", str(outside))
    with pytest.raises(ValueError, match="requires"):
        store.create("Missing", "No repo", "adopt")
    with pytest.raises(ValueError, match="Use adoption"):
        store.create("Fresh", "Research", "create", str(outside))


def test_http_onboarding_and_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from epipilot.workspace.api import app

    repo = _fixture_repo(tmp_path / "research")
    monkeypatch.setenv("EPIPILOT_WORKSPACE_DB", str(tmp_path / "data.sqlite"))
    monkeypatch.setenv("EPIPILOT_ALLOWED_ROOTS", str(tmp_path))
    client = TestClient(app)
    assert client.get("/api/health").json()["status"] == "ok"
    response = client.post(
        "/api/projects",
        json={
            "mode": "adopt",
            "name": "ETF",
            "goal": "Study independent alpha",
            "repository_path": str(repo),
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["git"]["attached"]
    candidate = data["candidates"][0]["id"]
    changed = client.post(
        "/api/projects/" + data["id"] + "/candidates/" + candidate + "/review",
        json={"decision": "accepted_note"},
    )
    assert changed.status_code == 200
    assert changed.json()["events"][0]["kind"] == "candidate_review"
    assert client.get("/api/projects/" + data["id"] + "/chat-prompt").status_code == 200
    assert client.get("/api/projects/not-a-project").status_code == 404
    assert (
        client.post(
            "/api/projects/" + data["id"] + "/intent",
            json={"message": "Verify B3 original sources first"},
        ).status_code
        == 200
    )


def test_exploration_workspace_api_reuses_canonical_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from epipilot.research.models import ResearchGoal
    from epipilot.research.store import ResearchStore
    from epipilot.workspace.api import app

    root = _fixture_repo(tmp_path / "research")
    monkeypatch.setenv("EPIPILOT_WORKSPACE_DB", str(tmp_path / "workspace.sqlite"))
    monkeypatch.setenv("EPIPILOT_ALLOWED_ROOTS", str(tmp_path))
    client = TestClient(app)
    created = client.post(
        "/api/projects",
        json={
            "mode": "adopt",
            "name": "Probe",
            "goal": "Study failures",
            "repository_path": str(root),
        },
    )
    assert created.status_code == 201
    url = "/api/projects/" + created.json()["id"] + "/exploration"
    uninitialized = client.get(url)
    assert uninitialized.status_code == 200
    assert not uninitialized.json()["initialized"]
    assert client.post(
        url, json={"action": "enable", "reason": "Assess the problem first"}
    ).status_code == 400

    scientific = ResearchStore(root / ".research")
    scientific.add("goal_set", ResearchGoal(statement="Controlled experiments", budget=10))
    mode = client.post(
        url,
        json={"action": "enable", "reason": "Analyze the problem first", "budget_share": 0.2},
    )
    assert mode.status_code == 200
    assert mode.json()["readiness"]["stage"] == "exploration"
    assert mode.json()["readiness"]["exploration_budget_limit"] == 2
    assert scientific.load()[0].exploration is not None
    assert not scientific.load()[0].claims

    assert client.post(
        url, json={"action": "advance", "reason": "No diagnostics yet"}
    ).status_code == 400
    advanced = client.post(
        url,
        json={
            "action": "advance",
            "reason": "Explicitly accept optimization with uncertainty",
            "override": True,
        },
    )
    assert advanced.status_code == 200
    assert advanced.json()["readiness"]["stage"] == "optimization"
    assert client.post(
        url, json={"action": "reopen", "reason": "Reconsider before optimizing"}
    ).status_code == 200
    assert client.post(
        url, json={"action": "disable", "reason": "Stop exploratory gating"}
    ).status_code == 200
    assert not client.get(url).json()["readiness"]["enabled"]

    created_no_repo = client.post(
        "/api/projects",
        json={"mode": "create", "name": "Empty", "goal": "Blank project"},
    ).json()
    assert client.get("/api/projects/" + created_no_repo["id"] + "/exploration").status_code == 400
