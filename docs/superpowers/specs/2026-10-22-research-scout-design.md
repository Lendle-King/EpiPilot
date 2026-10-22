# Research Scout and decision value V1

Authority: user approved the in-chat V1 design and explicitly authorized managed delivery
for this repository at f0225e595a91f6e63bcb243c76e36d0ce341d040 on
2026-10-22. Work uses the acquired research-scout-v1 lease; no remote publication.

One serial loop: inspect/recover -> bounded scout when required -> feasibility -> decision
score -> atomic forecast/reservation -> existing run/independent verify/settle -> outcome
reconciliation and derived calibration. No second truth database, autonomous daemon,
multi-agent tournament, scientific self-certification, or new dependency.

Scout prepare/submit reuses the host investigator. A request pins repository revision,
canonical event version, scientific epoch, policy and existing authoritative references.
Default bounds: three queries, three read sources, five candidates, four result branches,
three downstream options. No-op epochs are suppressed; cumulative external refresh remains
required at each ten-review boundary. Unknown costs retain reservations for recovery.
Search/interpretation costs share the canonical budget but are not scientific experiments.

Accepted candidates reuse DiscoveryBatch and its exact-source, falsifiability and atomic
canonical registration gates. Reject/defer metadata is auditable. Stale/invalid results
record real costs without partial findings. Git source reads use the pinned object, not
user-checkout existence; external source snapshots bind immutable artifacts and only prove
document observations. No quote alone resolves a scientific question.

Opt-in score: J = lambda * expected downstream decision improvement - weighted fixed-scale
costs - residual risk. Direct benefit defaults to zero; unlock value is included only once
through feasible future choices. Utilities and probabilities are explicit heuristic priors,
not calibrated EVSI. Compare small finite result/decision tables. Include no-change,
inconclusive, operational failure and stop choices. Check feasibility before any score;
legacy scoring must not pre-filter opted-in candidates. Exploration budget protection is
an explicit selection reason and does not alter score components.

Freeze raw and adjusted estimates, candidate comparisons, policy/model/calibration versions,
state/revision and chosen action before execution, atomically with resource reservation.
Post-verification outcomes reference canonical review and settlement. Model-implied branch
gain is not observed VoI; independently unavailable benefits stay unknown. Include failures
and cancellations. Reuse cost-ratio calibration. Probability bias correction uses prior
compatible observed outcomes only, smoothing, normalization and a 20-sample cold start.
Calibration views are reproducible derivatives, not additional canonical events.

Five event families: ResearchPolicyConfigured, ScoutStarted, ScoutCompleted,
DecisionForecastRecorded, DecisionOutcomeRecorded. Stable IDs, append-only typed payloads,
optimistic atomic writes and deterministic replay are mandatory. Policy changes require a
canonical user decision; they do not change ManagedPolicy or research budgets.

Acceptance: synthetic full loop; irrelevant information has zero value; affordable actual
unlocking only; fixed scales; malformed/stale batch rollback with overhead costs retained;
pre-execution forecast gate; idempotent restart; probability cold start/no future leakage;
external provenance cannot promote claims; candidate revision reads; old replay and behavior;
user checkout preserved. Run ruff format/check, strict mypy and complete pytest on exact
candidate. Report empirical policy comparisons without asserting universal improvement.
