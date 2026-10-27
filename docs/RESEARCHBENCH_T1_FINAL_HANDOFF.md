# ResearchBench-T1 Pilot — final local handoff

Date: 2026-10-27
Status: **incomplete Pilot; no holdout evaluation; Full T1 not started**

## Outcome

The eight frozen WDBC strategy/seed cells are terminal. The cumulative ledger records 25 unique model-call attempts out of the 32-call ceiling: 12 successful structured responses, 12 failed calls/responses, and one timeout. There are no open calls or duplicate call IDs. The runner ended with exit status 0 and `incomplete_missing_cells`; no model or training process remains active. No further model or training calls were made during this closeout.

Eight development training attempts completed, using 6.413077 worker seconds in total. Each of the eight measured trials reached the descriptive development target AUROC >= 0.95. Five cells submitted a candidate; three did not. A submitted candidate is not a holdout result. Because three submissions are missing, the frozen evaluator did not materialize or score holdout data. This is not a complete paired comparison and supports no claim that EpiPilot outperformed either baseline.

The requested model and effort remained `gpt-6-luna` / `max`. The WDBC source hash is `fed3eb72d0575ef6192293f5093c6e801b1476b577d0386bf4455504522172ed`; all cells shared the frozen stratified 60/20/20 split (seed 20272025; train 341 rows, dev 114 rows). Dev file SHA-256: `ba4f0ff2eda9f4111cc65f2829f5a6009869acc66facd60737474a5868774c1a`; train file SHA-256: `ce81c90eaf146955abae45bb2f071abaaf153b727adfc19813da5ab1a7c02d7d`. Holdout remained unmaterialized.

## Eight terminal cells

| Strategy | Seed | Calls | Trainings | Best dev AUROC | Best candidate | Terminal state |
|---|---:|---:|---:|---:|---|---|
| Memory Only | 42 | 4 | 0 | — | — | Failed: call budget exhausted |
| Agent Only | 42 | 4 | 2 | 0.9875532263 | Logistic, C=1, numeric_only, no class weighting (`8e6879c4…`) | Submitted |
| Memory Only | 137 | 4 | 1 | 0.9875532263 | Logistic, C=1.0, all features, no class weighting (`bb3f6d84…`) | Submitted; one repeated candidate was recorded, with no replacement attempt |
| EpiPilot Heuristic | 137 | 3 | 1 | 0.9875532263 | Logistic, C=1, all features, no class weighting (`bc833fa4…`) | Submitted from a hash-verified pre-timeout trial; the later timeout remains a failed call and was not retried |
| EpiPilot Conditional | 137 | 2 | 0 | — | — | Strategy stopped on nonpositive estimated net value |
| EpiPilot Heuristic | 42 | 3 | 2 | 0.9882083197 | Logistic, C=0.5, all features, no class weighting (`2cca7317…`) | Submitted |
| Agent Only | 137 | 3 | 2 | 0.9875532263 | Logistic, C=1.0, all features, no class weighting (`bb3f6d84…`) | Submitted |
| EpiPilot Conditional | 42 | 2 | 0 | — | — | Strategy stopped on nonpositive estimated net value |

All candidate details and per-call events remain in the linked recovery evidence. The 25-call cumulative usage audit found token usage for 12 calls and no usage for 13: 151,867 input, 15,097 output, and 11,904 cached input tokens across calls where usage was available. Provider request count and monetary cost were not metered. The Recovery 7 `summary.json` usage object is local to that recovery generation (6 available / 19 missing); the 12/13 totals above are recomputed across the linked ledgers and should be used for cumulative reporting.

Other cumulative ledger counts: 12 invalid-call/response incidents, one repeated-candidate incident, zero unsupported-claim incidents, and zero human interventions. The global ceiling had seven calls unused; unused capacity is not authorization to extend this frozen run.

## Conditional stop audit

Both Conditional cells had **no completed trial, candidate submission, or incumbent** when they made their second decision call. The code always supplies a non-executing `stop` option; it does not require an incumbent before ranking. If `choose_conditional` returns no candidate and the cell history is empty, `run_pilot` records `stopped` with no fallback. The frozen protocol explicitly allows this behavior. This was a real strategy decision made by the implemented policy, not a failed call or an accidental omission of a baseline candidate in the controller.

