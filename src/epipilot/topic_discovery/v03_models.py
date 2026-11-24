"""V0.3 scientific-discovery contracts with explicit evidence and validation levels."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from epipilot.core.models import utc_now
from epipilot.topic_discovery.models import DiscoveryRecord, DiscoveryWorkspace, MetricDirection


class EvidenceKind(StrEnum):
    LITERATURE = "literature"
    OBSERVATIONAL_DATA = "observational_data"
    INTERVENTION_EXPERIMENT = "intervention_experiment"
    SIMULATION_EXPERIMENT = "simulation_experiment"
    MECHANISTIC_ANALYSIS = "mechanistic_analysis"
    THEORETICAL_DERIVATION = "theoretical_derivation"
    INDEPENDENT_REPLICATION = "independent_replication"


class VariableRole(StrEnum):
    TREATMENT = "treatment"
    OUTCOME = "outcome"
    COVARIATE = "covariate"
    MEDIATOR = "mediator"
    MODERATOR = "moderator"
    CONTEXT = "context"


class RelationshipKind(StrEnum):
    ASSOCIATIONAL = "associational"
    CAUSAL = "causal"
    CONDITIONAL = "conditional"
    INVARIANT = "invariant"
    MECHANISTIC = "mechanistic"


class RelationshipMaturity(StrEnum):
    DESCRIPTIVE = "descriptive"
    OBSERVATIONALLY_SUPPORTED = "observationally_supported"
    INTERVENTION_SUPPORTED = "intervention_supported"
    MECHANISTICALLY_SUPPORTED = "mechanistically_supported"
    INDEPENDENTLY_REPLICATED = "independently_replicated"
    CROSS_ENVIRONMENT_REPLICATED = "cross_environment_replicated"


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    UNDER_TEST = "under_test"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    REVISED = "revised"


class InterventionType(StrEnum):
    RANDOMIZED = "randomized"
    CONTROLLED = "controlled"
    SIMULATION_PERTURBATION = "simulation_perturbation"
    NATURAL_EXPERIMENT = "natural_experiment"


class PrincipleEvidenceLevel(StrEnum):
    OBSERVATIONAL = "observational"
    INTERVENTION_SUPPORTED = "intervention_supported"
    MECHANISTICALLY_SUPPORTED = "mechanistically_supported"
    INDEPENDENTLY_REPLICATED = "independently_replicated"
    CROSS_ENVIRONMENT_REPLICATED = "cross_environment_replicated"


class ScientificVariable(DiscoveryRecord):
    """An operationally defined variable, not an ungrounded natural-language keyword."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=2)
    operational_definition: str = Field(min_length=12)
    role: VariableRole
    measurement_protocol: str = Field(min_length=8)
    unit: str | None = None
    allowed_values: list[str] = Field(default_factory=list)
    measurement_uncertainty: str | None = None


class EvidenceReference(DiscoveryRecord):
    """A source-pinned evidence item with study and environment provenance."""

    id: str = Field(min_length=1)
    kind: EvidenceKind
    source_id: str = Field(min_length=1)
    locator: str = Field(min_length=3)
    study_id: str = Field(min_length=1)
    environment_id: str | None = None
    replication_group_id: str | None = None
    artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    sample_size: int | None = Field(default=None, ge=1)
    supporting_quote: str | None = Field(default=None, min_length=8, max_length=1200)

    @model_validator(mode="after")
    def validate_data_provenance(self) -> Self:
        data_kinds = {
            EvidenceKind.OBSERVATIONAL_DATA,
            EvidenceKind.INTERVENTION_EXPERIMENT,
            EvidenceKind.SIMULATION_EXPERIMENT,
            EvidenceKind.INDEPENDENT_REPLICATION,
        }
        if self.kind in data_kinds and self.artifact_sha256 is None:
            raise ValueError("data and experiment evidence requires an artifact SHA-256")
        if self.kind in data_kinds and self.sample_size is None:
            raise ValueError("data and experiment evidence requires a sample size")
        if self.kind is EvidenceKind.LITERATURE and self.supporting_quote is None:
            raise ValueError("literature evidence requires a source-grounded quote")
        return self


