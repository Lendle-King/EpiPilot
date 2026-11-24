"""Topic-to-validated-project discovery contracts."""

from epipilot.topic_discovery.models import (
    AuditVerdict,
    DiscoveryWorkspace,
    ExperimentDesign,
    ExperimentReadiness,
    IdeaCandidate,
    MetricDirection,
    NoveltyAudit,
    NoveltyDifference,
    NoveltyDimension,
    PaperReference,
    PaperSourceKind,
    ResearchGap,
    TopicSpec,
)
from epipilot.topic_discovery.readiness import assess_experiment_readiness

__all__ = [
    "AuditVerdict",
    "DiscoveryWorkspace",
    "ExperimentDesign",
    "ExperimentReadiness",
    "IdeaCandidate",
    "MetricDirection",
    "NoveltyAudit",
    "NoveltyDifference",
    "NoveltyDimension",
    "PaperReference",
    "PaperSourceKind",
    "ResearchGap",
    "TopicSpec",
    "assess_experiment_readiness",
]
