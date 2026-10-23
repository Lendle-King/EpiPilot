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
                    "Scientific verdict: PIT source remains BLOCKED due to missing "
                    "original filings.",
                ),
            ]
        )


def _setup(tmp_path: Path) -> tuple[WorkspaceStore, str]:
    repo = _repo(tmp_path / "repo")
    store = WorkspaceStore(tmp_path / "workspace.sqlite3", roots=(tmp_path,))
    project = store.create("Synthetic alpha", "Validate PIT information", "adopt", str(repo))
    return store, str(project["id"])


def test_extraction_prompt_injects_goal_and_requires_chinese_output(tmp_path: Path) -> None:
    from epipilot.workspace.research_agent import GUIDANCE, extraction_prompt

    store, project_id = _setup(tmp_path)
    item = store.get(project_id)
    docs = snapshot_documents(Path(str(item["repository_path"])), str(item["git_commit"]))
    assert docs, "fixture snapshot must expose at least one document"

    prompt = extraction_prompt(docs[0], "研究目标: 验证策略表现")
    assert "研究目标: 验证策略表现" in prompt
    assert "Research goal (context, NOT evidence)" in prompt
    assert "Prefer lines that inform the" in prompt
    assert "data environment's properties" in prompt
    assert "methodological lessons" in prompt

    assert "Simplified Chinese" in GUIDANCE
    assert "research goal itself" in GUIDANCE
    assert "verbatim in its source language" in GUIDANCE
    assert "data environment itself" in GUIDANCE
    assert "data_" in GUIDANCE
    assert "steer future research routes" in GUIDANCE
    assert "starts with market_" in GUIDANCE or "prefixed with market_" in GUIDANCE
    assert "guide algorithm selection" in GUIDANCE
    assert "market_regime_persistence" in GUIDANCE
    assert "do not bury market-level" in GUIDANCE
    assert "topic_key prefixed with market_" in prompt
    assert "Market extraction requirement" in prompt


def test_snapshot_documents_prioritizes_the_current_round_report(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    report_path = repo / "docs" / "auto_iteration" / "round_10_20261017_183939.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        "# Round 10\nThe current route remains blocked pending a reproducible data panel.\n",
        encoding="utf-8",
    )
    for index in range(20):
        (repo / "docs" / f"data_source_{index:02d}_20261018.md").write_text(
            f"# Source {index}\nA bounded data-readiness observation {index}.\n",
            encoding="utf-8",
        )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add current round report and source notes")
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    documents = snapshot_documents(
        repo,
        commit,
        12,
        priority_paths=("docs/auto_iteration/round_10_20261017_183939.md",),
    )

    assert documents[0].path == "docs/auto_iteration/round_10_20261017_183939.md"
    assert len(documents) == 12


def test_semantic_agent_deduplicates_and_exposes_temporal_conflict(tmp_path: Path) -> None:
    store, project_id = _setup(tmp_path)
    agent = ResearchOnboardingAgent(store)
    result = agent.run_model(project_id, FakeScientificModel(), "fake-local", 2)
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


_GOOD_QUOTE = "GitHub Actions SUCCESS: workflow completed but alpha was not qualified."


@pytest.mark.parametrize("padding", [" ", "\t"])
def test_manual_import_rejects_added_quote_whitespace(tmp_path: Path, padding: str) -> None:
    store, project_id = _setup(tmp_path)
    agent = ResearchOnboardingAgent(store)
    good = _claim(
        "An execution success is not evidence of qualified alpha",
        "alpha_test",
        "neutral",
        2,
        _GOOD_QUOTE,
    )
    bad = good.model_copy(update={"exact_quote": padding + _GOOD_QUOTE})
    batch = ClaimBatch(claims=[good, bad])
    with pytest.raises(ValueError, match="Ungrounded"):
        agent.import_chat_batch(project_id, "docs/trial_20261014.md", batch.model_dump_json())
    assert agent.report(project_id)["available"] is False