class ScientificPhenomenon(DiscoveryRecord):
    """A reproducible or explicitly provisional empirical pattern to be explained."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=5)
    description: str = Field(min_length=20)
    domain: str = Field(min_length=2)
    variable_ids: list[str] = Field(min_length=2)
    observed_pattern: str = Field(min_length=12)
    observation_conditions: list[str] = Field(min_length=1)
    evidence: list[EvidenceReference] = Field(min_length=1)
    replication_count: int = Field(default=0, ge=0)
    known_exceptions: list[str] = Field(default_factory=list)
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_phenomenon(self) -> Self:
        if len(self.variable_ids) != len(set(self.variable_ids)):
            raise ValueError("phenomenon variable IDs must be unique")
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("phenomenon evidence IDs must be unique")
        if self.replication_count > len({item.study_id for item in self.evidence}):
            raise ValueError("replication_count cannot exceed distinct evidence study IDs")
        return self


class ScientificRelationship(DiscoveryRecord):
    """A testable relation between operationalized variables and observed phenomena."""

    id: str = Field(min_length=1)
    statement: str = Field(min_length=12)
    kind: RelationshipKind
    maturity: RelationshipMaturity = RelationshipMaturity.DESCRIPTIVE
    phenomenon_ids: list[str] = Field(min_length=1)
    variable_ids: list[str] = Field(min_length=2)
    mathematical_form: str | None = None
    direction: str | None = None
    effect_estimate: float | None = None
    uncertainty_interval: tuple[float, float] | None = None
    scope_conditions: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(min_length=1)
    alternative_explanations: list[str] = Field(min_length=1)
    supporting_evidence: list[EvidenceReference] = Field(min_length=1)
    counterevidence: list[EvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_relationship_evidence(self) -> Self:
        if len(self.phenomenon_ids) != len(set(self.phenomenon_ids)):
            raise ValueError("relationship phenomenon IDs must be unique")
        if len(self.variable_ids) != len(set(self.variable_ids)):
            raise ValueError("relationship variable IDs must be unique")
        support_ids = [item.id for item in self.supporting_evidence]
        counter_ids = [item.id for item in self.counterevidence]
        if len(support_ids) != len(set(support_ids)):
            raise ValueError("supporting evidence IDs must be unique")
        if len(counter_ids) != len(set(counter_ids)):
            raise ValueError("counterevidence IDs must be unique")
        if set(support_ids) & set(counter_ids):
            raise ValueError("supporting evidence and counterevidence must be disjoint")
        if self.uncertainty_interval is not None:
            lower, upper = self.uncertainty_interval
            if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
                raise ValueError("uncertainty interval must be finite and ordered")

        all_evidence = [*self.supporting_evidence, *self.counterevidence]
        kinds = {item.kind for item in all_evidence}
        if self.kind is RelationshipKind.CAUSAL and (
            self.maturity
            in {
                RelationshipMaturity.INTERVENTION_SUPPORTED,
                RelationshipMaturity.MECHANISTICALLY_SUPPORTED,
                RelationshipMaturity.INDEPENDENTLY_REPLICATED,
                RelationshipMaturity.CROSS_ENVIRONMENT_REPLICATED,
            }
            and EvidenceKind.INTERVENTION_EXPERIMENT not in kinds
        ):
            raise ValueError("causal maturity requires intervention experiment evidence")
        if (
            self.maturity is RelationshipMaturity.INTERVENTION_SUPPORTED
            and EvidenceKind.INTERVENTION_EXPERIMENT not in kinds
        ):
            raise ValueError("intervention-supported maturity requires intervention evidence")
        if self.maturity is RelationshipMaturity.MECHANISTICALLY_SUPPORTED:
            if EvidenceKind.MECHANISTIC_ANALYSIS not in kinds:
                raise ValueError("mechanistic maturity requires mechanistic analysis evidence")
            if EvidenceKind.INTERVENTION_EXPERIMENT not in kinds:
                raise ValueError("mechanistic maturity requires intervention evidence")
        if self.maturity is RelationshipMaturity.INDEPENDENTLY_REPLICATED:
            studies = {item.study_id for item in self.supporting_evidence}
            if len(studies) < 2 or EvidenceKind.INDEPENDENT_REPLICATION not in kinds:
                raise ValueError(
                    "independent replication requires multiple studies and replication evidence"
                )
        if self.maturity is RelationshipMaturity.CROSS_ENVIRONMENT_REPLICATED:
            environments = {
                item.environment_id
                for item in self.supporting_evidence
                if item.environment_id is not None
            }
            if len(environments) < 2:
                raise ValueError("cross-environment maturity requires at least two environments")
            if len({item.study_id for item in self.supporting_evidence}) < 2:
                raise ValueError("cross-environment maturity requires at least two studies")
        return self


class ScientificPrediction(DiscoveryRecord):
    """A prospective, measurable prediction derived from a scientific hypothesis."""

    id: str = Field(min_length=1)
    condition: str = Field(min_length=8)
    metric: str = Field(min_length=2)
    expected_direction: MetricDirection
    expected_value: float | None = None
    tolerance: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unit: str | None = None


class ScientificHypothesis(DiscoveryRecord):
    """A competing scientific explanation with prospective predictions and rejection rules."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=5)
    statement: str = Field(min_length=15)
    proposed_mechanism: str = Field(min_length=15)
    relationship_ids: list[str] = Field(min_length=1)
    scope_conditions: list[str] = Field(min_length=1)
    predictions: list[ScientificPrediction] = Field(min_length=1)
    competing_explanations: list[str] = Field(min_length=1)
    falsification_conditions: list[str] = Field(min_length=1)
    supporting_evidence_ids: list[str] = Field(min_length=1)
    counterevidence_ids: list[str] = Field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_hypothesis(self) -> Self:
        if len(self.relationship_ids) != len(set(self.relationship_ids)):
            raise ValueError("hypothesis relationship IDs must be unique")
        prediction_ids = [item.id for item in self.predictions]
        if len(prediction_ids) != len(set(prediction_ids)):
            raise ValueError("hypothesis prediction IDs must be unique")
        if len(self.supporting_evidence_ids) != len(set(self.supporting_evidence_ids)):
            raise ValueError("hypothesis supporting evidence IDs must be unique")
        if len(self.counterevidence_ids) != len(set(self.counterevidence_ids)):
            raise ValueError("hypothesis counterevidence IDs must be unique")
        if set(self.supporting_evidence_ids) & set(self.counterevidence_ids):
            raise ValueError("hypothesis support and counterevidence must be disjoint")
        return self


