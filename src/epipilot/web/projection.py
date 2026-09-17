"""Deterministic projection: explain state without certifying new project facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from epipilot.web.models import Card, ProjectView, RepositoryView, SourceRef

if TYPE_CHECKING:
    from epipilot.state.project import ProjectState


def build_project_view(
    state: ProjectState | None,
    repository: RepositoryView,
    *,
    project_id: str,
    captured_at: str,
) -> ProjectView:
    goals: list[Card] = []
    requirements: list[Card] = []
    tasks: list[Card] = []
    knowledge = list(repository.modules)
    opportunities: list[Card] = []
    decisions: list[Card] = []
    evidence: list[Card] = []
    next_steps: list[Card] = []
    verified_count = 0
    warnings = [
        "任务验证不等于目标验收：当前事件模型没有逐项需求验收映射。",
        "历史证据未绑定当前 Git 版本时，不能视为当前版本已验证。",
    ]
    if state is not None:
        if state.project_id != project_id:
            raise ValueError("project id mismatch")
        for requirement in state.requirements:
            card = Card(
                id=f"requirement-{requirement.id}", title=requirement.statement,
                status=requirement.kind.value,
                summary="正式需求；验收状态尚未建立证据映射。",
                refs=(SourceRef(kind="requirement", location=str(requirement.id),
                                scope=requirement.provenance.scope,
                                recorded_at=requirement.provenance.created_at.isoformat()),),
            )
            if requirement.kind.value == "goal":
                goals.append(card)
            else:
                requirements.append(card)
        by_evidence = {item.id: item for item in state.evidence}
        for item in state.evidence:
            independent = item.independently_verified and item.kind.value != "executor_report"
            evidence.append(Card(
                id=f"evidence-{item.id}", title=item.summary,
                status="independent_evidence" if independent else "unverified_observation",
                summary="证据仅在记录的来源与范围内有效；当前代码版本适用性未自动确认。",
                details={"证据类别": item.kind.value, "来源": item.provenance.source,
                         "适用范围": item.provenance.scope,
                         "记录时间": item.provenance.created_at.isoformat()},
                refs=(SourceRef(kind=item.kind.value, location=str(item.id),
                                scope=item.provenance.scope,
                                recorded_at=item.provenance.created_at.isoformat()),),
            ))
        for task in state.tasks:
            records = [record for record in state.verifications if record.task_id == task.id]
            latest = records[-1] if records else None
            proof = by_evidence.get(latest.evidence_id) if latest and latest.evidence_id else None
            verified = bool(
                task.status.value == "passed" and latest and latest.passed and proof
                and proof.independently_verified and proof.kind.value != "executor_report"
                and proof.id in task.linked_evidence
            )
            verified_count += int(verified)
            card = Card(
                id=f"task-{task.id}", title=task.objective,
                status=task.status.value if task.status.value != "passed" or verified else "needs_review",
                summary="历史独立验证通过；合并、部署和当前版本适用性另行判断。" if verified
                else "执行状态不是验收结论；需要独立验证。",
                details={"任务状态": task.status.value,
                         "独立验证": "已记录" if verified else "未确认",
                         "执行位置": "以实际执行器/工作区记录为准"},
                refs=(SourceRef(kind="task", location=str(task.id)),),
                related_ids=tuple(f"evidence-{value}" for value in task.linked_evidence),
            )
            tasks.append(card)
            if task.status.value in {"ready", "blocked", "failed", "agent_reported_done"}:
                next_steps.append(card)
        for unknown in state.unknowns:
            card = Card(
                id=f"unknown-{unknown.id}", title=unknown.question, status="technical_unknown",
                summary="决策相关的未知问题，不代表已证实的缺陷或必须实施的优化。",
                details={"影响": unknown.impact.value,
                         "建议调查方式": unknown.resolution_mode.value,
                         "阻塞任务数": str(len(unknown.blocking_tasks)),
                         "收益": "尚未测量", "代价与风险": "设计实验时评估"},
                refs=(SourceRef(kind="unknown", location=str(unknown.id)),),
                related_ids=tuple(f"task-{value}" for value in unknown.blocking_tasks),
            )
            opportunities.append(card)
            if unknown.resolution_mode.value == "ask_user":
                decisions.append(card)
        for hypothesis in state.hypotheses:
            refs = tuple(SourceRef(kind="evidence", location=str(value)) for value in
                         (*hypothesis.supporting_evidence, *hypothesis.contradicting_evidence))
            card = Card(
                id=f"hypothesis-{hypothesis.id}", title=hypothesis.statement,
                status=f"hypothesis_{hypothesis.status.value}",
                summary="假设状态不是项目事实；需要结合支持与反对证据解释。",
                details={"预期观察": "\n".join(hypothesis.predictions) or "尚未定义",
                         "证伪条件": "\n".join(hypothesis.falsification_conditions) or "尚未定义",
                         "收益": "尚未测量", "范围": "是否属于当前目标需规划器确认"},
                refs=(SourceRef(kind="hypothesis", location=str(hypothesis.id)), *refs),
                related_ids=tuple(f"evidence-{value}" for value in
                                  (*hypothesis.supporting_evidence, *hypothesis.contradicting_evidence)),
            )
            knowledge.append(card)
            if hypothesis.status.value not in {"refuted", "superseded"}:
                opportunities.append(card)
        for fact in state.facts:
            knowledge.append(Card(
                id=f"fact-{fact.id}", title=fact.statement,
                status="historical_fact" if fact.valid_to else "scoped_fact",
                summary="有证据支持的范围内结论；不能自动推广到新的代码或环境。",
                details={"有效起点": fact.valid_from.isoformat(),
                         "有效终点": fact.valid_to.isoformat() if fact.valid_to else "未关闭",
                         "来源": fact.provenance.source, "范围": fact.provenance.scope},
                refs=tuple(SourceRef(kind="evidence", location=str(value))
                           for value in fact.supporting_evidence),
                related_ids=tuple(f"evidence-{value}" for value in fact.supporting_evidence),
            ))
        for decision in state.decisions:
            decisions.append(Card(
                id=f"decision-{decision.id}", title=decision.question, status="decision_recorded",
                summary=decision.choice,
                details={"理由": decision.rationale, "决策权属": decision.authority.value,
                         "可撤销": "是" if decision.reversible else "否"},
                refs=tuple(SourceRef(kind="decision_basis", location=value)
                           for value in decision.basis_refs),
            ))
    else:
        warnings.append("尚未连接事件库；任务、实验、证据和目标验收数据均未提供。")

    if not goals or not any(item.status == "success_criterion" for item in requirements):
        gap = Card(
            id="gap-contract", title="明确当前目标与验收标准", status="observed_gap",
            summary="未找到完整的正式目标和成功标准；不要用任务完成率代替目标验收。",
            details={"建议": "在项目控制层登记目标和可独立检查的成功标准。",
                     "范围": "当前目标所必需", "收益": "让后续推进具有明确停止条件"},
            refs=(SourceRef(kind="project_state", location=project_id),),
        )
        opportunities.insert(0, gap)
        next_steps.insert(0, gap)
    if not repository.documents:
        opportunities.append(Card(
            id="gap-docs", title="建立项目主线说明", status="observed_gap",
            summary="本次受限扫描未发现 Markdown 项目文档；不等于仓库中不存在其他文档。",
            refs=(SourceRef(kind="repository_scan", location=".", revision=repository.revision),),
        ))
    return ProjectView(
        project_id=project_id, name=repository.name,
        source_mode="events" if state is not None else "repository",
        captured_at=captured_at, event_version=state.event_version if state else 0,
        repository=repository,
        summary="从项目主线、目标差距与证据出发，决定下一步。"
        if goals else "已建立静态项目地图；当前目标与执行结果尚需接入正式项目状态。",
        goals=tuple(goals), requirements=tuple(requirements), tasks=tuple(tasks),
        knowledge=tuple(knowledge), opportunities=tuple(opportunities), decisions=tuple(decisions),
        evidence=tuple(evidence), next_steps=tuple(next_steps[:5]),
        verified_task_count=verified_count, warnings=tuple(warnings),
    )
