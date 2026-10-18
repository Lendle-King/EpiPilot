"""Autonomous iteration loop: bounded rounds, stop verdicts, fail-closed stops."""

from __future__ import annotations

import re
from typing import Any

import pytest

from epipilot.workspace.chatgpt_web import ChatGPTWebError
from epipilot.workspace.experiment_routes import ExperimentRoutePlanner
from epipilot.workspace.iteration_loop import AutoIterationLoop, _host_repository_handoff
from test_experiment_routes import FakeRouteModel, _route_payload, _seed_claims
from test_research_onboarding_agent import _bad_batch, _good_batch, _setup


class AlwaysGood:
    def extract(self, prompt: str) -> Any:
        if "Source document: docs/auto_iteration/" in prompt:
            line = next(
                (
                    match
                    for match in re.finditer(r"^\[L(\d+)\] (.+)$", prompt, re.MULTILINE)
                    if len(match[2].strip()) >= 14
                ),
                None,
            )
            if line is None:
                return _bad_batch()
            claim = (
                _good_batch()
                .claims[0]
                .model_copy(update={"source_line": int(line[1]), "exact_quote": line[2]})
            )
            return _good_batch().model_copy(update={"claims": [claim]})
        return _good_batch()


class SequenceDriver:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.prompts: list[str] = []

    def submit(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._replies:
            raise AssertionError("driver ran out of scripted replies")
        return self._replies.pop(0)


def _review(verdict: str, report: str = "# Round report\nSynthetic observation only.") -> str:
    return (
        '{"verdict": "' + verdict + '", "evaluation_zh": "评估结论与 claims 一致。", '
        '"results_digest_zh": "对照结果已汇总, 指标符合预期。", '
        '"recommended_next_zh": "继续下一轮。", '
        '"evidence_refs": ["out/run"], "report_markdown": ' + repr(report).replace("'", '"') + "}"
    )


def _start(
    tmp_path: Any,
    driver: SequenceDriver,
    max_rounds: int,
    *,
    claim_model: Any = None,
    archive: bool = True,
    algorithm_executor: Any = None,
) -> tuple[Any, dict]:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    loop = AutoIterationLoop()
    payload = _route_payload(claim_id)
    if algorithm_executor is not None:
        payload["proposals"][0]["algorithm_id"] = "dual_momentum"
    status = loop.start(
        project_id,
        store=store,
        route_model=FakeRouteModel(payload),
        claim_model=claim_model or AlwaysGood(),
        claim_provider="fake",
        driver=driver,
        project_name="QUANT_ETF",
        repo_path=tmp_path / "repo" if archive else None,
        max_rounds=max_rounds,
        algorithm_executor=algorithm_executor,
    )
    return store, status


class RealFileExecutor:
    def __init__(self, repo: Any) -> None:
        self.repo = repo

    def execute(self, request: Any) -> Any:
        import json

        from test_algorithm_execution import receipt_fixture

        path = receipt_fixture(self.repo)
        payload = json.loads(path.read_text())
        payload.update(request.model_dump())
        path.write_text(json.dumps(payload))
        return path


def test_algorithm_round_rejects_summary_only_without_advancing_checkpoint(tmp_path: Any) -> None:
    driver = SequenceDriver([_review("goal_met")] * 3)
    store, status = _start(
        tmp_path, driver, 1, algorithm_executor=RealFileExecutor(tmp_path / "repo")
    )
    assert status["rounds_completed"] == 0
    assert status["step"] == "stopped"
    assert ExperimentRoutePlanner(store).load_loop_checkpoint(_project_of(store)) is None
    with store._db() as conn:
        assert (
            conn.execute("SELECT count(*) FROM route_runs WHERE kind='loop_attempt'").fetchone()[0]
            == 3
        )


def test_algorithm_round_repairs_review_and_archives_verified_metrics(tmp_path: Any) -> None:
    import json

    class RepairDriver(SequenceDriver):
        def submit(self, prompt: str) -> str:
            if not self.prompts:
                self.prompts.append(prompt)
                return _review("goal_met")
            route_id = re.search(r"ROUTE_RUN_ID: ([^\n]+)", prompt)[1]
            self.prompts.append(prompt)
            payload = json.loads(_review("goal_met"))
            payload.update(
                execution_run_id=route_id,
                candidate_conclusion="reject",
                new_claims_zh=["合成测试算法实验出现10%最大回撤,只支持测试观测。"],
            )
            return json.dumps(payload)

    driver = RepairDriver([])
    store, status = _start(
        tmp_path, driver, 1, algorithm_executor=RealFileExecutor(tmp_path / "repo")
    )
    assert status["rounds_completed"] == 1
    assert status["verdicts"] == ["continue"]  # historical evidence cannot prove forward goal
    assert status["round"] == 1
    report = next((tmp_path / "repo/docs/auto_iteration").glob("round_1_*.md"))
    assert "-10.000000%" in report.read_text()
    assert "reject" in report.read_text()
    assert "repair" in driver.prompts[1].lower()
    assert ExperimentRoutePlanner(store).load_loop_checkpoint(_project_of(store)) is not None


@pytest.mark.parametrize("explicit_resume", [False, True])
def test_algorithm_round_resumes_failed_claim_refresh_without_web_or_duplicate_report(
    tmp_path: Any,
    explicit_resume: bool,
) -> None:
    import json

    class CachedExecutor(RealFileExecutor):
        path: Any = None

        def execute(self, request: Any) -> Any:
            if self.path is None:
                self.path = super().execute(request)
            return self.path

    class ReviewDriver:
        def submit(self, prompt: str) -> str:
            payload = json.loads(_review("continue"))
            payload.update(
                execution_run_id=re.search(r"ROUTE_RUN_ID: ([^\n]+)", prompt)[1],
                candidate_conclusion="reject",
                new_claims_zh=["本轮算法合成结果未改善基线。"],
            )
            return json.dumps(payload)

    class BadReport(AlwaysGood):
        def extract(self, prompt: str) -> Any:
            if "Source document: docs/auto_iteration/" in prompt:
                return _bad_batch()
            return super().extract(prompt)

    executor = CachedExecutor(tmp_path / "repo")
    store, failed = _start(
        tmp_path, ReviewDriver(), 1, claim_model=BadReport(), algorithm_executor=executor
    )
    project_id = _project_of(store)
    assert failed["rounds_completed"] == 0
    route_id = ExperimentRoutePlanner(store).report(project_id)["route"]["run_id"]
    resumed = AutoIterationLoop().start(
        project_id,
        store=store,
        route_model=FakeRouteModel({}),
        claim_model=AlwaysGood(),
        claim_provider="fake",
        driver=SequenceDriver([]),
        project_name="P",
        repo_path=tmp_path / "repo",
        max_rounds=1,
        resume_route_run_id=route_id if explicit_resume else None,
        algorithm_executor=executor,
    )
    assert resumed["rounds_completed"] == 1, resumed
    assert resumed["round"] == 1
    assert len(list((tmp_path / "repo/docs/auto_iteration").glob("round_1_*.md"))) == 1


def test_imported_review_cannot_bypass_algorithm_contract_during_recovery(tmp_path: Any) -> None:
    import json

    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    payload = _route_payload(claim_id)
    payload["proposals"][0]["algorithm_id"] = "dual_momentum"
    planner = ExperimentRoutePlanner(store)
    route = planner.generate(project_id, FakeRouteModel(payload), "fake")["route"]
    review = json.loads(_review("blocked"))
    review["execution_run_id"] = route["run_id"]
    planner.import_review(project_id, json.dumps(review))
    status = AutoIterationLoop().start(
        project_id,
        store=store,
        route_model=FakeRouteModel({}),
        claim_model=AlwaysGood(),
        claim_provider="fake",
        driver=SequenceDriver([]),
        project_name="P",
        repo_path=tmp_path / "repo",
        max_rounds=1,
        algorithm_executor=RealFileExecutor(tmp_path / "repo"),
    )
    assert status["rounds_completed"] == 0
    assert status["step"] == "stopped"
    assert planner.load_loop_checkpoint(project_id) is None


def test_loop_stops_on_goal_met_and_records_rounds(tmp_path: Any) -> None:
    driver = SequenceDriver(
        [_review("continue", "# Round 1 report\nResult line."), _review("goal_met")]
    )
    store, status = _start(tmp_path, driver, max_rounds=5)

    assert status["stop_reason"] == "阶段判定停止: goal_met"
    assert status["rounds_completed"] == 2
    assert status["verdicts"] == ["continue", "goal_met"]
    assert status["running"] is False
    # round 1 committed its report and re-pinned the snapshot before round 2
    reports = list((tmp_path / "repo" / "docs" / "auto_iteration").glob("round_1_*.md"))
    assert len(reports) == 1
    assert "Result line." in reports[0].read_text(encoding="utf-8")
    assert store.get(_project_of(store))["git_commit"] != ""
    assert len(driver.prompts) == 2


def test_loop_stops_at_max_rounds(tmp_path: Any) -> None:
    driver = SequenceDriver([_review("continue"), _review("continue")])
    _, status = _start(tmp_path, driver, max_rounds=2)
    assert status["stop_reason"] == "达到最大轮次 2"
    assert status["rounds_completed"] == 2


def test_loop_stops_on_major_problem(tmp_path: Any) -> None:
    class Dead:
        def submit(self, prompt: str) -> str:
            raise ChatGPTWebError("session is not logged in")

    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    loop = AutoIterationLoop()
    status = loop.start(
        project_id,
        store=store,
        route_model=FakeRouteModel(_route_payload(claim_id)),
        claim_model=AlwaysGood(),
        claim_provider="fake",
        driver=Dead(),
        project_name="P",
        repo_path=None,
        max_rounds=3,
    )
    assert status["stop_reason"].startswith("重大问题:")
    assert status["rounds_completed"] == 0
    assert status["running"] is False


def test_loop_persists_auditable_summary(tmp_path: Any) -> None:
    driver = SequenceDriver([_review("goal_met")])
    store, status = _start(tmp_path, driver, max_rounds=3)
    planner = ExperimentRoutePlanner(store)
    # summary is appended as a loop_summary run record; route+review still visible
    assert planner.report(_project_of(store))["available"] is True
    with store._db() as conn:
        rows = conn.execute("SELECT kind FROM route_runs").fetchall()
    kinds = [row[0] for row in rows]
    assert "loop_summary" in kinds
    assert status["rounds_completed"] == 1


def _project_of(store: Any) -> str:
    with store._db() as conn:
        row = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()
    return str(row[0])


def test_loop_resumes_from_checkpoint_after_failure(tmp_path: Any) -> None:
    class Flaky:
        def __init__(self) -> None:
            self.calls = 0

        def submit(self, prompt: str) -> str:
            self.calls += 1
            if self.calls == 2:
                raise ChatGPTWebError("connection failed")
            return _review("continue", "# R1\nround one result.")

    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    route = FakeRouteModel(_route_payload(claim_id))
    loop = AutoIterationLoop()
    status = loop.start(
        project_id,
        store=store,
        route_model=route,
        claim_model=AlwaysGood(),
        claim_provider="fake",
        driver=Flaky(),
        project_name="P",
        repo_path=tmp_path / "repo",
        max_rounds=5,
    )
    assert status["stop_reason"].startswith("重大问题:")
    assert status["rounds_completed"] == 1

    resumed = loop.start(
        project_id,
        store=store,
        route_model=FakeRouteModel(_route_payload(claim_id)),
        claim_model=AlwaysGood(),
        claim_provider="fake",
        driver=SequenceDriver([_review("goal_met")]),
        project_name="P",
        repo_path=tmp_path / "repo",
        max_rounds=2,
    )
    assert resumed["stop_reason"] == "阶段判定停止: goal_met"
    assert resumed["rounds_completed"] == 2
    assert resumed["verdicts"] == ["continue", "goal_met"]
    assert resumed["round"] == 2


def test_checkpoint_records_last_successful_round(tmp_path: Any) -> None:
    driver = SequenceDriver([_review("goal_met")])
    store, _ = _start(tmp_path, driver, max_rounds=3)
    from epipilot.workspace.experiment_routes import ExperimentRoutePlanner as Planner

    checkpoint = Planner(store).load_loop_checkpoint(_project_of(store))
    assert checkpoint is not None
    assert checkpoint["rounds_completed"] == 1
    assert checkpoint["verdicts"] == ["goal_met"]


@pytest.mark.parametrize("verdict", ["blocked", "goal_met"])
def test_terminal_round_archives_report_before_checkpoint(tmp_path: Any, verdict: str) -> None:
    from epipilot.workspace.onboarding import git

    store, status = _start(
        tmp_path,
        SequenceDriver([_review(verdict, "# Terminal report\nNo independent result.")]),
        max_rounds=10,
    )
    repo = tmp_path / "repo"
    reports = list((repo / "docs" / "auto_iteration").glob("round_1_*.md"))
    assert len(reports) == 1
    relative = str(reports[0].relative_to(repo))
    assert "No independent result." in git(repo, "show", f"HEAD:{relative}")
    assert store.get(_project_of(store))["git_commit"] == git(repo, "rev-parse", "HEAD")
    checkpoint = ExperimentRoutePlanner(store).load_loop_checkpoint(_project_of(store))
    assert checkpoint is not None
    assert checkpoint["rounds_completed"] == 1
    assert status["stop_reason"] == f"阶段判定停止: {verdict}"


@pytest.mark.parametrize(
    "archive,report", [(False, "# Report\nSynthetic observation only."), (True, " ")]
)
def test_terminal_round_without_archive_does_not_advance_checkpoint(
    tmp_path: Any, archive: bool, report: str
) -> None:
    store, status = _start(
        tmp_path, SequenceDriver([_review("blocked", report)]), 10, archive=archive
    )
    assert status["step"] == "stopped"
    assert status["rounds_completed"] == 0
    assert ExperimentRoutePlanner(store).load_loop_checkpoint(_project_of(store)) is None


def test_rejected_new_report_does_not_advance_checkpoint(tmp_path: Any) -> None:
    class BadReport(AlwaysGood):
        def extract(self, prompt: str) -> Any:
            if "Source document: docs/auto_iteration/" in prompt:
                return _bad_batch()
            return super().extract(prompt)

    store, status = _start(
        tmp_path, SequenceDriver([_review("blocked")]), 10, claim_model=BadReport()
    )
    assert status["step"] == "stopped"
    assert status["rounds_completed"] == 0
    assert ExperimentRoutePlanner(store).load_loop_checkpoint(_project_of(store)) is None
    # The committed observation and rejected extraction stay available for audit.
    assert len(list((tmp_path / "repo" / "docs" / "auto_iteration").glob("*.md"))) == 1


def test_resume_uses_original_route_then_continues_round_budget(tmp_path: Any) -> None:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    planner = ExperimentRoutePlanner(store)
    original = planner.generate(project_id, FakeRouteModel(_route_payload(claim_id)), "fake")[
        "route"
    ]
    driver = SequenceDriver([_review("continue"), _review("goal_met")])
    model = FakeRouteModel(_route_payload(claim_id))
    status = AutoIterationLoop().start(
        project_id,
        store=store,
        route_model=model,
        claim_model=AlwaysGood(),
        claim_provider="fake",
        driver=driver,
        project_name="P",
        repo_path=tmp_path / "repo",
        max_rounds=2,
        resume_route_run_id=original["run_id"],
    )
    assert status["rounds_completed"] == 2
    assert original["run_id"] in driver.prompts[0]
    assert len(model.prompts) == 1
    with store._db() as conn:
        assert conn.execute("SELECT count(*) FROM route_runs WHERE kind='route'").fetchone()[0] == 2


def test_resume_rejects_wrong_route_before_using_driver(tmp_path: Any) -> None:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    model = FakeRouteModel(_route_payload(claim_id))
    ExperimentRoutePlanner(store).generate(project_id, model, "fake")
    driver = SequenceDriver([])
    status = AutoIterationLoop().start(
        project_id,
        store=store,
        route_model=model,
        claim_model=AlwaysGood(),
        claim_provider="fake",
        driver=driver,
        project_name="P",
        repo_path=tmp_path / "repo",
        max_rounds=2,
        resume_route_run_id="wrong-route",
    )
    assert status["step"] == "stopped"
    assert driver.prompts == []
    assert status["rounds_completed"] == 0


@pytest.mark.parametrize(
    "binding",
    [{"resume_route_run_id": "pending"}, {"resume_route_run_id": "wrong", "resume_tab_id": 7}],
)
def test_resume_http_rejects_incomplete_or_wrong_binding(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, binding: dict[str, Any]
) -> None:
    from fastapi.testclient import TestClient

    from epipilot.workspace import api

    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    ExperimentRoutePlanner(store).generate(
        project_id, FakeRouteModel(_route_payload(claim_id)), "fake"
    )
    monkeypatch.setattr(api, "_store", lambda: store)
    monkeypatch.setattr(api, "_auto_iteration", AutoIterationLoop())
    monkeypatch.setattr(api, "_run_auto_iteration_sync", lambda *args: None)
    with TestClient(api.app) as client:
        response = client.post(f"/api/projects/{project_id}/research-agent/loop", json=binding)
    assert response.status_code == 400


def test_http_algorithm_loop_rejects_missing_interpreter_before_start(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from epipilot.workspace import api

    store, project_id = _setup(tmp_path)
    script = tmp_path / "repo/scripts/run_epipilot_algorithm_round.py"
    script.parent.mkdir(parents=True)
    script.write_text("# synthetic adapter")
    monkeypatch.delenv("EPIPILOT_ALGORITHM_PYTHON", raising=False)
    monkeypatch.setattr(api, "_store", lambda: store)
    monkeypatch.setattr(api, "_auto_iteration", AutoIterationLoop())
    monkeypatch.setattr(api, "_run_auto_iteration_sync", lambda *args: None)
    with TestClient(api.app) as client:
        response = client.post(
            f"/api/projects/{project_id}/research-agent/loop", json={"max_rounds": 1}
        )
    assert response.status_code == 400


def test_http_loop_status_restores_persisted_checkpoint_after_restart(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from epipilot.workspace import api

    store, project_id = _setup(tmp_path)
    checkpoint = {
        "running": True,
        "round": 7,
        "step": "done",
        "message": "round 7: verdict blocked",
        "stop_reason": "阶段判定停止: blocked",
        "verdicts": ["blocked"] * 7,
        "rounds_completed": 7,
    }
    ExperimentRoutePlanner(store).record_loop_checkpoint(project_id, checkpoint)
    monkeypatch.setattr(api, "_store", lambda: store)
    monkeypatch.setattr(api, "_auto_iteration", AutoIterationLoop())

    with TestClient(api.app) as client:
        response = client.get(f"/api/projects/{project_id}/research-agent/loop")

    assert response.status_code == 200
    restored = response.json()
    assert restored["running"] is False
    assert restored["round"] == 7
    assert restored["rounds_completed"] == 7
    assert restored["stop_reason"] == "阶段判定停止: blocked"


def test_host_handoff_prioritizes_recent_validation_reports(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [
        "docs/data_sources/old_validation.md",
        "docs/data_sources/new_validation.md",
        "docs/data_sources/other_validation.md",
        "docs/data_sources/fourth_validation.md",
        "docs/data_sources/fifth_validation.md",
    ]
    timestamps = {
        "old_validation.md": "100",
        "new_validation.md": "500",
        "other_validation.md": "300",
        "fourth_validation.md": "200",
        "fifth_validation.md": "400",
    }

    def fake_git(_repo: Any, *args: str) -> str:
        if args[:2] == ("rev-parse", "HEAD"):
            return "head"
        if args[:2] == ("ls-tree", "-r"):
            return "\n".join(paths)
        if args[:2] == ("log", "-1"):
            path = args[-1]
            return timestamps[path.rsplit("/", 1)[-1]]
        if args[:2] == ("show", "head:docs/data_sources/new_validation.md"):
            return "NEW_RESULT"
        if args[0] == "show":
            return "OTHER_RESULT"
        raise AssertionError(args)

    monkeypatch.setattr("epipilot.workspace.iteration_loop.git", fake_git)

    handoff = _host_repository_handoff(tmp_path)

    assert handoff.index("new_validation.md") < handoff.index("fifth_validation.md")
    assert "NEW_RESULT" in handoff