class InterventionSpec(DiscoveryRecord):
    """A planned intervention with a declared target variable and rationale."""

    id: str = Field(min_length=1)
    variable_id: str = Field(min_length=1)
    intervention_type: InterventionType
    intervention_value: str = Field(min_length=2)
    rationale: str = Field(min_length=12)


class HypothesisOutcomePrediction(DiscoveryRecord):
    """A calibrated categorical outcome distribution under one hypothesis."""

    hypothesis_id: str = Field(min_length=1)
    outcome_probabilities: dict[str, float] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_probability_distribution(self) -> Self:
        if any(
            not label.strip() or not math.isfinite(probability) or not 0 <= probability <= 1
            for label, probability in self.outcome_probabilities.items()
        ):
            raise ValueError("outcome probabilities must be finite values in [0, 1]")
        if not math.isclose(sum(self.outcome_probabilities.values()), 1.0, abs_tol=1e-6):
            raise ValueError("outcome probabilities must sum to one")
        return self


class DiscriminativeExperiment(DiscoveryRecord):
    """A preregistrable experiment designed to distinguish competing hypotheses."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=5)
    hypothesis_priors: dict[str, float] = Field(min_length=2)
    outcome_labels: list[str] = Field(min_length=2)
    predictions: list[HypothesisOutcomePrediction] = Field(min_length=2)
    interventions: list[InterventionSpec] = Field(min_length=1)
    controls: list[str] = Field(min_length=1)
    primary_metric: str = Field(min_length=2)
    outcome_definition: str = Field(min_length=12)
    sampling_unit: str = Field(min_length=3)
    independence_unit: str = Field(min_length=3)
    analysis_plan: str = Field(min_length=20)
    falsification_rule: str = Field(min_length=12)
    held_out_conditions: list[str] = Field(min_length=1)
    estimated_sample_size: int = Field(gt=0)
    estimated_cost: float = Field(gt=0, allow_inf_nan=False)
    cost_unit: str = Field(min_length=1)
    preregistration_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_experiment_predictions(self) -> Self:
        prior_ids = set(self.hypothesis_priors)
        if any(
            not hypothesis_id.strip() or not math.isfinite(probability) or not 0 <= probability <= 1
            for hypothesis_id, probability in self.hypothesis_priors.items()
        ):
            raise ValueError("hypothesis priors must be finite probabilities in [0, 1]")
        if not math.isclose(sum(self.hypothesis_priors.values()), 1.0, abs_tol=1e-6):
            raise ValueError("hypothesis priors must sum to one")
        prediction_ids = [item.hypothesis_id for item in self.predictions]
        if len(prediction_ids) != len(set(prediction_ids)):
            raise ValueError("experiment must contain one prediction per hypothesis")
        if set(prediction_ids) != prior_ids:
            raise ValueError("experiment priors and prediction hypothesis IDs must match")
        expected_outcomes = set(self.outcome_labels)
        if len(expected_outcomes) != len(self.outcome_labels):
            raise ValueError("experiment outcome labels must be unique")
        if any(not label.strip() for label in self.outcome_labels):
            raise ValueError("experiment outcome labels must not be blank")
        if any(set(item.outcome_probabilities) != expected_outcomes for item in self.predictions):
            raise ValueError("all hypothesis predictions must use the same outcome labels")
        intervention_ids = [item.id for item in self.interventions]
        if len(intervention_ids) != len(set(intervention_ids)):
            raise ValueError("experiment intervention IDs must be unique")
        if not self.cost_unit.strip():
            raise ValueError("experiment cost unit must not be blank")
        return self


class ExperimentResult(DiscoveryRecord):
    """Observed outcome counts and evidence artifacts from one executed experiment."""

    id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    observed_outcome_counts: dict[str, int]
    evidence: list[EvidenceReference] = Field(min_length=1)
    protocol_deviations: list[str] = Field(default_factory=list)
    completed_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_observed_outcomes(self) -> Self:
        if not self.observed_outcome_counts:
            raise ValueError("experiment result requires observed outcomes")
        if any(
            not label.strip() or isinstance(count, bool) or count < 0
            for label, count in self.observed_outcome_counts.items()
        ):
            raise ValueError("observed outcome counts must be non-negative integers")
        if sum(self.observed_outcome_counts.values()) < 1:
            raise ValueError("experiment result must contain at least one observation")
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("experiment result evidence IDs must be unique")
        return self


class HypothesisBeliefUpdate(DiscoveryRecord):
    """Bayesian posterior update under the experiment's declared categorical likelihood."""

    id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    prior_probabilities: dict[str, float] = Field(min_length=2)
    posterior_probabilities: dict[str, float] = Field(min_length=2)
    observed_outcome_counts: dict[str, int]
    log_evidence: float
    updated_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_belief_update(self) -> Self:
        if set(self.prior_probabilities) != set(self.posterior_probabilities):
            raise ValueError("prior and posterior hypothesis IDs must match")
        for probabilities in (self.prior_probabilities, self.posterior_probabilities):
            if any(
                not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities.values()
            ):
                raise ValueError("belief probabilities must be finite values in [0, 1]")
            if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-6):
                raise ValueError("belief probabilities must sum to one")
        if not math.isfinite(self.log_evidence):
            raise ValueError("log evidence must be finite")
        return self


