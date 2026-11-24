"""V0.3 scientific discovery prompts, evidence validation and experiment selection."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

from pydantic import TypeAdapter

from epipilot.topic_discovery.models import DiscoveryWorkspace
from epipilot.topic_discovery.v03_models import (
    DiscriminativeExperiment,
    EvidenceReference,
    ExperimentInformationScore,
    ExperimentResult,
    HypothesisBeliefUpdate,
    ScientificDiscoveryProject,
    RelationshipMaturity,
    ScientificHypothesis,
    ScientificPhenomenon,
    ScientificReadinessReport,
    ScientificRelationship,
    ValidatedPrinciple,
)


def _evidence_index(project: ScientificDiscoveryProject) -> dict[str, EvidenceReference]:
    evidence: dict[str, EvidenceReference] = {}
    for phenomenon in project.phenomena:
        for item in phenomenon.evidence:
            evidence[item.id] = item
    for relationship in project.relationships:
        for item in [*relationship.supporting_evidence, *relationship.counterevidence]:
            evidence[item.id] = item
    for result in project.experiment_results:
        for item in result.evidence:
            evidence[item.id] = item
    for principle in project.principles:
        for item in [*principle.supporting_evidence, *principle.counterevidence]:
            evidence[item.id] = item
    return evidence


def build_scientific_relationship_prompt(
    project: ScientificDiscoveryProject,
    *,
    phenomenon_ids: Sequence[str] | None = None,
) -> str:
    """Build a source-grounded prompt to propose relations among observed variables."""
    selected_ids = set(phenomenon_ids or [item.id for item in project.phenomena])
    phenomena = [item for item in project.phenomena if item.id in selected_ids]
    if not phenomena:
        raise ValueError("at least one known scientific phenomenon is required")
    if len(phenomena) != len(selected_ids):
        raise ValueError("relationship prompt references unknown phenomenon IDs")
    variable_ids = {value for item in phenomena for value in item.variable_ids}
    variables = [item for item in project.variables if item.id in variable_ids]
    existing = [
        item for item in project.relationships if set(item.phenomenon_ids) & selected_ids
    ]
    context = {
        "topic": project.discovery_workspace.topic.model_dump(mode="json"),
        "variables": [item.model_dump(mode="json") for item in variables],
        "phenomena": [item.model_dump(mode="json") for item in phenomena],
        "existing_relationships": [item.model_dump(mode="json") for item in existing],
    }
    return f"""你是严格的科学关系发现研究员。请从已记录的变量、观测现象和证据中提出候选关系。

研究范围: {project.discovery_workspace.topic.title}

任务:
1. 区分关联、因果、条件依赖、不变关系和机制关系，不得把相关性直接写成因果。
2. 说明变量的操作化定义、关系方向、适用条件和可能的数学形式。
3. 每个关系必须引用已有 phenomenon ID、variable ID 和原始 evidence ID。
4. 显式列出混杂因素、替代解释、隐含假设和反例。
5. 证据不足时将 maturity 设为 descriptive 或 observationally_supported。
6. 只有存在干预证据时才能提出 intervention_supported；机制支持必须同时有干预和机制分析证据。
7. 不得创建、改写或伪造 evidence reference；supporting_evidence 和 counterevidence 必须逐字复用输入证据对象。
8. 关系的适用范围必须明确；不要将局部实验现象概括成普适定律。

返回纯 JSON 数组。每个对象字段:
id, statement, kind, maturity, phenomenon_ids, variable_ids, mathematical_form,
direction, effect_estimate, uncertainty_interval, scope_conditions, assumptions,
alternative_explanations, supporting_evidence, counterevidence。

可选枚举:
kind = associational | causal | conditional | invariant | mechanistic
maturity = descriptive | observationally_supported | intervention_supported |
           mechanistically_supported | independently_replicated |
           cross_environment_replicated

关系候选必须通过程序验证后才能进入假说生成阶段。

