# Research Scout and decision-aware CARTS (V1)

V1 is opt-in and serial. The configured host coding agent supplies semantic discovery
and public literature retrieval; EpiPilot checks its bounded structured submissions,
accounts costs, selects experiments, freezes predictions and verifies results. There
is no background scheduler or implicit model call. Existing projects without a research
policy retain their existing selection and event history.

## One loop

`iterate -> scout prepare/submit when due -> start/run -> record/verify -> iterate`

Recover active operations first. `iterate` and `next` are read-only. New measured reviews,
explicit decisions/requirements, policy versions or adopted repository revisions allow
new Scout epochs. Audit events and Scout-created hypotheses do not create new epochs.
An unchanged failed/no-op epoch cannot be retried under another operation name. Record
an actual changed strategy as a canonical decision or acquire new evidence first.

Scout is exposed as a request/submission protocol, so an existing agent can perform the
search with its already authorized tools. After `prepare`, copy its canonical version,
authority, root and revision exactly. Read relevant pinned files, claim observations,
negative results and open questions. Investigate objective gaps, contradictions, changed
failure conditions and transferable mechanisms. Do not dump repository/transcript history.

## Configure once

Use the existing project and database, initialized with an explicit authorized goal.
Use global flags before the action:

```text
epi --root /research --project PROJECT --db /private/workspace.sqlite3 scout configure --file research-policy.json
epi --root /research --project PROJECT --db /private/workspace.sqlite3 scout prepare --operation STABLE-ID
epi --root /research --project PROJECT --db /private/workspace.sqlite3 scout schema
epi --root /research --project PROJECT --db /private/workspace.sqlite3 scout submit --file scout-result.json
```

Example policy, replacing `basis_ref` with the existing canonical USER decision ID and
amounts with the explicitly authorized research resource scales/reservation:

```json
{
  "id": "decision-v1",
  "basis_ref": "CANONICAL-USER-DECISION-ID",
  "utility_version": "goal-v1",
  "information_weight": 1.0,
  "risk_weight": 0.2,
  "cost_scales": [{"unit": "wall_seconds", "amount": 120}],
  "cost_weights": [{"unit": "wall_seconds", "amount": 1}],
  "scout_cost": [{"unit": "wall_seconds", "amount": 5}]
}
```

Defaults cap one Scout at three queries, three anchored sources and five candidates;
a decision estimate has four result branches and at most three options. These are
engineering bounds, not claims of optimality. The host/tool must enforce time and query
limits before spending. Overruns are retained and reduce the remaining shared budget.
Changing a policy appends a new ID linked to a user decision, never resets the budget or
changes immutable managed Git authority. Unknown costs leave the reservation open for
recovery: do not submit zero merely because usage was not captured.

## Submission and provenance

`scout schema` emits the strict `ScoutSubmission` schema. Supply `operation_id`, measured
`actual_cost`, `batch` (existing DiscoveryBatch), candidates, decision estimates and actual
public query strings. A justified empty discovery is valid. Accepted candidate topic,
question and alternatives must agree with its Finding. Sources must be referenced by exact
`git:SHA:path:line` or external snapshot ID. New hypotheses remain PROPOSED.

