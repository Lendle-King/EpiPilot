# ResearchBench-T1 WDBC continuation

**Recorded:** 2026-10-27  
**Status:** completed; local closeout only  
**Execution revision:** `031a41085c7330d634c15ab34aa7996f22acc11e`  
**Parent protocol revision:** `b045629f898e7fea49346cc64a74b452fbe540a9`

## Scope and interpretation

This is the budgeted continuation of the eight WDBC cells from protocol v2. Each strategy kept its own earlier candidate history and development feedback. The continuation used the same frozen stratified WDBC split and estimator seeds 42 and 137. Every model decision used `gpt-6-luna` with reasoning effort `max`.

The holdout had already been evaluated in the parent run. After all continuation calls closed and all eight submissions were frozen, the evaluator descriptively scored the eight submissions and the two common baselines. This was not an independent blind test. No holdout result was sent back to a research agent or used for a continuation decision.

This small run does not show a general strategy winner. Agent Only and EpiPilot Heuristic each finished with a mean development AUROC increase of about `0.000328` across the two paired estimator seeds. Memory Only and EpiPilot Conditional finished at their parent baseline development AUROC. All eight selected candidates and both shared baselines scored `1.0` on the previously opened holdout, so the test result is at ceiling and cannot distinguish the strategies.

## Frozen budget and execution

| Budget | Limit | Actual |
|---|---:|---:|
| New model calls | 4 per cell; 32 total | 21 total: 20 successful responses, 1 timeout |
| New adaptive training | 2 per cell; 16 total | 11 attempts, all 11 completed successfully |
| Time per fit | 15 seconds | 8.920247 seconds total across the 11 fits |
| Model call wall time | — | 538.9112 seconds total |
| Resource guard | 95% whole-machine memory ceiling | Not triggered |

Five new training slots went unused: two in each Memory Only cell and one in EpiPilot Heuristic seed 42. Those Memory Only calls repeatedly proposed candidates already present in their own cell history; the controller logged and skipped the repeats. The Heuristic seed-42 cell retained its valid prior best after one later model call timed out. No call or fit was retried to refill unused capacity.

The 20 calls with usage reported 260,214 input tokens, 18,758 output tokens, and 23,808 cached input tokens. Usage was missing for the timed-out call. Provider request count was not observable and provider cost was not metered, so monetary spend cannot be stated. The continuation recorded nine repeated proposals, zero invalid incidents, zero unsupported claims, and zero controller-recorded human interventions.

The parent v2 segment had 10 calls and 16 training attempts. Including this continuation, those two linked segments total 31 calls and 27 training attempts. Earlier protocol-v1 recovery attempts remain a separate historical run and are not folded into this continuation's outcome.

## Cell-level development history

All eight parent cells began with the same common baseline: Logistic Regression, `C=1`, all features, no class weighting; development AUROC `0.9875532263`. The table also records each cell's parent adaptive candidate and every newly trained candidate. Candidate hashes and full parent/new histories are in the JSON receipt.

| Strategy | Seed | Parent adaptive candidate (dev AUROC) | New adaptive fits (dev AUROC) | New calls | New fits | Unused fit slots | Repeats / termination | Final best dev AUROC |
|---|---:|---|---|---:|---:|---:|---|---:|
| Memory Only | 137 | Logistic `C=0.1` (`0.983623`) | None | 4/4 | 0/2 | 2 | 4 repeats of parent baseline; stopped after repeat proposals | `0.987553` |
| EpiPilot Heuristic | 137 | Logistic `C=0.3` (`0.987226`) | Logistic `C=3` (`0.984933`); `C=0.7` (`0.988208`) | 2/4 | 2/2 | 0 | Completed; selected by measured dev AUROC | `0.988208` |
| Agent Only | 42 | Logistic `C=0.1` (`0.983623`) | Logistic `C=10` (`0.981002`); `C=2` (`0.985915`) | 3/4 | 2/2 | 0 | 1 repeat of parent baseline; retained baseline as best | `0.987553` |
| Agent Only | 137 | Logistic `C=0.1` (`0.983623`) | Logistic `C=10` (`0.981002`); `C=0.5` (`0.988208`) | 2/4 | 2/2 | 0 | Completed; selected by measured dev AUROC | `0.988208` |
| EpiPilot Conditional | 137 | Random Forest, 40 trees, depth 5 (`0.976744`) | Logistic `C=0.3` (`0.987226`); `C=2` (`0.985915`) | 2/4 | 2/2 | 0 | Completed; retained baseline as best | `0.987553` |
| EpiPilot Heuristic | 42 | Logistic `C=0.3` (`0.987226`) | Logistic `C=3` (`0.984933`) | 2/4 | 1/2 | 1 | Second charged call timed out; kept the valid parent baseline | `0.987553` |
| Memory Only | 42 | Logistic `C=0.1` (`0.983623`) | None | 4/4 | 0/2 | 2 | 2 repeats of parent baseline and 2 of parent adaptive candidate; stopped after repeat proposals | `0.987553` |
| EpiPilot Conditional | 42 | Random Forest, 100 trees, depth 4 (`0.979365`) | Logistic `C=0.3` (`0.987226`); `C=0.1` (`0.983623`) | 2/4 | 2/2 | 0 | Completed; retained baseline as best | `0.987553` |

The first new fit in each fitted cell is cumulative fit 3; the second is fit 4. These scores are development-set measurements only. Candidate selection remained based on measured development AUROC. Both Heuristic seed 137 and Agent Only seed 137 improved the parent best; their corresponding seed-42 cells did not. This paired two-seed result is descriptive and too small to support a general performance claim.

## Holdout closeout

The finalization marker records the state `all_submissions_frozen_all_agent_calls_reconciled_final_evaluation_started`, with eight frozen submission hashes. The completion marker records ten evaluations: eight selected policy submissions plus the shared baseline for each estimator seed. The holdout contained 114 rows. All ten AUROC values were `1.0`.

Because this split's holdout had already been evaluated in protocol v2, this is a descriptive re-score on a seen test set. The all-`1.0` ceiling provides no ranking evidence. It does not establish that a strategy generalizes better or improves long-horizon research.

## Closeout correction and evidence

The original runner's derived per-cell summary showed zero successful fits and zero training seconds because terminal fit events do not carry cell metadata. I fixed the summary aggregation to join training-start and terminal events by stable trial ID, and added regression assertions that per-cell successful fits and durations sum to the global values. The receipt recomputes the completed run from the immutable event ledger. The event ledger, frozen manifest, candidate submissions, and experimental outcomes were not changed, and no model, training, or holdout evaluation was rerun for this correction.

The portable receipt [`RESEARCHBENCH_T1_V2_CONTINUATION_RECEIPT.json`](RESEARCHBENCH_T1_V2_CONTINUATION_RECEIPT.json) has full candidate hashes/configuration, parent and continuation development histories, per-cell call/training usage, final selections, holdout outcomes, and hashes of the local evidence artifacts. It does not include raw prompts or model outputs, the event ledger, holdout data or hash, or local machine paths.

This closeout is committed locally on `experiment/researchbench-t1-cost-capped`. It does not publish or push the run, start Full T1, or change earlier v1/v2 results.