输入材料:
{json.dumps(context, ensure_ascii=False, indent=2)}
"""


def validate_scientific_relationships(
    project: ScientificDiscoveryProject,
    raw_json: str,
) -> list[ScientificRelationship]:
    """Validate relationship references and prohibit invented evidence provenance."""
    relationships = TypeAdapter(list[ScientificRelationship]).validate_json(raw_json)
    known_phenomena = {item.id for item in project.phenomena}
    known_variables = {item.id for item in project.variables}
    known_evidence = _evidence_index(project)
    existing_ids = {item.id for item in project.relationships}
    candidate_ids = [item.id for item in relationships]
    if len(candidate_ids) != len(set(candidate_ids)) or set(candidate_ids) & existing_ids:
        raise ValueError("new scientific relationship IDs must be unique")
    for relationship in relationships:
        if not set(relationship.phenomenon_ids).issubset(known_phenomena):
            raise ValueError(f"relationship {relationship.id} references unknown phenomena")
        if not set(relationship.variable_ids).issubset(known_variables):
            raise ValueError(f"relationship {relationship.id} references unknown variables")
        for evidence in [*relationship.supporting_evidence, *relationship.counterevidence]:
            if known_evidence.get(evidence.id) != evidence:
                raise ValueError(
                    f"relationship {relationship.id} invented or modified evidence {evidence.id}"
                )
    return relationships


def build_scientific_hypothesis_prompt(
    project: ScientificDiscoveryProject,
    *,
    relationship_ids: Sequence[str] | None = None,
) -> str:
    """Generate competing scientific explanations from validated relationships."""
    selected_ids = set(relationship_ids or [item.id for item in project.relationships])
    relationships = [item for item in project.relationships if item.id in selected_ids]
    if not relationships:
        raise ValueError("at least one known scientific relationship is required")
    if len(relationships) != len(selected_ids):
        raise ValueError("hypothesis prompt references unknown relationship IDs")
    evidence = _evidence_index(project)
    evidence_ids = {
        evidence_id
        for relationship in relationships
        for evidence_id in (
            [item.id for item in relationship.supporting_evidence]
            + [item.id for item in relationship.counterevidence]
        )
    }
    context = {
        "topic": project.discovery_workspace.topic.model_dump(mode="json"),
        "relationships": [item.model_dump(mode="json") for item in relationships],
        "evidence": [evidence[item].model_dump(mode="json") for item in sorted(evidence_ids)],
    }
    return f"""你是机器学习与科学发现领域的研究者。请围绕给定关系提出多个相互竞争的科学解释。

任务:
1. 至少提出两个机制上不同、能够相互区分的假说。
2. 每个假说都必须引用已有 relationship IDs 和 evidence IDs。
3. 每个假说至少给出两个可观测预测，明确预测条件、指标、方向和可选定量值。
4. 给出竞争性解释、适用范围和明确的证伪条件。
5. 区分“解释已有数据”与“预测未见数据”；优先产生未见条件下可区分的预测。
6. 不要将一个算法设计包装成科学假说；先解释变量之间为什么存在这种关系。
7. 不能仅凭自然语言置信度宣称因果；因果解释必须明确需要什么干预证据。

返回纯 JSON 数组，元素符合 ScientificHypothesis:
id, title, statement, proposed_mechanism, relationship_ids, scope_conditions,
predictions, competing_explanations, falsification_conditions,
supporting_evidence_ids, counterevidence_ids, status。

每条 prediction 包含:
id, condition, metric, expected_direction (maximize|minimize),
expected_value (optional), tolerance (optional), unit (optional)。