Keep a Git anchor pinned to a regular file object; its presence in the user's current
checkout is unnecessary. For external material, `batch.external_sources` binds a source
URL/title, access time, publication time when known, access level, original locator, quote,
relative output artifact and SHA-256. Save concise permitted source notes in the existing
project artifact output (the CLI's global `--output` can select it). Files outside that
output, tampered hashes and mismatching quotes reject the batch. Leads/failed reads cannot
support findings. Stored notes verify document wording only, not remote authenticity,
methodology, local measured effect, first-publication time or scientific acceptance.
Never send secrets or private claim text in search queries. Related versions/mirrors do
not constitute independent replication.

External review becomes due at each configured ten-review boundary, reusing the cumulative
synthesis protocol and source ledger. A local result without the required external reading
is explicitly reported incomplete; it cannot be presented as a completed literature review.

Stale/invalid submissions settle actual overhead without accepting any findings or estimates.
Strict JSON/schema errors occur before service admission: the reservation stays open. Correct
that submission (or submit a justified empty batch with the measured usage) under the same
operation; do not launch another search or invent zero usage to clear it.
Unknown usage remains reserved. Repeating the identical submitted payload returns the same
receipt; changing an already completed payload is rejected. A competing event writer leaves
an optimistic-concurrency conflict and the original reservation for explicit recovery.

## Decision tables and score

Each estimate identifies its proposal, predictor, goal utility version, rationale/basis and
at most three named downstream options. Each option has a normalized prior utility; an
option tied to a future experiment names its canonical proposal ID. Include at least one
non-executing stop/defer choice. Provide branches in this order:

1. sufficient independently measured support;
2. sufficient independently measured definitive refutation;
3. inconclusive (including evidence below scientific thresholds);
4. execution or verification failure/cancellation.

Branch probabilities sum to one. Each branch has one conditional expected utility per
option, in the same order. Their weighted means must equal the stated prior utilities.
Utilities lie in [0,1] under the same explicitly chosen goal rubric. They are heuristic
forecasts, not facts or evidence. Do not choose a fresh utility scale per experiment.

For example, with branch probabilities [0.25,0.25,0.25,0.25], utilities for continue/stop
can be [[0.8,0.2],[0.2,0.6],[0.5,0.4],[0.5,0.4]], with prior [0.5,0.4]. The expected
benefit of adapting the choice is 0.1. A table that always favors the same option has
zero decision value even if it reduces an unrelated hypothesis's uncertainty.

The V1 score is `lambda * decision_value - sum(weight[k]*cost[k]/scale[k]) - residual_risk`.
Cost scales are fixed policy values; omitted cost weights mean one per declared unit.
Direct gains default to zero and unlocking is included in the decision table only once.
Future experiments must exist, have registered contracts/qualified inputs and resources,
and be jointly affordable with the current experiment. Only direct AND/OR prerequisite
support can be forecast as unlocking; V1 conservatively excludes fidelity-dependent
future experiments rather than inventing their measurements. Other hard gates still apply.
The score does not divide by cost again. Exploration protection can alter selection but
is recorded as a reason without altering the numeric score.

## Frozen forecasts, outcomes and calibration

Starting an opted-in experiment atomically persists its DecisionForecast and reservation.
It includes raw/adjusted probabilities, raw/calibrated costs, masks, scores, considered
finalists, policy/calibration versions, repository SHA and state version. A rejected
reservation leaves no forecast. Standalone forecast writes and replay streams with unpaired
forecasts are rejected. Forecasts bind the accepted Scout revision/epoch, and each accepted
Scout completion replaces the current estimate set instead of merging stale estimates.
Forged/stale forecasts are rejected by deterministic replay.

Existing verification and settlement automatically produce one DecisionOutcome. Restarting
`verify/resume` fills a missing outcome and never duplicates it. Outcomes use canonical
quality, reliability, samples, scientific scope and definitive-negative rules. Merely
running successfully or verifying a file does not count as scientific support.

Actual cost includes measurement and verification; Scout overhead is counted separately in
the same budget. Cost calibration retains CARTS' clipped median actual/raw-estimate ratios.
Probability correction uses prior compatible type/fidelity/scope/predictor/utility records:
smoothed mean residual against raw probabilities, clipped positive and renormalized. Fewer
than twenty comparable samples leave probabilities unchanged. This is an explicit heuristic;
it is not a claim of improved calibration or calibrated EVSI. Recompute table prior means
under corrected probabilities so changing beliefs is not counted as a switching benefit.

Reports show raw forecasts, actual costs, Brier scores, model-implied branch gains and
verified unlocks. `observed_gain` remains null in V1: a model-implied gain or changed
selection is not observed VoI. Unexecuted candidates have no observed outcome. Derived
calibration hashes depend only on earlier canonical outcome records and policy/algorithm;
no separate calibration database or canonical update event is needed.

## Delivery and compatibility

Five additive typed event families share `project_events`: policy configured, Scout started,
Scout completed, decision forecast recorded and decision outcome recorded. Existing events
and unconfigured projects replay with empty optional metadata. Read status/report for all
records; AgentPacket includes the effective research policy and active Scout request.

Ordinary search/measurement/event operations do not create Git worktrees or push. Code and
versioned report edits use `work acquire/complete`; keep `--root` canonical, use the lease cwd,
read the integrated SHA and refresh stale discovery/forecast inputs. Publication is a separate
explicit milestone operation and never scientific acceptance. Do not checkout/reset/stash
or stage the user's working copy to make new source files visible.