Recorded decision scores:

| Seed | Proposal | Model-supplied outcome probabilities (improved, no_gain, inconclusive, infrastructure_failure) | VoI | Normalized cost | Risk penalty | Net value |
|---:|---|---|---:|---:|---:|---:|
| 137 | `call2_c1` | 0.18, 0.62, 0.15, 0.05 | 0.000 | 0.300 | 0.030 | -0.330 |
| 137 | `call2_c2` | 0.24, 0.49, 0.19, 0.08 | ≈0.000 | 0.300 | 0.040 | -0.340 |
| 42 | `call2_c1` | 0.16, 0.35, 0.41, 0.08 | ≈0.000 | 0.300 | 0.016 | -0.316 |
| 42 | `call2_c2` | 0.18, 0.34, 0.39, 0.09 | 0.000 | 0.300 | 0.018 | -0.318 |

The code sets estimated candidate cost to 15, total research goal budget to 50, and fixed final-evaluation reserve to 20. `rank_decision` therefore uses `15 / 50 = 0.30` as normalized cost; risk penalty is `0.2 × proposal risk`. The stop threshold is `best.net_value <= 0`. Both decisions had zero calibration samples and all stop/alternative feasibility flags enabled before and after each outcome. The recorded near-zero VoI means the model's supplied branch table did not produce a positive expected value of information under the EpiPilot scoring formula; the controller then subtracted cost and risk and stopped as coded.

This is not a demonstrated dimensional arithmetic bug: the cost is divided by the 50-second goal envelope before being combined with utilities constrained to `[0,1]`. The scale is nevertheless **uncalibrated**: the model supplies the probabilities, branch utilities, and risk, and there are no qualified empirical calibration samples. The event ledger preserves probabilities and derived scores but not the raw branch-utility table, so the supplied utilities and the near-zero VoI cannot be independently reconstructed now. This is an instrumentation gap, not a basis for changing the recorded outcome.

For a new protocol, provide every arm the same frozen, dev-validated baseline incumbent and predefine how its setup cost is charged. Then Conditional can evaluate information relative to a real candidate and stopping can fall back to the shared incumbent. Freeze the utility scale, risk weight, and stopping threshold in advance; retain the small sanitized probability/utility inputs alongside each derived score. These are future-protocol changes, not a reason to patch or rerun these cells.

## Strategy and candidate audit

- **Agent Only** used the ordinary candidate-response path. Its accepted proposals were trained directly; it did not invoke the EpiPilot proposal ranker.
- **Memory Only** used the cell-scoped history/notes path. The linked ledger contains a validated memory-note event for the seed-137 cell. Notes did not cross seed cells. Seed 137's repeated proposal was recorded as a repeat, not trained again; seed 42 never reached a training trial.
- **EpiPilot Heuristic** invoked `rank_proposals` and recorded two-proposal rankings for real decisions. The seed-42 cell selected `call2_p1` and `call3_p1`; both proposals were trained. Seed 137 submitted a real pre-timeout trained candidate after its later call timed out.
- **EpiPilot Conditional** invoked `_next_action(policy="conditional")` in both seeds. Both calls returned `decision_value_stop`; neither candidate was trained.

The seeds did **not** generate different train/dev splits. Every cell used the same frozen split; the cell seed was passed into estimator `random_state`. All eight completed fits selected Logistic Regression; no Random Forest candidate was trained. WDBC has only numeric features, so `feature_mode=all` and `numeric_only` select the same columns for this dataset. This makes that search-space dimension redundant here.

Some distinct hashes also reflect a representation issue. `normalize_candidate` sorts fields but preserves an integer or float as received. Heuristic seed 137 stored `C: 1`, while Agent Only and Memory Only seed 137 stored `C: 1.0`; their hashes differ even though scikit-learn receives the same numeric C value and the remaining parameters match. Those three configurations produced the same dev AUROC. This is a candidate-canonicalization bug that can distort deduplication and hash interpretation; it does not change the measurements already recorded. A minimal future code fix is to canonicalize Logistic `C` to one numeric representation before hashing and duplicate detection. Do not retroactively change these hashes or the Pilot ledger.