class ValidatedPrinciple(DiscoveryRecord):
    """An evidence-graded scientific principle; its label never substitutes for review."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=5)
    statement: str = Field(min_length=20)
    mechanism: str = Field(min_length=20)
    relationship_ids: list[str] = Field(min_length=1)
    hypothesis_ids: list[str] = Field(min_length=1)
    evidence_level: PrincipleEvidenceLevel
    causal_claim: bool = False
    supporting_evidence: list[EvidenceReference] = Field(min_length=1)
    counterevidence: list[EvidenceReference] = Field(default_factory=list)
    scope_conditions: list[str] = Field(min_length=1)
    boundary_conditions: list[str] = Field(min_length=1)
    predictive_consequences: list[str] = Field(min_length=1)
    unresolved_limitations: list[str] = Field(min_length=1)
    reviewed_by_human: bool = False
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_evidence_level(self) -> Self:
        support_ids = [item.id for item in self.supporting_evidence]
        counter_ids = [item.id for item in self.counterevidence]
        if len(support_ids) != len(set(support_ids)):
            raise ValueError("principle supporting evidence IDs must be unique")
        if len(counter_ids) != len(set(counter_ids)):
            raise ValueError("principle counterevidence IDs must be unique")
        if set(support_ids) & set(counter_ids):
            raise ValueError("principle support and counterevidence must be disjoint")
        kinds = {item.kind for item in self.supporting_evidence}
        if self.causal_claim and EvidenceKind.INTERVENTION_EXPERIMENT not in kinds:
            raise ValueError("causal principle claims require intervention experiment evidence")
        if self.causal_claim and self.evidence_level is PrincipleEvidenceLevel.OBSERVATIONAL:
            raise ValueError("observational evidence level cannot support a causal principle")
        if (
            self.evidence_level is PrincipleEvidenceLevel.INTERVENTION_SUPPORTED
            and EvidenceKind.INTERVENTION_EXPERIMENT not in kinds
        ):
            raise ValueError("intervention-supported principle requires intervention evidence")
        if self.evidence_level is PrincipleEvidenceLevel.MECHANISTICALLY_SUPPORTED:
            required = {
                EvidenceKind.INTERVENTION_EXPERIMENT,
                EvidenceKind.MECHANISTIC_ANALYSIS,
            }
            if not required.issubset(kinds):
                raise ValueError(
                    "mechanistic principle requires intervention and mechanism evidence"
                )
        if self.evidence_level is PrincipleEvidenceLevel.INDEPENDENTLY_REPLICATED:
            studies = {item.study_id for item in self.supporting_evidence}
            if len(studies) < 2 or EvidenceKind.INDEPENDENT_REPLICATION not in kinds:
                raise ValueError("independently replicated principle requires multiple studies")
        if self.evidence_level is PrincipleEvidenceLevel.CROSS_ENVIRONMENT_REPLICATED:
            environments = {
                item.environment_id
                for item in self.supporting_evidence
                if item.environment_id is not None
            }
            studies = {item.study_id for item in self.supporting_evidence}
            if (
                len(environments) < 2
                or len(studies) < 2
                or EvidenceKind.INDEPENDENT_REPLICATION not in kinds
            ):
                raise ValueError(
                    "cross-environment principle requires two environments, "
                    "two studies and replication evidence"
                )
        return self


class ScientificDiscoveryProject(DiscoveryRecord):
    """Scientific-discovery layer attached to the existing literature and novelty workspace."""

    id: str = Field(min_length=1)
    discovery_workspace: DiscoveryWorkspace
    variables: list[ScientificVariable] = Field(default_factory=list)
    phenomena: list[ScientificPhenomenon] = Field(default_factory=list)
    relationships: list[ScientificRelationship] = Field(default_factory=list)
    hypotheses: list[ScientificHypothesis] = Field(default_factory=list)
    experiments: list[DiscriminativeExperiment] = Field(default_factory=list)
    experiment_results: list[ExperimentResult] = Field(default_factory=list)
    belief_updates: list[HypothesisBeliefUpdate] = Field(default_factory=list)
    principles: list[ValidatedPrinciple] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_project_graph(self) -> Self:
        groups = (
            ("variable", [item.id for item in self.variables]),
            ("phenomenon", [item.id for item in self.phenomena]),
            ("relationship", [item.id for item in self.relationships]),
            ("hypothesis", [item.id for item in self.hypotheses]),
            ("experiment", [item.id for item in self.experiments]),
            ("experiment result", [item.id for item in self.experiment_results]),
            ("belief update", [item.id for item in self.belief_updates]),
            ("principle", [item.id for item in self.principles]),
        )
        for name, identifiers in groups:
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"scientific project {name} IDs must be unique")

        variable_ids = {item.id for item in self.variables}
        phenomenon_ids = {item.id for item in self.phenomena}
        relationship_ids = {item.id for item in self.relationships}
        hypothesis_ids = {item.id for item in self.hypotheses}
        experiment_ids = {item.id for item in self.experiments}
        result_ids = {item.id for item in self.experiment_results}

        evidence_by_id: dict[str, EvidenceReference] = {}

        def register_evidence(items: list[EvidenceReference]) -> None:
            for item in items:
                previous = evidence_by_id.get(item.id)
                if previous is not None and previous != item:
                    raise ValueError(f"evidence ID {item.id} has conflicting provenance")
                evidence_by_id[item.id] = item

        for phenomenon in self.phenomena:
            if not set(phenomenon.variable_ids).issubset(variable_ids):
                raise ValueError(f"phenomenon {phenomenon.id} references unknown variables")
            register_evidence(phenomenon.evidence)
        for relationship in self.relationships:
            if not set(relationship.phenomenon_ids).issubset(phenomenon_ids):
                raise ValueError(f"relationship {relationship.id} references unknown phenomena")
            if not set(relationship.variable_ids).issubset(variable_ids):
                raise ValueError(f"relationship {relationship.id} references unknown variables")
            phenomenon_variables = {
                variable_id
                for phenomenon in self.phenomena
                if phenomenon.id in relationship.phenomenon_ids
                for variable_id in phenomenon.variable_ids
            }
            if not set(relationship.variable_ids).issubset(phenomenon_variables):
                raise ValueError(
                    f"relationship {relationship.id} uses variables outside its phenomena"
                )
            register_evidence(relationship.supporting_evidence)
            register_evidence(relationship.counterevidence)
        known_evidence_ids = set(evidence_by_id)

        relationship_by_id = {item.id: item for item in self.relationships}
        for hypothesis in self.hypotheses:
            if not set(hypothesis.relationship_ids).issubset(relationship_ids):
                raise ValueError(f"hypothesis {hypothesis.id} references unknown relationships")
            allowed_support = {
                item.id
                for relationship_id in hypothesis.relationship_ids
                for item in relationship_by_id[relationship_id].supporting_evidence
            }
            allowed_counter = {
                item.id
                for relationship_id in hypothesis.relationship_ids
                for item in relationship_by_id[relationship_id].counterevidence
            }
            if not set(hypothesis.supporting_evidence_ids).issubset(allowed_support):
                raise ValueError(f"hypothesis {hypothesis.id} cites unrelated supporting evidence")
            if not set(hypothesis.counterevidence_ids).issubset(allowed_counter):
                raise ValueError(f"hypothesis {hypothesis.id} cites unrelated counterevidence")
            referenced_evidence = set(hypothesis.supporting_evidence_ids) | set(
                hypothesis.counterevidence_ids
            )
            if not referenced_evidence.issubset(known_evidence_ids):
                raise ValueError(f"hypothesis {hypothesis.id} references unknown evidence")
        for experiment in self.experiments:
            if not set(experiment.hypothesis_priors).issubset(hypothesis_ids):
                raise ValueError(f"experiment {experiment.id} references unknown hypotheses")
            if any(item.variable_id not in variable_ids for item in experiment.interventions):
                raise ValueError(f"experiment {experiment.id} intervenes on unknown variables")
        experiment_by_id = {item.id: item for item in self.experiments}
        for result in self.experiment_results:
            if result.experiment_id not in experiment_ids:
                raise ValueError(f"experiment result {result.id} references an unknown experiment")
            known_outcomes = set(experiment_by_id[result.experiment_id].outcome_labels)
            if not set(result.observed_outcome_counts).issubset(known_outcomes):
                raise ValueError(f"experiment result {result.id} contains unknown outcomes")
            register_evidence(result.evidence)
        for update in self.belief_updates:
            if update.experiment_id not in experiment_ids:
                raise ValueError(
                    f"belief update references unknown experiment {update.experiment_id}"
                )
            if update.result_id not in result_ids:
                raise ValueError(f"belief update references unknown result {update.result_id}")
            if not set(update.posterior_probabilities).issubset(hypothesis_ids):
                raise ValueError("belief update references unknown hypotheses")
            if not set(update.prior_probabilities).issubset(hypothesis_ids):
                raise ValueError("belief update prior references unknown hypotheses")
        for principle in self.principles:
            if not principle.reviewed_by_human:
                raise ValueError(
                    f"principle {principle.id} requires explicit human review before registration"
                )
            if not set(principle.relationship_ids).issubset(relationship_ids):
                raise ValueError(f"principle {principle.id} references unknown relationships")
            if not set(principle.hypothesis_ids).issubset(hypothesis_ids):
                raise ValueError(f"principle {principle.id} references unknown hypotheses")
            register_evidence(principle.supporting_evidence)
            register_evidence(principle.counterevidence)
        return self


class ExperimentInformationScore(DiscoveryRecord):
    """Expected information gain and cost-normalized score for one experiment."""

    experiment_id: str = Field(min_length=1)
    expected_information_gain_bits: float = Field(ge=0, allow_inf_nan=False)
    estimated_cost: float = Field(gt=0, allow_inf_nan=False)
    cost_unit: str = Field(min_length=1)
    information_gain_per_cost: float = Field(ge=0, allow_inf_nan=False)
    within_budget: bool


class ScientificReadinessReport(DiscoveryRecord):
    """Pre-experiment gate; it never certifies the truth of a scientific claim."""

    ready_for_human_review: bool
    selected_experiment_id: str | None = None
    expected_information_gain_bits: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
    )
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    qualification: str = (
        "This is a pre-experiment completeness and information-gain check. "
        "It does not prove causal validity, statistical power, or hypothesis truth."
    )
