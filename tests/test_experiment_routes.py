"""Experiment route generation and iteration records: grounding and schema tests."""

from __future__ import annotations

from typing import Any

import pytest

from epipilot.workspace.experiment_routes import ROUTE_GUIDANCE, ExperimentRoutePlanner
from epipilot.workspace.research_agent import ResearchOnboardingAgent
from test_research_onboarding_agent import ScriptedModel, _good_batch, _setup


class FakeRouteModel:
    def __init__(self, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    @property
    def prompt(self) -> str:
        return self.prompts[-1] if self.prompts else ""

    def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        if isinstance(self.payload, list):
            return self.payload.pop(0)
        return self.payload


def _route_payload(claim_id: str) -> dict[str, Any]:
    return {
        "premises_zh": ["2021-2023 阶段多个方法共同走弱,算法需检查阶段适应性。"],
        "proposals": [
            {
                "title": "Regime-aware sleeve rotation under PIT data",
                "rationale_zh": "状态持续性未解,先在冻结参数下检验状态分仓的增量价值。",
                "basis_claim_ids": [claim_id],
                "design_zh": "在 2018-2026 PIT 面板上对比固定分仓与状态分仓, "
                "成本 5bps, 次日开盘执行。",
                "success_criteria_zh": "验证期净 Calmar 相对基线提升且换手不高于 12 倍。",
                "falsification_zh": "若验证期无改善或 2021-2023 亏损扩大,则否定该路线。",
            }
        ],
        "stop_rules_zh": ["不修改 V2.5 冻结参数", "不回填补录前向证据"],
    }


def _seed_claims(store: Any, project_id: str) -> str:
    agent = ResearchOnboardingAgent(store)
    agent.run_model(project_id, ScriptedModel([_good_batch(), _good_batch()]), "fake", 2)
    report = agent.report(project_id)
    return str(report["claims"][0]["id"])


def test_route_generation_requires_existing_claims(tmp_path: Any) -> None:
    store, project_id = _setup(tmp_path)
    planner = ExperimentRoutePlanner(store)
    with pytest.raises(ValueError, match="No grounded claims"):
        planner.generate(project_id, FakeRouteModel(_route_payload("x")), "fake")


def test_algorithm_mode_rejects_diagnostic_and_previously_completed_algorithms(
    tmp_path: Any,
) -> None:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    planner = ExperimentRoutePlanner(store)
    diagnostic = _route_payload(claim_id)
    with pytest.raises(ValueError, match="algorithm_id"):
        planner.generate(project_id, FakeRouteModel(diagnostic), "fake", require_algorithm=True)
    candidate = _route_payload(claim_id)
    candidate["proposals"][0]["algorithm_id"] = "dual_momentum"
    planner.generate(project_id, FakeRouteModel(candidate), "fake", require_algorithm=True)
    planner.record_loop_checkpoint(project_id, {"algorithm_id": "dual_momentum"})
    with pytest.raises(ValueError, match="algorithm_id"):
        planner.generate(project_id, FakeRouteModel(candidate), "fake", require_algorithm=True)


def test_route_guidance_prefers_public_diagnostics_when_inputs_missing() -> None:
    assert "bounded data-readiness or diagnostic route" in ROUTE_GUIDANCE
    assert "must not treat public diagnostics as V2.5 evidence" in ROUTE_GUIDANCE
    assert "do not repeat a route that only requests missing artifacts" in ROUTE_GUIDANCE
    assert "committed host-side validation" in ROUTE_GUIDANCE
    assert "do not block solely because" in ROUTE_GUIDANCE


def test_route_generation_is_grounded_on_claim_ids(tmp_path: Any) -> None:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    planner = ExperimentRoutePlanner(store)
    model = FakeRouteModel(_route_payload(claim_id))

    report = planner.generate(project_id, model, "fake-route")
    assert report["available"] is True
    assert report["route"]["proposals"][0]["basis_claim_ids"] == [claim_id]
    assert report["route"]["provider"] == "fake-route"
    assert "Grounded claims" in model.prompts[0]
    assert claim_id in model.prompts[0]
    assert "Complete claim-id inventory" in model.prompts[0]

    with pytest.raises(ValueError, match="unknown claim id"):
        planner.generate(project_id, FakeRouteModel(_route_payload("not-a-real-id")), "fake")


def test_route_review_import_validates_and_persists(tmp_path: Any) -> None:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    planner = ExperimentRoutePlanner(store)
    planner.generate(project_id, FakeRouteModel(_route_payload(claim_id)), "fake")

    review = (
        '{"verdict": "feasible", "evaluation_zh": "路线可行,前置条件清晰且与 claims 一致。", '
        '"results_digest_zh": "对照回测完成:验证期 Calmar 提升 0.05,换手持平。", '
        '"recommended_next_zh": "扩大状态规则邻域检验并保持冻结基准。", '
        '"evidence_refs": ["out/route_run_1"]}'
    )
    report = planner.import_review(project_id, review)
    assert report["review"]["verdict"] == "feasible"
    assert report["review"]["evidence_refs"] == ["out/route_run_1"]

    with pytest.raises(ValueError, match="schema validation"):
        planner.import_review(project_id, '{"verdict": "x"}')


def test_route_output_tolerates_extra_fields_and_retries_schema_failures(tmp_path: Any) -> None:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    planner = ExperimentRoutePlanner(store)

    noisy = _route_payload(claim_id)
    noisy["unexpected_top_level"] = True
    noisy["proposals"][0]["priority"] = "P0"
    model = FakeRouteModel(noisy)
    report = planner.generate(project_id, model, "fake")
    assert report["route"]["proposals"][0]["title"].startswith("Regime-aware")
    assert "unexpected_top_level" not in report["route"]

    broken = {"premises_zh": [], "proposals": [{"title": "x"}], "stop_rules_zh": []}
    retrying = FakeRouteModel([broken, _route_payload(claim_id)])
    report = planner.generate(project_id, retrying, "fake")
    assert report["route"]["proposals"][0]["basis_claim_ids"] == [claim_id]
    assert len(retrying.prompts) == 2
    assert "CORRECTIVE CONSTRAINT" in retrying.prompts[1]

    stubborn = FakeRouteModel([broken, broken])
    with pytest.raises(ValueError, match="schema validation"):
        planner.generate(project_id, stubborn, "fake")


def test_route_retries_when_claim_ids_are_hallucinated(tmp_path: Any) -> None:
    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    planner = ExperimentRoutePlanner(store)

    hallucinated = _route_payload("73a06da3ac87e0fb41507548")
    model = FakeRouteModel([hallucinated, _route_payload(claim_id)])
    report = planner.generate(project_id, model, "fake")
    assert report["route"]["proposals"][0]["basis_claim_ids"] == [claim_id]
    assert len(model.prompts) == 2
    assert "unknown claim ids" in model.prompts[1]
    assert "character-for-character" in model.prompts[1]

    stubborn = FakeRouteModel([hallucinated, hallucinated])
    with pytest.raises(ValueError, match="unknown claim id"):
        planner.generate(project_id, stubborn, "fake")


def test_algorithm_model_can_follow_the_offered_json_schema(tmp_path: Any) -> None:
    import json

    store, project_id = _setup(tmp_path)
    claim_id = _seed_claims(store, project_id)
    candidate = _route_payload(claim_id)
    candidate["proposals"][0]["algorithm_id"] = "dual_momentum"

    class SchemaFollowingModel:
        def complete_json(self, system: str, prompt: str) -> Any:
            offered = prompt.split("Return JSON exactly like: ", 1)[1]
            schema, _end = json.JSONDecoder().raw_decode(offered)
            result = dict(candidate)
            result["proposals"] = [
                {key: candidate["proposals"][0][key] for key in schema["proposals"][0]}
            ]
            return result

    result = ExperimentRoutePlanner(store).generate(
        project_id, SchemaFollowingModel(), "fake", require_algorithm=True
    )
    assert result["route"]["proposals"][0]["algorithm_id"] == "dual_momentum"
