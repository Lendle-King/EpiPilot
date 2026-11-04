from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_metering import policy
from test_real_agents import task

from epipilot.benchfirst.live import LivePlan, OperatorApproval, read_approval


def plan() -> LivePlan:
    return LivePlan(
        family="agent_only",
        source={
            "checkout": "/absent",
            "url": "https://github.com/Lendle-King/EpiPilot",
            "sha": "a" * 40,
        },
        task=task(),
        image="sha256:" + "b" * 64,
        gateway_image="sha256:" + "c" * 64,
        model="test-model",
        track="calibration",
    )


def test_no_zero_budget_or_stale_or_other_plan_authorization(tmp_path: Path) -> None:
    frozen = plan()
    budget = policy(plan_sha256=frozen.sha256, max_attempts=0, max_cost_nusd=0)
    approval = OperatorApproval(
        operator_reference=budget.approval_ref,
        plan_sha256=frozen.sha256,
        budget=budget,
        approved_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="approval"):
        approval.check(frozen)
    path = tmp_path / "approval.json"
    path.write_text(approval.model_dump_json())
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        read_approval(path)
    path.chmod(0o600)
    assert read_approval(path) == approval


def test_prototype_cannot_label_a_run_as_a_formal_track() -> None:
    with pytest.raises(ValueError):
        LivePlan.model_validate({**plan().model_dump(), "track": "matched"})
    with pytest.raises(ValueError):
        LivePlan.model_validate({**plan().model_dump(), "track": "native"})
