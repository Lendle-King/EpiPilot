"""Research Onboarding Agent: model-independent scientific extraction contract tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from epipilot.workspace.onboarding import WorkspaceStore
from epipilot.workspace.research_agent import (
    ClaimBatch,
    GroundedClaim,
    ResearchOnboardingAgent,
    reconcile,
    snapshot_documents,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(root: Path) -> Path:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixtures@example.invalid")
    _git(root, "config", "user.name", "Research Fixtures")
    (root / "docs").mkdir()
    (root / "docs" / "trial_20261013.md").write_text(
        "# Baseline\n"
        "GitHub Actions SUCCESS: workflow completed but alpha was not qualified.\n"
        "Scientific verdict: PIT source remains BLOCKED due to missing original filings.\n"
        "DO NOT promote earnings alpha before primary-source provenance is checked.\n",
        encoding="utf-8",
    )
    (root / "docs" / "trial_20261014.md").write_text(
        "# New attempt\n"
        "GitHub Actions SUCCESS: workflow completed but alpha was not qualified.\n"
        "New original filing evidence QUALIFIES PIT data for prospective testing.\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "synthetic research results")
    return root


def _claim(
    proposition: str,
    topic: str,
    stance: str,
    line: int,
    quote: str,
    *,
    kind: str = "observation",
    execution: str = "completed",
    scientific: str = "blocked",
) -> GroundedClaim:
    return GroundedClaim.model_validate(
        {
            "proposition": proposition,
            "topic_key": topic,
            "stance": stance,
            "source_line": line,
            "exact_quote": quote,
            "kind": kind,
            "execution_status": execution,
            "scientific_status": scientific,
            "caveat": "Historical data is not equivalent to qualified forward returns.",
        }
    )


class FakeScientificModel:
    """Deterministic synthetic semantic extractor; never calls a model service."""

    def extract(self, prompt: str) -> ClaimBatch:
        common = _claim(
            "Workflow completed; that does not prove a profitable alpha",
            "alpha_qualification",
            "neutral",
            2,
            "GitHub Actions SUCCESS: workflow completed but alpha was not qualified.",
        )
        if "trial_20261014.md" in prompt:
            return ClaimBatch(
                claims=[
                    common,
                    _claim(
                        "PIT qualification appears supported in the later research report",
                        "pit_data_qualification",
                        "supports",
                        3,
                        "New original filing evidence QUALIFIES PIT data for prospective testing.",
                        scientific="supported",
                    ),
                ]
            )
        return ClaimBatch(
            claims=[
                common,
                _claim(
                    "PIT historical source validation is still blocked",
                    "pit_data_qualification",
                    "contradicts",
                    3,
                    "Scientific verdict: PIT source remains BLOCKED due to missing original filings.",
                ),
            ]
        )


def _setup(tmp_path: Path) -> tuple[WorkspaceStore, str]:
    repo = _repo(tmp_path / "repo")
    store = WorkspaceStore(tmp_path / "workspace.sqlite3", roots=(tmp_path,))
    project = store.create("Synthetic alpha", "Validate PIT information", "adopt", str(repo))
    return store, str(project["id"])


def test_semantic_agent_deduplicates_and_exposes_temporal_conflict(tmp_path: Path) -> None:
    store, project_id = _setup(tmp_path)
    agent = ResearchOnboardingAgent(store)
    result = agent.run_local(project_id, FakeScientificModel(), "fake-local", 2)
    assert result["available"] is True
    assert result["raw_claim_count"] == 4
    assert result["distinct_claim_count"] == 3
    assert len(result["potential_conflicts"]) == 1
    assert result["potential_conflicts"][0]["topic_key"] == "pit_data_qualification"
    assert not result["independently_verified"]
    common = next(c for c in result["claims"] if c["topic_key"] == "alpha_qualification")
    assert len(common["citations"]) == 2
    assert common["execution_status"] == "completed"
    assert common["scientific_status"] == "blocked"
    assert all(c["review"] == "pending" for c in result["claims"])


def test_rejects_hallucinated_quotes_without_partial_persistence(tmp_path: Path) -> None:
    store, project_id = _setup(tmp_path)
    agent = ResearchOnboardingAgent(store)
    repo = Path(store.get(project_id)["repository_path"])
    commit = str(store.get(project_id)["git_commit"])
    docs = snapshot_documents(repo, commit, 2)
    fabricated = _claim(
        "This result is fabricated and cannot be traced to a source line",
        "fabricated_claim",
        "supports",
        2,
        "This invented statement never appeared anywhere in the document.",
    )
    with pytest.raises(ValueError, match="Ungrounded"):
        reconcile([(docs[0], ClaimBatch(claims=[fabricated]))])
    assert agent.report(project_id)["available"] is False


def test_chatgpt_manual_batches_validate_merge_and_review(tmp_path: Path) -> None:
    store, project_id = _setup(tmp_path)
    agent = ResearchOnboardingAgent(store)
    docs = agent.chat_packet(project_id, 2)["documents"]
    assert len(docs) == 2
    for doc in docs:
        prompt = str(doc["numbered_text"])
        batch = FakeScientificModel().extract("Document: " + str(doc["path"]) + "\n" + prompt)
        result = agent.import_chat_batch(project_id, str(doc["path"]), batch.model_dump_json())
    assert result["distinct_claim_count"] == 3
    assert result["raw_claim_count"] == 4
    first_id = result["claims"][0]["id"]
    reviewed = agent.review(project_id, first_id, "accepted_note")
    assert any(c["id"] == first_id and c["review"] == "accepted_note" for c in reviewed["claims"])
    assert not reviewed["independently_verified"]
    with pytest.raises(KeyError):
        agent.review(project_id, "nonexistent", "accepted_note")
    with pytest.raises(ValueError):
        agent.import_chat_batch(project_id, "other.md", '{"claims":[]}')
    assert len(
        [e for e in store.get(project_id)["events"] if e["kind"] == "semantic_claim_review"]
    ) == 1


def test_existing_repo_snapshot_is_immutable_during_analysis(tmp_path: Path) -> None:
    store, project_id = _setup(tmp_path)
    item = store.get(project_id)
    repo = Path(item["repository_path"])
    (repo / "docs" / "trial_20261014.md").write_text(
        "Fake success: all alpha backtests are newly successful.\n", encoding="utf-8"
    )
    result = ResearchOnboardingAgent(store).run_local(
        project_id, FakeScientificModel(), "synthetic", 2
    )
    assert result["distinct_claim_count"] == 3
    assert store.get(project_id)["git"]["changed_paths"] == 1


def test_http_semantic_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from epipilot.workspace.api import app

    store, pid = _setup(tmp_path)
    monkeypatch.setenv("EPIPILOT_WORKSPACE_DB", str(store.path))
    monkeypatch.setenv("EPIPILOT_ALLOWED_ROOTS", str(tmp_path))
    client = TestClient(app)
    response = client.get("/api/projects/" + pid + "/research-agent/chat-packet")
    assert response.status_code == 200
    documents = response.json()["documents"]
    assert len(documents) == 2
    assert client.get("/api/projects/" + pid + "/research-agent").json()["available"] is False
    doc = documents[0]
    batch = FakeScientificModel().extract(str(doc["path"]) + "\n" + str(doc["numbered_text"]))
    imported = client.post(
        "/api/projects/" + pid + "/research-agent/chat-import",
        json={"source_path": doc["path"], "response_json": batch.model_dump_json()},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["distinct_claim_count"] == 2
    claim_id = imported.json()["claims"][0]["id"]
    reviewed = client.post(
        "/api/projects/" + pid + "/research-agent/claims/" + claim_id + "/review",
        json={"decision": "accepted_note"},
    )
    assert reviewed.status_code == 200
    assert any(c["review"] == "accepted_note" for c in reviewed.json()["claims"])
    forged = json.dumps(
        {
            "claims": [
                {
                    **batch.claims[0].model_dump(),
                    "exact_quote": "fabricated citation which is not real",
                }
            ]
        }
    )
    failure = client.post(
        "/api/projects/" + pid + "/research-agent/chat-import",
        json={"source_path": doc["path"], "response_json": forged},
    )
    assert failure.status_code == 400
    assert len(agent_reports(store, pid)) == 1


def agent_reports(store: WorkspaceStore, project_id: str) -> list[object]:
    with store._db() as conn:
        return list(
            conn.execute("SELECT run_id FROM semantic_onboarding WHERE project_id=?", (project_id,))
        )