The Agent Only seed-42 candidate (`C=1`, numeric_only) and seed-137 candidate (`C=1.0`, all) also achieved the same dev score. Their feature selections are equivalent on this all-numeric dataset, but these are separate fits with different cell seeds. Equal AUROC alone does not establish equal fitted model state. Several cells therefore explored effectively identical Logistic configurations; hash inequality should not be presented as evidence of scientifically distinct models.

## Why a new clean Pilot is needed

This run cannot answer the planned four-policy comparison: three cells have no candidate, five cells submitted, and no holdout score exists. Cells also consumed different numbers of calls and trainings (within the frozen per-cell maxima), including 12 failed responses and one timeout. In addition, each recovery generation was pinned to a different controller revision as infrastructure and strict-schema fixes were made. The recorded revisions were `f47ec2d` (initial freeze), `c7264d3`, `95f9670`, `432a104`, `256e15b`, `cb51ae7`, `9fcf4f5`, and `54b5501` (Recovery 7). These changes were primarily CLI failure capture, strict schema compatibility, lineage/recovery, and preservation of valid prior trials; all existing calls and results remain attached to their original revisions.

Recommended next version, pending a new frozen protocol and budget decision:

1. Freeze one tested controller revision and candidate canonicalization before any scored call. Perform the model/schema qualification before starting the per-cell clock; keep every subsequent failed request charged to its cell.
2. Supply a shared dev-only baseline to all four arms, with the baseline preparation cost explicitly budgeted. Define an explicit fallback-to-baseline rule so every arm can submit a candidate even when its strategy stops early.
3. Keep matched per-cell call/trial caps and report both offered and consumed opportunities. Decide before execution how early stops and invalid responses affect the primary outcome; do not refill consumed budgets or choose winners from holdout results.
4. Use a preregistered Conditional utility scale tied to interpretable units or calibrated historical evidence. Log the compact model-provided decision table and computed components so a stop can be audited.
5. Remove redundant WDBC feature-mode choices or define dataset-specific candidate spaces. Preserve the original holdout firewall and evaluate only after all cells have frozen submissions.

This report does not authorize another call, training, holdout evaluation, Full T1, or a push.

## Local evidence index

Recovery 7 used controller revision `54b5501c33709f5a3141080ed39c50fb66162555` and ended with exit status 0. Recovery 7 SHA-256 values:

- `pilot_work/meta.json`: `7bbe1a782e48351f8b5d47738981725e8f5fcfd76ef1c1f5e2ea10c8ff66423f`
- `pilot_work/events.jsonl`: `7fe65da4518ce4c4e72c6b9ebc2100d3c15410d6a0060dcf53c459ba386e1d3e`
- `summary.json`: `ee2123c0422f1a95e4ebd546a9dd94a0db2e569be9417331d691c7b41891036e`
- `pilot_work/recovery_origin.json`: `b00f2e1622c5f41db4f503aadb92007d0ffd46d497cb7d6ccb1bf165660a7aff`

The immutable Recovery 6 parent hashes still match the values embedded in Recovery 7: manifest/meta `7c35d08cf2203bf4c4d1cf36be5a6dda39a004963cbd2606e7ca973cda87c079`, ledger `506b0d5a752a85a40f4a1df834eb7081824cc44b2bd6b6c19647a05cc9213313`, summary `546512e6eee1ce5f704fce487fd2fcd2d6a521e04e9d2602f1d40fe40f277e53`, and seed-137 heuristic submission `12106bc953bf35025f5c6aaced73f8c800e2c9d3593a2512fe8172da978e38cd`.

Full artifacts remain in the local task directory `T1_Pilot_Recovery_7/`; the root-level `T1_Pilot_Recovery_7_PROGRESS.json` is the linked-ledger cumulative audit. No raw model prompts or full CLI sessions are included in this report.