输入材料:
{json.dumps(context, ensure_ascii=False, indent=2)}
"""


def validate_scientific_hypotheses(
    project: ScientificDiscoveryProject,
    raw_json: str,
) -> list[ScientificHypothesis]:
    """Validate hypotheses against registered relationships and evidence lineage."""
    hypotheses = TypeAdapter(list[ScientificHypothesis]).validate_json(raw_json)
    known_relationships = {item.id: item for item in project.relationships}
    known_evidence = _evidence_index(project)
    existing_ids = {item.id for item in project.hypotheses}
    candidate_ids = [item.id for item in hypotheses]
    if len(candidate_ids) != len(set(candidate_ids)) or set(candidate_ids) & existing_ids:
        raise ValueError("new scientific hypothesis IDs must be unique")
    for hypothesis in hypotheses:
        if not set(hypothesis.relationship_ids).issubset(known_relationships):
            raise ValueError(f"hypothesis {hypothesis.id} references unknown relationships")
        allowed_support = {
            item.id
            for relationship_id in hypothesis.relationship_ids
            for item in known_relationships[relationship_id].supporting_evidence
        }
        allowed_counter = {
            item.id
            for relationship_id in hypothesis.relationship_ids
            for item in known_relationships[relationship_id].counterevidence
        }
        if not set(hypothesis.supporting_evidence_ids).issubset(allowed_support):
            raise ValueError(f"hypothesis {hypothesis.id} cites unrelated supporting evidence")
        if not set(hypothesis.counterevidence_ids).issubset(allowed_counter):
            raise ValueError(f"hypothesis {hypothesis.id} cites unrelated counterevidence")
        if not set(hypothesis.supporting_evidence_ids + hypothesis.counterevidence_ids).issubset(
            set(known_evidence)
        ):
            raise ValueError(f"hypothesis {hypothesis.id} references unknown evidence")
    return hypotheses


def build_discriminative_experiment_prompt(
    project: ScientificDiscoveryProject,
    *,
    hypothesis_ids: Sequence[str],
) -> str:
    """Ask for experiments whose predicted outcomes distinguish competing hypotheses."""
    selected_ids = set(hypothesis_ids)
    if len(selected_ids) < 2:
        raise ValueError("at least two competing hypothesis IDs are required")
    hypotheses = [item for item in project.hypotheses if item.id in selected_ids]
    if len(hypotheses) != len(selected_ids):
        raise ValueError("experiment prompt references unknown hypothesis IDs")
    variable_ids = {
        intervention.variable_id
        for experiment in project.experiments
        for intervention in experiment.interventions
    }
    variable_ids.update(
        variable_id
        for relationship in project.relationships
        for variable_id in relationship.variable_ids
    )
    variables = [item for item in project.variables if item.id in variable_ids]
    context = {
        "topic": project.discovery_workspace.topic.model_dump(mode="json"),
        "variables": [item.model_dump(mode="json") for item in variables],
        "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
        "budget_limit": project.discovery_workspace.topic.budget_limit,
        "budget_unit": project.discovery_workspace.topic.budget_unit,
    }
    return f"""你是严谨的实验设计研究员。请设计一个能够区分所选竞争性科学假说的实验。

设计原则:
1. 实验必须让至少两个假说产生不同的结果分布；如果预测完全相同，实验没有区分能力。
2. 明确干预变量、干预类型、控制变量、采样单位和独立性单位。
3. outcome_labels 必须是互斥且穷尽的类别；每个假说对所有类别给出概率，且概率和为 1。
4. 明确主要指标、结果定义、样本量、分析计划、证伪规则和留出条件。
5. 说明随机化、混杂控制、测量误差、统计功效和可能的失效边界。
6. 预算单位必须与主题预算一致；不要虚构可用资源。
7. 预期信息增益由程序根据先验和预测似然计算，不要手工填写或优化一个宣传分数。
8. 设计必须能被预注册，并且清楚标注哪些选择是在观察结果前确定的。

返回一个 DiscriminativeExperiment JSON 对象:
id, title, hypothesis_priors, outcome_labels, predictions, interventions, controls,
primary_metric, outcome_definition, sampling_unit, independence_unit, analysis_plan,
falsification_rule, held_out_conditions, estimated_sample_size, estimated_cost,
cost_unit, preregistration_sha256。

predictions 是数组，每个元素:
hypothesis_id, outcome_probabilities。

