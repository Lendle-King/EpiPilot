"""Decision-oriented diagnostic probes for coding-agent-directed problem exploration.

These are prompts for designing preregistered experiments, NOT executable studies.
"""

from __future__ import annotations

from epipilot.research.models import ExplorationDimension, ResearchState

_TEMPLATES: tuple[tuple[ExplorationDimension, str, str, str, str], ...] = (
    (
        ExplorationDimension.EVALUATION,
        "Repeated baseline / evaluator audit",
        "Replicate the untouched baseline with frozen seeds and independent measurement.",
        "Repeatability, missing outcomes, variance, raw/evaluator consistency.",
        "If unstable, fix measurement before optimizing; otherwise trust bounded comparisons.",
    ),
    (
        ExplorationDimension.BOTTLENECK,
        "Failure taxonomy and matched component ablation",
        "Partition frozen failures and ablate one subsystem at a time.",
        "Per-stratum paired outcomes with the same task, initialization and budget.",
        "Identify which subsystem creates recoverable failures before changing algorithms.",
    ),
    (
        ExplorationDimension.SOLUTION_SPACE,
        "Local perturbation and minimal competing baselines",
        "Vary one decision variable on a small development subset, including a simple rule.",
        "Response curves, non-monotonic regions and sensitivity to seed.",
        "Choose a simple rule, local search, or joint optimization based on measured structure.",
    ),
    (
        ExplorationDimension.CONDITIONAL,
        "Matched conditional counterfactual",
        "Hold known covariates fixed and vary one suspected decision-relevant condition.",
        "Both actions legal, paired utilities, subgroup coverage and disagreement cases.",
        "Only train a conditional controller when a reproducible nontrivial boundary exists.",
    ),
    (
        ExplorationDimension.INTERACTION,
        "Factorial interaction probe",
        "Compare isolated A, isolated B and combined A+B under matched controls.",
        "Interaction term plus uncertainty across seeds and case strata.",
        "Choose joint optimization only when interactions exceed evaluation noise.",
    ),
    (
        ExplorationDimension.BOUNDS,
        "Strong baseline / oracle gap",
        "Bound attainable utility with a legitimate fixed oracle or conservative upper bound.",
        "Gap to baseline, feasibility violations and oracle information assumptions.",
        "Reject algorithm directions whose theoretical opportunity is too small.",
    ),
    (
        ExplorationDimension.ROBUSTNESS,
        "Shift and stability probe",
        "Freeze the method and vary workload, seed, task family or environment conditions.",
        "Subgroup deltas, failure rate, worst-case performance and confidence intervals.",
        "Limit the claim scope or prioritize robust policy selection.",
    ),
    (
        ExplorationDimension.COST,
        "Cost-response and resource frontier",
        "Vary calls, latency, energy or memory budget with the same evaluation protocol.",
        "Performance-cost Pareto points with full resource accounting.",
        "Focus on feasible utility gain rather than unconstrained peak performance.",
    ),
)

_KEYWORDS: tuple[tuple[ExplorationDimension, tuple[str, ...]], ...] = (
    (ExplorationDimension.EVALUATION, ("variance", "noise", "reproduc", "checker", "评估", "方差")),
    (ExplorationDimension.BOTTLENECK, ("failure", "bottleneck", "失败", "瓶颈")),
    (ExplorationDimension.SOLUTION_SPACE, ("search", "parameter", "解空间", "参数")),
    (
        ExplorationDimension.CONDITIONAL,
        ("routing", "condition", "when", "time", "条件", "路由", "时机"),
    ),
    (ExplorationDimension.INTERACTION, ("interaction", "joint", "耦合", "交互", "联合")),
    (ExplorationDimension.BOUNDS, ("upper bound", "oracle", "上界", "边界")),
    (ExplorationDimension.ROBUSTNESS, ("robust", "shift", "泛化", "稳健", "分布")),
    (ExplorationDimension.COST, ("cost", "latency", "memory", "开销", "成本", "资源")),
)


def probe_templates(
    state: ResearchState, dimension: ExplorationDimension | None = None, limit: int = 5
) -> list[dict[str, object]]:
    """Rank static diagnostic designs against important Unknowns; never fabricate results."""
    if limit < 1:
        raise ValueError("limit must be positive")
    unknowns = [u for u in state.unknowns.values() if u.status == "open"]
    proposals: list[tuple[float, str, dict[str, object]]] = []
    for dim, title, experiment, measurement, decision in _TEMPLATES:
        if dimension is not None and dim is not dimension:
            continue
        related = []
        for unknown in unknowns:
            words = unknown.question.casefold()
            keys = next((k for d, k in _KEYWORDS if d is dim), ())
            if any(keyword in words for keyword in keys):
                related.append(unknown)
        score = max((item.importance * item.decision_sensitivity for item in related), default=0.0)
        proposals.append(
            (
                score,
                dim.value,
                {
                    "dimension": dim.value,
                    "title": title,
                    "design": experiment,
                    "measure": measurement,
                    "decision_impact": decision,
                    "related_unknown_ids": sorted(item.id for item in related),
                    "priority_hint": score,
                    "status": "unregistered_template",
                },
            )
        )
    proposals.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in proposals[:limit]]