class ScriptedModel:
    """Deterministic per-call batches to exercise retry and isolation paths."""

    def __init__(self, responses: list[ClaimBatch]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def extract(self, prompt: str) -> ClaimBatch:
        self.prompts.append(prompt)
        return self._responses.pop(0)


def _good_batch() -> ClaimBatch:
    return ClaimBatch(
        claims=[
            _claim(
                "完成的工作流执行不等同于策略科学验证",
                "alpha_qualification",
                "neutral",
                2,
                _GOOD_QUOTE,
            )
        ]
    )


def _bad_batch() -> ClaimBatch:
    return ClaimBatch(
        claims=[
            _claim(
                "This claim uses a hallucinated source quotation",
                "alpha_qualification",
                "neutral",
                2,
                "THIS QUOTE DOES NOT EXIST IN THE DOCUMENT",
            )
        ]
    )


class _AlwaysBad:
    def extract(self, prompt: str) -> ClaimBatch:
        return _bad_batch()


def test_run_model_retries_grounding_failure_with_corrective_prompt(
    tmp_path: Path,
) -> None:
    store, project_id = _setup(tmp_path)
    model = ScriptedModel([_bad_batch(), _good_batch(), _good_batch()])
    result = ResearchOnboardingAgent(store).run_model(project_id, model, "fake", 2)
    assert result["rejected_documents"] == []
    assert result["distinct_claim_count"] >= 1
    assert len(model.prompts) == 3  # one retry for the first document
    assert "CORRECTIVE CONSTRAINT" not in model.prompts[0]
    assert "CORRECTIVE CONSTRAINT" in model.prompts[1]
    assert "CORRECTIVE CONSTRAINT" not in model.prompts[2]


def test_run_model_uses_minimal_grounding_fallback_after_two_failures(
    tmp_path: Path,
) -> None:
    store, project_id = _setup(tmp_path)
    model = ScriptedModel([_bad_batch(), _bad_batch(), _good_batch(), _good_batch()])

    result = ResearchOnboardingAgent(store).run_model(project_id, model, "fake", 2)

    assert result["rejected_documents"] == []
    assert result["distinct_claim_count"] >= 1
    assert len(model.prompts) == 4
    assert "CORRECTIVE CONSTRAINT" in model.prompts[1]
    assert "MINIMAL GROUNDED FALLBACK" in model.prompts[2]
    assert "MINIMAL GROUNDED FALLBACK" not in model.prompts[3]


def test_run_model_rejects_only_the_ungrounded_document(tmp_path: Path) -> None:
    store, project_id = _setup(tmp_path)

    class Selective:
        def extract(self, prompt: str) -> ClaimBatch:
            return _bad_batch() if "trial_20261014" in prompt else _good_batch()

    result = ResearchOnboardingAgent(store).run_model(project_id, Selective(), "fake", 2)
    rejected = result["rejected_documents"]
    assert isinstance(rejected, list)
    assert [entry["path"] for entry in rejected] == ["docs/trial_20261014.md"]
    assert result["distinct_claim_count"] == 1
    claim = result["claims"][0]
    assert claim["citations"][0]["source_path"] == "docs/trial_20261013.md"


def test_run_model_fails_closed_when_no_batch_grounds(tmp_path: Path) -> None:
    store, project_id = _setup(tmp_path)
    with pytest.raises(ValueError, match="No document produced"):
        ResearchOnboardingAgent(store).run_model(project_id, _AlwaysBad(), "fake", 2)


def test_run_model_rejects_an_empty_claim_batch(tmp_path: Path) -> None:
    store, project_id = _setup(tmp_path)

    class Empty:
        def extract(self, prompt: str) -> ClaimBatch:
            del prompt
            return ClaimBatch(claims=[])

    with pytest.raises(ValueError, match="No document produced"):
        ResearchOnboardingAgent(store).run_model(project_id, Empty(), "fake", 1)
    report = ResearchOnboardingAgent(store).report(project_id)
    assert report["distinct_claim_count"] == 0
    assert [entry["path"] for entry in report["unresolved_rejected_documents"]] == [
        "docs/trial_20261014.md"
    ]


def test_manual_grounded_import_resolves_active_rejection_without_erasing_history(
    tmp_path: Path,
) -> None:
    store, project_id = _setup(tmp_path)
    agent = ResearchOnboardingAgent(store)
    with pytest.raises(ValueError, match="No document produced"):
        agent.run_model(project_id, _AlwaysBad(), "fake", 1)

    result = agent.import_chat_batch(
        project_id,
        "docs/trial_20261014.md",
        _good_batch().model_dump_json(),
    )
    assert result["distinct_claim_count"] == 1
    assert result["rejected_documents"]
    assert result["unresolved_rejected_documents"] == []

    # A later refresh must reuse the manually grounded batch instead of asking
    # the model to invalidate it again.
    refreshed = agent.run_model(project_id, _AlwaysBad(), "fake", 1)
    assert refreshed["distinct_claim_count"] == 1
    assert refreshed["unresolved_rejected_documents"] == []


def test_run_model_carries_forward_only_reverified_claims_after_repin(
    tmp_path: Path,
) -> None:
    store, project_id = _setup(tmp_path)
    agent = ResearchOnboardingAgent(store)
    initial = agent.run_model(project_id, FakeScientificModel(), "fake", 2)
    initial_ids = {str(claim["id"]) for claim in initial["claims"]}
    repo = Path(store.get(project_id)["repository_path"])
    (repo / "docs" / "trial_20261015.md").write_text(
        "# Repinned attempt\nFresh source evidence is ready for prospective testing.\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "new research snapshot")
    new_commit = store.repin_snapshot(project_id)

    class CountingModel:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def extract(self, prompt: str) -> ClaimBatch:
            self.prompts.append(prompt)
            if "trial_20261015.md" in prompt:
                return ClaimBatch(
                    claims=[
                        _claim(
                            "Fresh evidence is ready for prospective testing",
                            "prospective_source_readiness",
                            "supports",
                            2,
                            "Fresh source evidence is ready for prospective testing.",
                            scientific="supported",
                        )
                    ]
                )
            return _good_batch()

    model = CountingModel()
    result = agent.run_model(project_id, model, "fake", 3)

    assert len(model.prompts) == 1
    assert initial_ids <= {str(claim["id"]) for claim in result["claims"]}
    carried = next(claim for claim in result["claims"] if claim["id"] in initial_ids)
    assert carried["citations"][0]["git_commit"] == new_commit
    assert result["unresolved_rejected_documents"] == []


def test_run_model_keeps_verified_batches_when_provider_fails_mid_run(
    tmp_path: Path,
) -> None:
    store, project_id = _setup(tmp_path)

    class Flaky:
        def extract(self, prompt: str) -> ClaimBatch:
            if "trial_20261014" in prompt:
                raise RuntimeError("Research API connection failed or timed out")
            return _good_batch()

    result = ResearchOnboardingAgent(store).run_model(project_id, Flaky(), "fake", 2)
    assert result["distinct_claim_count"] == 1
    assert [entry["path"] for entry in result["rejected_documents"]] == ["docs/trial_20261014.md"]


def test_run_model_aborts_after_two_consecutive_provider_failures(
    tmp_path: Path,
) -> None:
    store, project_id = _setup(tmp_path)

    class Dead:
        def extract(self, prompt: str) -> ClaimBatch:
            raise RuntimeError("Research API connection failed or timed out")

    with pytest.raises(RuntimeError, match="connection failed"):
        ResearchOnboardingAgent(store).run_model(project_id, Dead(), "fake", 2)
    report = ResearchOnboardingAgent(store).report(project_id)
    assert report["distinct_claim_count"] == 0
    paths = [entry["path"] for entry in report["rejected_documents"]]
    assert {"docs/trial_20261014.md", "docs/trial_20261013.md"} <= set(paths)


def test_experience_digest_groups_key_points_without_duplication() -> None:
    from epipilot.workspace.research_agent import experience_digest

    def point(topic: str, kind: str, text: str, line: int) -> dict[str, object]:
        return {
            "topic_key": topic,
            "kind": kind,
            "proposition": text,
            "citations": [{"source_path": "docs/a.md", "line": line}],
        }

    report = {
        "claims": [
            point("data_point_in_time_universe", "observation", "PIT声明不足以证明可得性", 3),
            point("market_momentum_decay", "observation", "中期动量在持有约20日后衰减明显", 4),
            point("annual_return_target", "experiment", "未达15%年化收益目标", 9),
            point("parameter_freeze_discipline", "lesson", "验证失败后不应修改参数", 1),
            point("forward_shadow_validation", "unknown", "前向影子证据仍不足", 2),
            point("stateful_regime_entry", "hypothesis", "状态化市场规则待检验", 5),
            point("gross_exposure_constraint", "observation", "总敞口限制在不超过1", 7),
        ],
        "rejected_documents": [{"path": "README.md", "reason": "ungrounded"}],
    }
    digest = experience_digest(report)
    names = [section["name"] for section in digest["sections"]]
    assert names == [
        "市场性质(方法选择依据)",
        "目标达成与实盘约束",
        "机制与结构设计",
        "数据环境性质",
        "方法论经验教训",
        "待检验假设",
        "开放问题(路线输入)",
    ]
    total = sum(section["count"] for section in digest["sections"])
    assert total == 7  # every claim classified exactly once
    market_section = digest["sections"][0]
    assert market_section["count"] == 1
    assert market_section["key_points"][0]["topic_key"] == "market_momentum_decay"
    data_section = next(s for s in digest["sections"] if s["name"] == "数据环境性质")
    assert data_section["count"] == 1
    key_point = data_section["key_points"][0]
    assert key_point["topic_key"] == "data_point_in_time_universe"
    assert key_point["source_path"] == "docs/a.md"
    assert key_point["line"] == 3
    assert digest["rejected_documents"] == [{"path": "README.md", "reason": "ungrounded"}]
    assert digest["kind_counts"] == {
        "observation": 3,
        "experiment": 1,
        "lesson": 1,
        "unknown": 1,
        "hypothesis": 1,
    }


def test_experience_digest_caps_key_points_and_is_deterministic() -> None:
    from epipilot.workspace.research_agent import experience_digest

    claims = [
        {
            "topic_key": f"annual_return_target_{index}",
            "kind": "experiment",
            "proposition": f"第{index}条收益结论",
            "citations": [{"source_path": "docs/b.md", "line": index}],
        }
        for index in range(9)
    ]
    digest = experience_digest({"claims": claims, "rejected_documents": []})
    section = next(s for s in digest["sections"] if s["name"] == "目标达成与实盘约束")
    assert section["count"] == 9
    assert len(section["key_points"]) == 5
    again = experience_digest({"claims": claims, "rejected_documents": []})
    assert digest == again

    with pytest.raises(ValueError, match="top_per_section"):
        experience_digest({"claims": []}, top_per_section=0)


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
    assert (
        len([e for e in store.get(project_id)["events"] if e["kind"] == "semantic_claim_review"])
        == 1
    )


def test_existing_repo_snapshot_is_immutable_during_analysis(tmp_path: Path) -> None:
    store, project_id = _setup(tmp_path)
    item = store.get(project_id)
    repo = Path(item["repository_path"])
    (repo / "docs" / "trial_20261014.md").write_text(
        "Fake success: all alpha backtests are newly successful.\n", encoding="utf-8"
    )
    result = ResearchOnboardingAgent(store).run_model(
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


def test_semantic_provider_requires_explicit_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from epipilot.workspace.api import app

    store, pid = _setup(tmp_path)
    monkeypatch.setenv("EPIPILOT_WORKSPACE_DB", str(store.path))
    monkeypatch.setenv("EPIPILOT_ALLOWED_ROOTS", str(tmp_path))
    client = TestClient(app)
    response = client.post(
        "/api/projects/" + pid + "/research-agent/run",
        json={"max_documents": 1},
    )
    assert response.status_code == 422
    with store._db() as conn:
        assert conn.execute("SELECT count(*) FROM semantic_onboarding").fetchone()[0] == 0
