"""Tests for the V0.3 principle-driven scientific discovery protocol."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from epipilot.topic_discovery.cli import main
from epipilot.topic_discovery.models import (
    DiscoveryWorkspace,
    MetricDirection,
    TopicSpec,
)
from epipilot.topic_discovery.scientific_discovery import (
    assess_scientific_readiness,
    build_discriminative_experiment_prompt,
    build_scientific_hypothesis_prompt,
    build_scientific_relationship_prompt,
    confirm_validated_principle,
    rank_discriminative_experiments,
    score_discriminative_experiment,
    update_hypothesis_beliefs,
    validate_discriminative_experiment,
    validate_scientific_hypotheses,
    validate_scientific_relationships,
)
from epipilot.topic_discovery.v03_models import (
    DiscriminativeExperiment,
    EvidenceKind,
    EvidenceReference,
    ExperimentResult,
    HypothesisOutcomePrediction,
    InterventionSpec,
    InterventionType,
    PrincipleEvidenceLevel,
    RelationshipKind,
    RelationshipMaturity,
    ScientificDiscoveryProject,
    ScientificHypothesis,
    ScientificPhenomenon,
    ScientificPrediction,
    ScientificRelationship,
    ScientificVariable,
    ValidatedPrinciple,
    VariableRole,
)


def _topic() -> TopicSpec:
    return TopicSpec(
        id="topic-physical-competence",
        title="Physical competence boundaries for frozen robot skills",
        description=(
            "Study how physical interventions alter the execution region of reusable skills."
        ),
        research_area="Embodied AI",
        target_venues=["ICLR", "ICML"],
        budget_limit=100.0,
        budget_unit="GPU-hour",
    )


def _evidence(
    identifier: str,
    kind: EvidenceKind,
    *,
    study_id: str,
    environment_id: str = "env-a",
    digest_char: str = "a",
) -> EvidenceReference:
    return EvidenceReference(
        id=identifier,
        kind=kind,
        source_id=f"source-{identifier}",
        locator="dataset rows 0-100 and protocol section 2",
        study_id=study_id,
        environment_id=environment_id,
        replication_group_id=f"group-{study_id}",
        artifact_sha256=digest_char * 64,
        sample_size=100,
    )


def _hypothesis(identifier: str, title: str, statement: str) -> ScientificHypothesis:
    return ScientificHypothesis(
        id=identifier,
        title=title,
        statement=statement,
        proposed_mechanism=(
            "The physical contact configuration changes the feasible state transitions "
            "available to a frozen skill."
        ),
        relationship_ids=["relation-contact-delay"],
        scope_conditions=["A fixed frozen skill and a controlled object family."],
        predictions=[
            ScientificPrediction(
                id=f"{identifier}-prediction-1",
                condition="Under a matched contact intervention and held-out delay.",
                metric="skill_success_rate",
                expected_direction=MetricDirection.MAXIMIZE,
                expected_value=0.8,
                tolerance=0.05,
                unit="probability",
            ),
            ScientificPrediction(
                id=f"{identifier}-prediction-2",
                condition="Under a no-contact control with the same observation delay.",
                metric="skill_success_rate",
                expected_direction=MetricDirection.MINIMIZE,
                expected_value=0.4,
                tolerance=0.05,
                unit="probability",
            ),
        ],
        competing_explanations=[
            "Additional state information, rather than contact, explains the effect."
        ],
        falsification_conditions=["A matched non-contact intervention produces the same outcome."],
        supporting_evidence_ids=["evidence-observed"],
        counterevidence_ids=[],
    )


def _experiment(
    identifier: str = "experiment-contact-delay",
    *,
    informative: bool = True,
    cost: float = 20.0,
) -> DiscriminativeExperiment:
    first = (
        {"success": 0.9, "failure": 0.1}
        if informative
        else {
            "success": 0.5,
            "failure": 0.5,
        }
    )
    second = (
        {"success": 0.1, "failure": 0.9}
        if informative
        else {
            "success": 0.5,
            "failure": 0.5,
        }
    )
    return DiscriminativeExperiment(
        id=identifier,
        title="Matched contact intervention under delayed observation",
        hypothesis_priors={"hypothesis-contact": 0.5, "hypothesis-feature": 0.5},
        outcome_labels=["success", "failure"],
        predictions=[
            HypothesisOutcomePrediction(
                hypothesis_id="hypothesis-contact",
                outcome_probabilities=first,
            ),
            HypothesisOutcomePrediction(
                hypothesis_id="hypothesis-feature",
                outcome_probabilities=second,
            ),
        ],
        interventions=[
            InterventionSpec(
                id="intervention-contact",
                variable_id="contact-mode",
                intervention_type=InterventionType.CONTROLLED,
                intervention_value="establish stable object contact",
                rationale="Isolate the contact-mode effect while matching observation delay.",
            )
        ],
        controls=["same object pose", "same frozen skill", "matched observation delay"],
        primary_metric="skill_success_rate",
        outcome_definition="A binary success is a verified completion of the frozen skill.",
        sampling_unit="independent initial physical state",
        independence_unit="independent physical root",
        analysis_plan=(
            "Compare preregistered success proportions across randomized intervention arms."
        ),
        falsification_rule="Reject the contact mechanism if the matched control has equal success.",
        held_out_conditions=["unseen object instance", "unseen contact geometry"],
        estimated_sample_size=80,
        estimated_cost=cost,
        cost_unit="GPU-hour",
        preregistration_sha256="c" * 64,
    )


def _project() -> ScientificDiscoveryProject:
    observational = _evidence(
        "evidence-observed",
        EvidenceKind.OBSERVATIONAL_DATA,
        study_id="study-observational",
        digest_char="a",
    )
    intervention = _evidence(
        "evidence-intervention",
        EvidenceKind.INTERVENTION_EXPERIMENT,
        study_id="study-intervention",
        digest_char="b",
    )
    variables = [
        ScientificVariable(
            id="contact-mode",
            name="Contact mode",
            operational_definition="Discrete contact topology between gripper and object.",
            role=VariableRole.MODERATOR,
            measurement_protocol="Read the simulator contact graph at each control step.",
            unit="categorical",
            allowed_values=["stable", "sliding", "no-contact"],
        ),
        ScientificVariable(
            id="observation-delay",
            name="Observation delay",
            operational_definition="Elapsed time between physical state sampling and policy use.",
            role=VariableRole.TREATMENT,
            measurement_protocol="Measure timestamps at sensor capture and policy receipt.",
            unit="milliseconds",
        ),
        ScientificVariable(
            id="skill-success",
            name="Frozen skill success",
            operational_definition="Whether the frozen policy completes the declared skill goal.",
            role=VariableRole.OUTCOME,
            measurement_protocol="Use the environment's verified terminal success predicate.",
            unit="binary",
            allowed_values=["success", "failure"],
        ),
    ]
    phenomenon = ScientificPhenomenon(
        id="phenomenon-contact-delay",
        title="Contact and delay jointly affect skill completion",
        description=(
            "Observed skill completion rates differ across contact modes under "
            "matched but nonzero observation delays."
        ),
        domain="robot manipulation",
        variable_ids=["contact-mode", "observation-delay", "skill-success"],
        observed_pattern="Success rate changes with contact mode and observation delay.",
        observation_conditions=["Same frozen policy", "matched object family"],
        evidence=[observational, intervention],
        replication_count=2,
    )
    relationship = ScientificRelationship(
        id="relation-contact-delay",
        statement="Contact mode moderates the effect of observation delay on skill success.",
        kind=RelationshipKind.CONDITIONAL,
        maturity=RelationshipMaturity.INTERVENTION_SUPPORTED,
        phenomenon_ids=[phenomenon.id],
        variable_ids=["contact-mode", "observation-delay", "skill-success"],
        mathematical_form="P(success | contact_mode, observation_delay)",
        direction="Stable contact mitigates the negative effect of longer observation delay.",
        effect_estimate=0.2,
        uncertainty_interval=(0.05, 0.35),
        scope_conditions=["Frozen policy", "tested object family", "declared delay range"],
        assumptions=["The success predicate is consistent across intervention arms."],
        alternative_explanations=[
            "Unmeasured object pose differences explain the observed effect."
        ],
        supporting_evidence=[observational, intervention],
        counterevidence=[],
    )
    hypotheses = [
        _hypothesis(
            "hypothesis-contact",
            "Contact-mediated competence",
            "Contact mode causally changes the skill's feasible state-transition region.",
        ),
        _hypothesis(
            "hypothesis-feature",
            "State-feature sufficiency",
            "Contact mode is only a proxy for a richer geometric state representation.",
        ),
    ]
    experiment = _experiment()
    return ScientificDiscoveryProject(
        id="scientific-project-test",
        discovery_workspace=DiscoveryWorkspace(
            id="workspace-test",
            topic=_topic(),
        ),
        variables=variables,
        phenomena=[phenomenon],
        relationships=[relationship],
        hypotheses=hypotheses,
        experiments=[experiment],
    )


def test_expected_information_gain_rewards_distinguishable_predictions() -> None:
    experiment = _experiment()
    score = score_discriminative_experiment(experiment)
    assert score.expected_information_gain_bits == pytest.approx(0.5310044, abs=1e-6)
    assert score.information_gain_per_cost == pytest.approx(
        score.expected_information_gain_bits / 20.0
    )


def test_identical_hypothesis_predictions_have_zero_information_gain() -> None:
    score = score_discriminative_experiment(_experiment(informative=False))
    assert score.expected_information_gain_bits == pytest.approx(0.0, abs=1e-9)


def test_experiment_ranking_respects_budget_and_information_gain_per_cost() -> None:
    expensive = _experiment("experiment-expensive", cost=80.0)
    efficient = _experiment("experiment-efficient", cost=10.0)
    infeasible = _experiment("experiment-over-budget", cost=120.0)
    scores = rank_discriminative_experiments(
        [expensive, efficient, infeasible],
        budget_limit=100.0,
        budget_unit="GPU-hour",
    )
    assert [item.experiment_id for item in scores] == [
        "experiment-efficient",
        "experiment-expensive",
        "experiment-over-budget",
    ]
    assert scores[0].within_budget is True
    assert scores[-1].within_budget is False


def test_bayesian_update_moves_probability_toward_better_predicting_hypothesis() -> None:
    experiment = _experiment()
    result = ExperimentResult(
        id="result-contact-delay",
        experiment_id=experiment.id,
        observed_outcome_counts={"success": 9, "failure": 1},
        evidence=[
            _evidence(
                "evidence-result",
                EvidenceKind.INTERVENTION_EXPERIMENT,
                study_id="study-result",
                digest_char="d",
            )
        ],
    )
    update = update_hypothesis_beliefs(experiment, result)
    assert update.posterior_probabilities["hypothesis-contact"] > 0.999
    assert update.posterior_probabilities["hypothesis-feature"] < 0.001
    assert update.result_id == result.id


def test_bayesian_update_rejects_outcomes_impossible_under_every_hypothesis() -> None:
    experiment = _experiment(informative=False)
    predictions = [
        item.model_copy(update={"outcome_probabilities": {"success": 0.0, "failure": 1.0}})
        for item in experiment.predictions
    ]
    impossible = experiment.model_copy(update={"predictions": predictions})
    result = ExperimentResult(
        id="result-impossible",
        experiment_id=impossible.id,
        observed_outcome_counts={"success": 1},
        evidence=[
            _evidence(
                "evidence-impossible",
                EvidenceKind.INTERVENTION_EXPERIMENT,
                study_id="study-impossible",
                digest_char="e",
            )
        ],
    )
    with pytest.raises(ValueError, match="impossible under every hypothesis"):
        update_hypothesis_beliefs(impossible, result)


def test_scientific_project_checks_cross_object_references() -> None:
    project = _project()
    assert project.relationships[0].phenomenon_ids == ["phenomenon-contact-delay"]
    payload = project.model_dump()
    payload["hypotheses"][0]["relationship_ids"] = ["missing-relationship"]
    with pytest.raises(ValidationError, match="references unknown relationships"):
        ScientificDiscoveryProject.model_validate(payload)


def test_causal_relationship_cannot_claim_intervention_support_without_intervention_data() -> None:
    project = _project()
    relationship = project.relationships[0].model_copy(
        update={
            "kind": RelationshipKind.CAUSAL,
            "supporting_evidence": [project.phenomena[0].evidence[0]],
        }
    )
    with pytest.raises(ValidationError, match="requires intervention experiment evidence"):
        ScientificRelationship.model_validate(relationship.model_dump())


def test_counterevidence_cannot_satisfy_a_causal_evidence_gate() -> None:
    project = _project()
    relationship = project.relationships[0].model_dump(mode="json")
    relationship["kind"] = RelationshipKind.CAUSAL.value
    relationship["supporting_evidence"] = [
        project.phenomena[0].evidence[0].model_dump(mode="json")
    ]
    relationship["counterevidence"] = [
        project.phenomena[0].evidence[1].model_dump(mode="json")
    ]
    with pytest.raises(
        ValidationError,
        match="causal maturity requires intervention experiment evidence",
    ):
        ScientificRelationship.model_validate(relationship)


def test_hypothesis_validator_requires_competing_hypotheses() -> None:
    project = _project()
    candidate = _hypothesis(
        "hypothesis-new-only",
        "A single proposed explanation",
        "Contact mode changes the reachable state set under delayed observation.",
    )
    with pytest.raises(ValueError, match="at least two competing scientific hypotheses"):
        validate_scientific_hypotheses(
            project,
            json.dumps([candidate.model_dump(mode="json")]),
        )


def test_project_registers_result_and_bayesian_update_together() -> None:
    project = _project()
    experiment = project.experiments[0]
    result = ExperimentResult(
        id="result-registered",
        experiment_id=experiment.id,
        observed_outcome_counts={"success": 9, "failure": 1},
        evidence=[
            _evidence(
                "evidence-registered-result",
                EvidenceKind.INTERVENTION_EXPERIMENT,
                study_id="study-registered-result",
                digest_char="f",
            )
        ],
    )
    update = update_hypothesis_beliefs(experiment, result)
    payload = project.model_dump(mode="json")
    payload["experiment_results"] = [result.model_dump(mode="json")]
    payload["belief_updates"] = [update.model_dump(mode="json")]
    registered = ScientificDiscoveryProject.model_validate(payload)
    assert registered.belief_updates[0].result_id == result.id


def test_mechanistic_principle_requires_intervention_and_mechanism_evidence() -> None:
    observational = _evidence(
        "principle-observation",
        EvidenceKind.OBSERVATIONAL_DATA,
        study_id="principle-study",
    )
    with pytest.raises(ValidationError, match="requires intervention and mechanism evidence"):
        ValidatedPrinciple(
            id="principle-invalid",
            title="A proposed physical principle",
            statement=(
                "Contact mode changes the feasible state-transition region of a frozen skill."
            ),
            mechanism="Contact constraints remove physically infeasible state transitions.",
            relationship_ids=["relation-contact-delay"],
            hypothesis_ids=["hypothesis-contact"],
            evidence_level=PrincipleEvidenceLevel.MECHANISTICALLY_SUPPORTED,
            supporting_evidence=[observational],
            scope_conditions=["Fixed frozen skill"],
            boundary_conditions=["Within the tested contact regime"],
            predictive_consequences=["Predict success under unseen contact configurations."],
            unresolved_limitations=[
                "The result has not been replicated across robot morphologies."
            ],
        )


def test_independently_replicated_principle_requires_multiple_studies() -> None:
    one_study = _evidence(
        "replication-one",
        EvidenceKind.INDEPENDENT_REPLICATION,
        study_id="replication-study-one",
    )
    with pytest.raises(ValidationError, match="requires multiple studies"):
        ValidatedPrinciple(
            id="principle-replication-invalid",
            title="A replicated physical principle",
            statement=(
                "Contact mode changes the feasible state-transition region of a frozen skill."
            ),
            mechanism="Contact constraints remove physically infeasible state transitions.",
            relationship_ids=["relation-contact-delay"],
            hypothesis_ids=["hypothesis-contact"],
            evidence_level=PrincipleEvidenceLevel.INDEPENDENTLY_REPLICATED,
            supporting_evidence=[one_study],
            scope_conditions=["Fixed frozen skill"],
            boundary_conditions=["Within the tested contact regime"],
            predictive_consequences=["Predict success under unseen contact configurations."],
            unresolved_limitations=["The result has not been tested on real hardware."],
        )


def test_validated_principle_requires_explicit_human_review() -> None:
    project = _project()
    principle = ValidatedPrinciple(
        id="principle-contact-boundary",
        title="Contact-conditioned skill competence",
        statement="Contact mode changes the feasible state-transition region of a frozen skill.",
        mechanism="Contact constraints remove physically infeasible state transitions.",
        relationship_ids=["relation-contact-delay"],
        hypothesis_ids=["hypothesis-contact"],
        evidence_level=PrincipleEvidenceLevel.INTERVENTION_SUPPORTED,
        causal_claim=True,
        supporting_evidence=project.relationships[0].supporting_evidence,
        scope_conditions=["Fixed frozen skill", "Tested object family"],
        boundary_conditions=["Within the tested contact regime"],
        predictive_consequences=["Predict success under unseen contact configurations."],
        unresolved_limitations=["The result has not been tested on real hardware."],
    )
    with pytest.raises(ValueError, match="human approval is required"):
        confirm_validated_principle(principle, human_approved=False)

    approved = confirm_validated_principle(principle, human_approved=True)
    payload = project.model_dump()
    payload["principles"] = [approved.model_dump()]
    registered = ScientificDiscoveryProject.model_validate(payload)
    assert registered.principles[0].reviewed_by_human is True


def test_scientific_project_rejects_unreviewed_principles() -> None:
    project = _project()
    principle = ValidatedPrinciple(
        id="principle-unreviewed",
        title="Contact-conditioned skill competence",
        statement="Contact mode changes the feasible state-transition region of a frozen skill.",
        mechanism="Contact constraints remove physically infeasible state transitions.",
        relationship_ids=["relation-contact-delay"],
        hypothesis_ids=["hypothesis-contact"],
        evidence_level=PrincipleEvidenceLevel.INTERVENTION_SUPPORTED,
        supporting_evidence=project.relationships[0].supporting_evidence,
        scope_conditions=["Fixed frozen skill"],
        boundary_conditions=["Within the tested contact regime"],
        predictive_consequences=["Predict success under unseen contact configurations."],
        unresolved_limitations=["The result has not been tested on real hardware."],
    )
    payload = project.model_dump()
    payload["principles"] = [principle.model_dump()]
    with pytest.raises(ValidationError, match="requires explicit human review"):
        ScientificDiscoveryProject.model_validate(payload)


def test_scientific_readiness_requires_positive_information_gain_and_budget() -> None:
    project = _project()
    report = assess_scientific_readiness(
        project,
        experiment_id="experiment-contact-delay",
    )
    assert report.ready_for_human_review is True
    assert report.expected_information_gain_bits == pytest.approx(0.5310044, abs=1e-6)

    no_information = _experiment(informative=False)
    changed = project.model_copy(update={"experiments": [no_information]})
    blocked = assess_scientific_readiness(changed, experiment_id=no_information.id)
    assert blocked.ready_for_human_review is False
    assert any("zero expected information gain" in item for item in blocked.blockers)


def test_relationship_prompt_and_validator_preserve_registered_evidence() -> None:
    project = _project()
    prompt = build_scientific_relationship_prompt(project)
    assert "不得把相关性直接写成因果" in prompt
    candidate = project.relationships[0].model_copy(update={"id": "relation-new"})
    validated = validate_scientific_relationships(
        project,
        json.dumps([candidate.model_dump(mode="json")]),
    )
    assert validated[0].id == "relation-new"

    invalid = candidate.model_copy(
        update={
            "supporting_evidence": [
                candidate.supporting_evidence[0].model_copy(
                    update={"source_id": "fabricated-source"}
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="invented or modified evidence"):
        validate_scientific_relationships(
            project,
            json.dumps([invalid.model_dump(mode="json")]),
        )


def test_hypothesis_prompt_and_validator_require_competing_explanations() -> None:
    project = _project()
    prompt = build_scientific_hypothesis_prompt(project)
    assert "至少提出两个机制上不同" in prompt
    candidates = [
        _hypothesis(
            "hypothesis-new-a",
            "Contact causality",
            "Contact mode changes the reachable state set under delayed observation.",
        ),
        _hypothesis(
            "hypothesis-new-b",
            "Geometry proxy",
            "Contact mode proxies a latent geometric feature that predicts success.",
        ),
    ]
    validated = validate_scientific_hypotheses(
        project,
        json.dumps([item.model_dump(mode="json") for item in candidates]),
    )
    assert len(validated) == 2


def test_experiment_prompt_and_validator_require_competing_hypotheses() -> None:
    project = _project()
    prompt = build_discriminative_experiment_prompt(
        project,
        hypothesis_ids=["hypothesis-contact", "hypothesis-feature"],
    )
    assert "不同的结果分布" in prompt
    candidate = _experiment("experiment-new")
    validated = validate_discriminative_experiment(
        project,
        candidate.model_dump_json(),
    )
    assert validated.id == "experiment-new"


def test_cli_scores_experiments_and_writes_information_gain_report(
    tmp_path: Path,
) -> None:
    experiment = _experiment()
    (tmp_path / "experiments.json").write_text(
        json.dumps([experiment.model_dump(mode="json")]),
        encoding="utf-8",
    )
    result = main(
        [
            "--root",
            str(tmp_path),
            "scientific-score-experiments",
            "--experiments",
            "experiments.json",
            "--budget",
            "100",
            "--budget-unit",
            "GPU-hour",
            "--output",
            "scores.json",
        ]
    )
    assert result == 0
    payload = json.loads((tmp_path / "scores.json").read_text(encoding="utf-8"))
    assert payload[0]["expected_information_gain_bits"] == pytest.approx(0.5310044, abs=1e-6)


def test_cli_scientific_readiness_writes_gate_report(tmp_path: Path) -> None:
    project = _project()
    (tmp_path / "project.json").write_text(project.model_dump_json(), encoding="utf-8")
    result = main(
        [
            "--root",
            str(tmp_path),
            "scientific-readiness",
            "--project",
            "project.json",
            "--experiment-id",
            "experiment-contact-delay",
            "--output",
            "readiness.json",
        ]
    )
    assert result == 0
    payload = json.loads((tmp_path / "readiness.json").read_text(encoding="utf-8"))
    assert payload["ready_for_human_review"] is True


def test_experiment_ranking_rejects_incomparable_cost_units() -> None:
    first = _experiment("experiment-gpu", cost=10.0)
    second = _experiment("experiment-cloud", cost=10.0).model_copy(update={"cost_unit": "USD"})
    with pytest.raises(ValueError, match="different cost units"):
        rank_discriminative_experiments([first, second])


def test_project_rejects_hypothesis_evidence_outside_its_relationships() -> None:
    project = _project()
    payload = project.model_dump()
    payload["hypotheses"][0]["supporting_evidence_ids"] = ["unrelated-evidence"]
    with pytest.raises(ValidationError, match="cites unrelated supporting evidence"):
        ScientificDiscoveryProject.model_validate(payload)