输入材料:
{json.dumps(context, ensure_ascii=False, indent=2)}
"""


def validate_discriminative_experiment(
    project: ScientificDiscoveryProject,
    raw_json: str,
) -> DiscriminativeExperiment:
    """Validate experiment hypotheses, intervention variables and topic budget units."""
    experiment = DiscriminativeExperiment.model_validate_json(raw_json)
    known_hypotheses = {item.id for item in project.hypotheses}
    known_variables = {item.id for item in project.variables}
    if not set(experiment.hypothesis_priors).issubset(known_hypotheses):
        raise ValueError("experiment references unknown scientific hypotheses")
    if any(item.variable_id not in known_variables for item in experiment.interventions):
        raise ValueError("experiment references an unknown intervention variable")
    topic = project.discovery_workspace.topic
    if topic.budget_unit is not None and experiment.cost_unit != topic.budget_unit:
        raise ValueError("experiment cost unit does not match the topic budget unit")
    return experiment


def score_discriminative_experiment(
    experiment: DiscriminativeExperiment,
    *,
    budget_limit: float | None = None,
    budget_unit: str | None = None,
) -> ExperimentInformationScore:
    """Compute expected information gain in bits and normalize it by experiment cost."""
    if budget_limit is not None and (budget_limit <= 0 or not math.isfinite(budget_limit)):
        raise ValueError("budget_limit must be a positive finite number")
    if (budget_limit is None) != (budget_unit is None):
        raise ValueError("budget_limit and budget_unit must be supplied together")

    priors = experiment.hypothesis_priors
    predictions = {
        item.hypothesis_id: item.outcome_probabilities for item in experiment.predictions
    }
    if set(priors) != set(predictions):
        raise ValueError("experiment priors and predictions must cover the same hypotheses")

    prior_entropy = -sum(
        probability * math.log2(probability)
        for probability in priors.values()
        if probability > 0
    )
    expected_posterior_entropy = 0.0
    for outcome in experiment.outcome_labels:
        marginal_probability = sum(
            priors[hypothesis_id] * predictions[hypothesis_id][outcome]
            for hypothesis_id in priors
        )
        if marginal_probability <= 0:
            continue
        posterior = {
            hypothesis_id: (
                priors[hypothesis_id] * predictions[hypothesis_id][outcome]
                / marginal_probability
            )
            for hypothesis_id in priors
        }
        posterior_entropy = -sum(
            probability * math.log2(probability)
            for probability in posterior.values()
            if probability > 0
        )
        expected_posterior_entropy += marginal_probability * posterior_entropy

    information_gain = max(0.0, prior_entropy - expected_posterior_entropy)
    within_budget = (
        budget_limit is None
        or (
            budget_unit == experiment.cost_unit
            and experiment.estimated_cost <= budget_limit
        )
    )
    return ExperimentInformationScore(
        experiment_id=experiment.id,
        expected_information_gain_bits=information_gain,
        estimated_cost=experiment.estimated_cost,
        cost_unit=experiment.cost_unit,
        information_gain_per_cost=information_gain / experiment.estimated_cost,
        within_budget=within_budget,
    )


def rank_discriminative_experiments(
    experiments: Sequence[DiscriminativeExperiment],
    *,
    budget_limit: float | None = None,
    budget_unit: str | None = None,
) -> list[ExperimentInformationScore]:
    """Rank feasible experiments by expected information gain per unit cost."""
    cost_units = {experiment.cost_unit for experiment in experiments}
    if len(cost_units) > 1:
        raise ValueError("experiments with different cost units cannot be ranked together")
    scores = [
        score_discriminative_experiment(
            experiment,
            budget_limit=budget_limit,
            budget_unit=budget_unit,
        )
        for experiment in experiments
    ]
    return sorted(
        scores,
        key=lambda item: (
            not item.within_budget,
            -item.information_gain_per_cost,
            -item.expected_information_gain_bits,
            item.experiment_id,
        ),
    )


def update_hypothesis_beliefs(
    experiment: DiscriminativeExperiment,
    result: ExperimentResult,
) -> HypothesisBeliefUpdate:
    """Update hypothesis probabilities from observed categorical outcomes.

    The likelihood assumes conditionally independent categorical observations within
    the declared independence unit. Clustered or sequential observations require a
    different likelihood model and must not be passed as iid counts.
    """
    if result.experiment_id != experiment.id:
        raise ValueError("experiment result references a different experiment")
    observed = result.observed_outcome_counts
    unknown_outcomes = set(observed) - set(experiment.outcome_labels)
    if unknown_outcomes:
        raise ValueError(f"experiment result contains unknown outcomes: {sorted(unknown_outcomes)}")

    predictions = {
        item.hypothesis_id: item.outcome_probabilities for item in experiment.predictions
    }
    total_observations = sum(observed.values())
    log_multinomial = math.lgamma(total_observations + 1) - sum(
        math.lgamma(count + 1) for count in observed.values()
    )
    log_joint: dict[str, float] = {}
    for hypothesis_id, prior in experiment.hypothesis_priors.items():
        if prior <= 0:
            log_joint[hypothesis_id] = -math.inf
            continue
        log_likelihood = log_multinomial + math.log(prior)
        for outcome, count in observed.items():
            if count == 0:
                continue
            probability = predictions[hypothesis_id][outcome]
            if probability <= 0:
                log_likelihood = -math.inf
                break
            log_likelihood += count * math.log(probability)
        log_joint[hypothesis_id] = log_likelihood

    finite_values = [value for value in log_joint.values() if math.isfinite(value)]
    if not finite_values:
        raise ValueError(
            "observed outcomes are impossible under every hypothesis; revise the hypothesis set"
        )
    maximum = max(finite_values)
    log_evidence = maximum + math.log(
        sum(math.exp(value - maximum) for value in finite_values)
    )
    posterior = {
        hypothesis_id: (
            math.exp(value - log_evidence) if math.isfinite(value) else 0.0
        )
        for hypothesis_id, value in log_joint.items()
    }
    return HypothesisBeliefUpdate(
        id=f"belief-{result.id}",
        experiment_id=experiment.id,
        result_id=result.id,
        prior_probabilities=experiment.hypothesis_priors,
        posterior_probabilities=posterior,
        observed_outcome_counts=observed,
        log_evidence=log_evidence,
    )


def assess_scientific_readiness(
    project: ScientificDiscoveryProject,
    *,
    experiment_id: str,
) -> ScientificReadinessReport:
    """Check whether a discriminative experiment is ready for human review."""
    blockers: list[str] = []
    warnings: list[str] = []
    experiment = next(
        (item for item in project.experiments if item.id == experiment_id),
        None,
    )
    if experiment is None:
        return ScientificReadinessReport(
            ready_for_human_review=False,
            selected_experiment_id=experiment_id,
            blockers=["the requested experiment is not registered in the scientific project"],
        )

    if not project.phenomena:
        blockers.append("at least one evidence-backed scientific phenomenon is required")
    if not project.relationships:
        blockers.append("at least one evidence-backed scientific relationship is required")
    if len(experiment.hypothesis_priors) < 2:
        blockers.append("the experiment must distinguish at least two competing hypotheses")

    score = score_discriminative_experiment(experiment)
    if score.expected_information_gain_bits <= 1e-9:
        blockers.append("the proposed experiment has zero expected information gain")
    topic = project.discovery_workspace.topic
    if topic.budget_limit is None or topic.budget_unit is None:
        blockers.append("an explicit topic budget and unit are required")
    elif experiment.cost_unit != topic.budget_unit:
        blockers.append("experiment cost unit does not match the topic budget unit")
    elif experiment.estimated_cost > topic.budget_limit:
        blockers.append("estimated experiment cost exceeds the topic budget")

    if any(
        relationship.maturity
        in {
            RelationshipMaturity.DESCRIPTIVE,
            RelationshipMaturity.OBSERVATIONALLY_SUPPORTED,
        }
        for relationship in project.relationships
    ):
        warnings.append(
            "some relationships remain observational; causal claims require "
            "appropriate interventions"
        )
    if experiment.preregistration_sha256 is None:
        warnings.append("the experiment protocol has not been pinned by a preregistration hash")
    return ScientificReadinessReport(
        ready_for_human_review=not blockers,
        selected_experiment_id=experiment.id,
        expected_information_gain_bits=score.expected_information_gain_bits,
        blockers=blockers,
        warnings=warnings,
    )


def confirm_validated_principle(
    principle: ValidatedPrinciple,
    *,
    human_approved: bool,
) -> ValidatedPrinciple:
    """Register a principle only after explicit human review of its evidence package."""
    if not human_approved:
        raise ValueError("human approval is required before registering a validated principle")
    if principle.reviewed_by_human:
        raise ValueError("principle is already marked as human-reviewed")
    return principle.model_copy(update={"reviewed_by_human": True})


def validate_experiment_result(
    experiment: DiscriminativeExperiment,
    raw_json: str,
) -> ExperimentResult:
    """Validate that observed outcomes and evidence match a registered experiment."""
    result = ExperimentResult.model_validate_json(raw_json)
    if result.experiment_id != experiment.id:
        raise ValueError("experiment result references a different experiment")
    unknown = set(result.observed_outcome_counts) - set(experiment.outcome_labels)
    if unknown:
        raise ValueError(f"experiment result contains unknown outcomes: {sorted(unknown)}")
    return result
