"""Autonomous iteration loop: bounded rounds, stop verdicts, fail-closed stops."""

from __future__ import annotations

from typing import Any

from epipilot.workspace.chatgpt_web import ChatGPTWebError
from epipilot.workspace.experiment_routes import ExperimentRoutePlanner
from epipilot.workspace.iteration_loop import AutoIterationLoop
from test_experiment_routes import FakeRouteModel, _route_payload, _seed_claims
from test_research_onboarding_agent import _good_batch, _setup


class AlwaysGood:
    def extract(self, prompt: str) -> Any:
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


def _review(verdict: str, report: str = "") -> str:
    return (
        '{"verdict": "' + verdict + '", "evaluation_zh": "评估结论与 claims 一致。", '
        '"results_digest_zh": "对照结果已汇总, 指标符合预期。", '
        '"recommended_next_zh": "继续下一轮。", '
        '"evidence_refs": ["out/run"], "report_markdown": ' + repr(report).replace("'", '"') + "}"
    )


def _start(tmp_path: Any, driver: SequenceDriver, max_rounds: int) -> tuple[Any, dict]:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    loop = AutoIterationLoop()
    status = loop.start(
        project_id,
        store=store,
        route_model=FakeRouteModel(_route_payload(claim_id)),
        claim_model=AlwaysGood(),
        claim_provider="fake",
        driver=driver,
        project_name="QUANT_ETF",
        repo_path=tmp_path / "repo",
        max_rounds=max_rounds,
    )
    return store, status


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
