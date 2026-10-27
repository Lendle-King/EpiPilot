# ResearchBench-T1 — bounded Mac qualification ledger

Updated: 2026-10-25. **Experimental evidence only; no four-arm performance claim.**
All tasks below were dispatched by the private `Lendle-King/Remote-Control`
GitHub Actions runner and pinned to an exact EpiPilot experiment commit.
No secret test labels from the research pilot were released to the Agent.

| GitHub run | Phase | Outcome | Measured evidence | Limits |
| --- | --- | --- | --- | --- |
| [40191319290](https://github.com/Lendle-King/Remote-Control/actions/runs/40191319290) | Mac and Codex readiness | Success | macOS ARM64; Codex CLI installed; ChatGPT login status succeeded; zero model invocations | Did not select or score a particular model |
| [40192249930](https://github.com/Lendle-King/Remote-Control/actions/runs/40192249930) | Offline WDBC smoke | Success | 2 real training/validation trials, approximately 8.55 s combined trial cost; independent smoke holdout AUROC about 0.99769; zero model invocations | Different preflight split from pilot, and an easy problem with ceiling effects |
| [40192580613](https://github.com/Lendle-King/Remote-Control/actions/runs/40192580613) | Agent-only, first bounded Codex process | Rejected | One Codex CLI invocation attempted; invalid model output format; **no qualified development trial** | Provider API call count and dollar spend were not metered |
| [40195783939](https://github.com/Lendle-King/Remote-Control/actions/runs/40195783939) | Agent-only, single bounded format retry | **Success** | One Codex CLI invocation; strict JSON candidate accepted; one real T1 development trial and one submission completed; Codex subprocess 11.914 s, total orchestration 23.666 s | No final holdout; no comparison against other policies; internal provider request count and actual monetary cost unknown |

## Resource-safe Mac continuation — 2026-10-25

The following **zero-model** probes were performed after the bounded
agent-only format qualification:

| GitHub Run | Mode | Verified outcome |
| --- | --- | --- |
| [40198426212](https://github.com/Lendle-King/Remote-Control/actions/runs/40198426212) | resource_probe, original threshold | Success; Mac physical RAM 24.0 GiB, available 5.84 GiB, CPU 26.9%; no Codex invocation |
| [40198975172](https://github.com/Lendle-King/Remote-Control/actions/runs/40198975172) | resource_probe, predicted peak guard | Success; Mac RAM 24.0 GiB, available **5.87 GiB**, CPU **30.2%**; 4.32 GiB free-memory reserve plus 1.5 GiB Codex RSS cap plus 0.5 GiB uncertainty = **6.32 GiB required**. The probe explicitly returned `codex_launch_eligible=false`. **Zero** model attempts. |

The next proposed agent-only seed (137) was **NOT launched**. This was
an intentional resource-based stop, not an experimental or scientific failure.
The new `resource_guard.py` adds OS low priority, single-worker scheduling,
one numerical thread, periodic process-tree RSS monitoring, process-count
limits, CPU pressure refusal, and sustained pressure termination. These
are sampled user-space guards and are **not** kernel-enforced cgroup limits.

The private Remote-Control task workflow was disabled immediately after
this resource finding; a later model call requires a newly reviewed
resource gate, a fresh task authorization and fixed source code. Full
four-arm or three-dataset comparisons have not been started.

The successful retry used frozen EpiPilot commit
`573000e4fbad96cfa3bfcbf70399fe8b9a83cbd5`. Its GitHub Actions
sanitized receipt artifact is
`epipilot-t1-40195783939-1`, digest
`sha256:65d65d593fceb45393413c5da08d2d534d9c608cdb3da12eb833438e3871b1e6`.
It records `trial_completed=true`, `model_output_format=strict_json`,
`codex_process_attempts=1` and
`candidate_sha256=8c34f0ca5fe2d5c5821c08f7f2591df3af5e6cea9c2949db43c81207b66bb535`
(the original proposal-file bytes).

**Known instrumentation defect in that historical receipt:** the field
`development_cost_seconds` was mistakenly read from `charged_seconds`
instead of the genuine T1 trial `seconds`, resulting in `null`. The
development AUROC was also omitted from the uploaded sanitized summary,
although T1 accepted the trial and submission. The subsequent patch adds
strict trial–submission provenance checks and exports those metrics for
**future** runs. It does not fabricate or retroactively infer missing values.

## Protocol version boundary — strict Luna model / 95% total RAM

Effective for future executions, the T1 experiment runner must request
**only** `codex exec --model 'gpt-6-luna(max)'` and rejects all alternative
model names, including the former `default`. The Mac watchdog monitors
**only global physical-memory usage at the 95% threshold**; previous
per-process RSS/CPU/process-count gates were removed.

The previous successful seed 42/137 sessions used Codex `default`.
Those outcomes are **not same-model evidence for a Luna-locked Pilot** and
cannot be treated as completed matched controls under the revised protocol.
A failed exact-Luna call must stop without retrying with a different model.

## Strict Luna / 95%-only RAM follow-up — 2026-10-25

This protocol removed per-process RSS, CPU-load and process-count hard gates
and monitored **only whole-Mac physical memory used <=95%**. Codex was
required to receive the explicit `--model 'gpt-6-luna(max)'` argument.

| GitHub run | Result | Observation |
| --- | --- | --- |
| [40201406655](https://github.com/Lendle-King/Remote-Control/actions/runs/40201406655) | **Pass** (zero-model resource probe) | 24 GiB total RAM, 10.90 GiB available, 54.6% whole-Mac memory used, limit 95%; no model invocation |
| [40201519820](https://github.com/Lendle-King/Remote-Control/actions/runs/40201519820) | **Rejected**, model-only qualification | One `gpt-6-luna(max)` Codex CLI attempt **started**; controller elapsed 37.502 seconds; Codex exited nonzero with no accepted candidate and no T1 development trial. The CLI stderr was intentionally not retained in sanitized artifacts; the underlying failure category (model ID, permission or provider) is undetermined. Provider-side request count and spend remain **unknown** |

The model was not replaced with a default or alternative; no automatic
retry occurred. The private push-triggered execution workflow was disabled
again. As of 2026-10-25 there was no successful execution attesting that the
local Codex path accepted the Luna model. The previous `default`-model
qualifications remain scientifically separate.

## Local exact-model qualification and fairness repair — 2026-10-27

The current local checkout is the clean branch base
`efccceeb29990da937991b76ff95418f63f0c1da`. No Pilot cell or Full run was
started. The two qualification attempts in this task were:

| Attempt | Invocation | Outcome |
| --- | --- | --- |
| 1 | `--model 'gpt-6-luna(max)'` with `model_reasoning_effort=max` | CLI exited during app-server initialization with `Operation not permitted`; no model response was returned. |
| 2 | `codex --no-daemon exec --model gpt-6-luna -c 'model_reasoning_effort="max"' -C /tmp --skip-git-repo-check --sandbox read-only --ephemeral 'Reply with exactly OK.'` | Success; CLI returned `OK` and recorded model `gpt-6-luna` with reasoning effort `max`. No project or experiment context was sent. |

This confirms the corrected model slug/effort pair works through the local
CLI path. It does not independently attest the upstream provider backend.
Provider request count, token usage, and billing were not available in the
receipt. Qualification attempts are complete; no further check is authorized
by this record.

The whole-Mac memory guard was exercised with a read-only resource sample and
a trivial guarded child process (no model or ML training): 55.86% memory used,
10.59 GiB available, 95% limit, 1.2 GiB required free, and the guarded child
exited successfully. This verifies the normal launch path, not forced
termination under memory pressure.

The Pilot profile now reserves 30 worker seconds independently per cell and
240 seconds total across eight cells. The Full profile still has a 900-second
global cap; its 36 independent 40-second cells require 1,440 seconds, so
initialization now fails closed before creating work or vault directories.
The current `mac_agent_runner.py` model argv now uses the corrected model
slug and explicit max effort. Focused non-model regressions passed: 28 tests.
The test command used an empty pytest config because this environment lacks
the repository's optional `pytest-asyncio` plugin.

## Four-policy Pilot execution — 2026-10-27

The frozen Pilot manifest was prepared from code revision
`f47ec2d919953781e2b3411472d6beec169dd731`. It records eight WDBC cells,
stratified split seed `20272025`, randomized order seed `20271027`, a maximum
of four Codex call attempts and two 15-second training trials per cell, 30
training seconds per cell, and a 240-second total training safety ceiling.
The holdout was not materialized before or during this run.

The formal `pilot` command launched one Codex CLI process for each of the
eight cells. All eight returned in about 0.36 seconds with a failed call
outcome, zero completed turns, zero observed tool events, and no available
usage telemetry. No development trial or submission was produced, no cell
was retried, and final holdout evaluation did not start. The provider-side
request count and cost remain unknown; the sanitized runner did not retain
CLI stderr, so the underlying failure cause is undetermined. The runner's
`status=completed` means it finished recording all eight failed cells; it
does not indicate a successful experimental Pilot.

The manifest, event ledger, and sanitized runner summary remain local in the
ignored results directory `T1_Pilot_Results/` in the Codex task workspace.
`pilot_work/events.jsonl` is the source of truth for the eight attempts;
`summary.json` records zero trials and no holdout evaluation. A later
single-call recovery attempt is recorded below. Full T1 remains paused.

## Single-call recovery and failure logging — 2026-10-27

The bounded single-step runner and recovery manifest were committed at
`95f9670be040ebf4e0c7852e9ae4564c04ce43ee`. Recovery_2 links the original
manifest and ledger plus Recovery_1's manifest, ledger, and summary hashes.
Its frozen code revision was `95f9670be040ebf4e0c7852e9ae4564c04ce43ee`.
It carried forward all nine attempts; the target cell already used two.

The normal `require_escalated` execution request launched exactly one
`gpt-6-luna` / `max` process for `memory_only / WDBC / seed 42`, call 3 of 4.
The CLI exited 1 after 3.927 seconds with `thread.started`, `turn.started`,
`error`, and `turn.failed` events, but no completed turn, candidate, or usage.
The attempt was charged to the cumulative budget: 10/32 total, target cell
3/4, 22 calls globally remaining and one call remaining for that cell. No
training trial ran and the holdout stayed sealed. Provider request count and
cost are unknown.

The actual stdout error message and stderr tail are **not recoverable** from
this run. The append-only event stores event type names and a 600-character
redacted stderr prefix; it has no stdout event payload. The runner removed its
temporary call directory, and the command tool returned only the sanitized
runner summary. The saved stderr prefix ends during app-server initialization,
before any error text. No private sessions, credentials, or raw logs were
searched. This evidence does not identify a schema, disabled-tool, provider,
or authentication error.

The logger is now changed to retain bounded, secret-redacted `error` and
`turn.failed` fields (`type`, `message`, `code`, and `param`) plus the tail of
stderr; raw stdout and stderr remain unpersisted. Synthetic regression tests
place an error after startup noise and verify both event fields and stderr-tail
retention while redacting secrets. Those fixtures validate the logger only;
they do not explain the real failed call. T1 logging and resource-guard tests
pass (27 tests); the working tree was committed locally without a push.

## Research conclusions allowed now

1. A pinned Mac runner can execute the offline reference benchmark with
   real training and preflight holdout scoring.
2. A ChatGPT-authenticated Codex CLI can return a schema-qualified candidate
   and complete one **agent-only** development experiment under the one-call
   protocol.
3. This is a systems/qualification success. There is no independent
   four-strategy comparison, no reproducible statistical efficiency claim,
   no provider cost attestation, and no evidence yet that EpiPilot's routing
   policy improves performance.
4. Preserve the pilot holdout for a genuinely independent, blinded evaluator.
   Formal comparisons require policy-isolated sessions, identical candidate
   spaces and budget ceilings, complete negative trial records, and
   provider-side usage accounting. Do not train further solely to recover a
   missing metric from the historical sanitized receipt.

## Next acceptance gate

Do not automatically launch the full 3-dataset 36-cell benchmark. First
finish audited per-cell cost/metric recording, preserve a sealed test
environment, and construct identical-cost `agent_only`, `memory_only`,
`epipilot_heuristic`, and `epipilot_conditional` sessions, initially
on at most two pilot seeds. Require an explicit model-call and monetary
budget before additional paid work.

## Final local Pilot handoff — 2026-10-27

Recovery 7 brought the frozen eight-cell Pilot to terminal status, but three
cells had no candidate and holdout evaluation remained sealed. The full
cumulative ledger, Conditional stop audit, candidate-hash findings, and
recommendations for a new frozen Pilot are in
[`RESEARCHBENCH_T1_FINAL_HANDOFF.md`](RESEARCHBENCH_T1_FINAL_HANDOFF.md).
The sanitized, portable aggregate receipt is
[`RESEARCHBENCH_T1_PILOT_RECEIPT.json`](RESEARCHBENCH_T1_PILOT_RECEIPT.json);
the raw linked ledgers and provider output were not published.

## Protocol v2 fixed-incumbent comparison — 2026-10-27

The separate protocol-v2 run completed all eight WDBC cells and the one
permitted final holdout evaluation. The run used frozen controller revision
`b045629f898e7fea49346cc64a74b452fbe540a9`, exact `gpt-6-luna` with reasoning
effort `max`, and the same stratified 60/20/20 split as v1 (seed `20272025`).
Estimator seeds 42 and 137 were paired on that one split. The randomized cell
order was frozen with seed `20271028`. The source, train, and development
hashes are preserved in the portable receipt; the holdout file and its hash
are not published.

Each cell trained the shared standardized Logistic Regression baseline
(`C=1.0`, all features, no class weighting) and one adaptive candidate, for
two successful fits per cell. Both fit slots were capped at 15 seconds; each
cell had a 30-second total cap and four model-call opportunities. All 16 fits
completed in 12.909826 worker seconds (less than 1.66 seconds per cell). Ten
model calls completed successfully: Agent Only, Heuristic, and Conditional
used one per cell; Memory Only used two for its isolated source-checked note
and candidate decision. All returned usage telemetry totals 126,564 input and
11,321 output tokens; provider request count and monetary cost were not
metered. There were no retries, failed calls, repeated experiments, or manual
interventions.

| Strategy | Seed | Calls | Input / output tokens | Fits / worker seconds | Adaptive candidate | Adaptive dev AUROC | Frozen submission / test AUROC |
|---|---:|---:|---:|---:|---|---:|---|
| Agent Only | 42 | 1 | 12,570 / 402 | 2 / 1.652951 | Logistic, C=0.1 | 0.983623 | Common baseline / 1.000000 |
| Memory Only | 42 | 2 | 25,056 / 956 | 2 / 1.606002 | Logistic, C=0.1 | 0.983623 | Common baseline / 1.000000 |
| EpiPilot Heuristic | 42 | 1 | 12,913 / 1,905 | 2 / 1.579594 | Logistic, C=0.3 | 0.987226 | Common baseline / 1.000000 |
| EpiPilot Conditional | 42 | 1 | 12,745 / 1,934 | 2 / 1.650083 | Forest, 100 trees, depth 4, leaf 2 | 0.979365 | Common baseline / 1.000000 |
| Agent Only | 137 | 1 | 12,568 / 614 | 2 / 1.606161 | Logistic, C=0.1 | 0.983623 | Common baseline / 1.000000 |
| Memory Only | 137 | 2 | 25,054 / 690 | 2 / 1.581762 | Logistic, C=0.1 | 0.983623 | Common baseline / 1.000000 |
| EpiPilot Heuristic | 137 | 1 | 12,913 / 2,085 | 2 / 1.617737 | Logistic, C=0.3 | 0.987226 | Common baseline / 1.000000 |
| EpiPilot Conditional | 137 | 1 | 12,745 / 2,735 | 2 / 1.615536 | Forest, 40 trees, depth 5, leaf 2 | 0.976744 | Common baseline / 1.000000 |

The baseline had development AUROC `0.987553` in both seeds and holdout
AUROC `1.000000` in both. Every arm selected the baseline after its adaptive
fit scored lower on development data. As a result, all eight frozen
submissions have the same candidate hash and all eight test scores are the
same submitted model configuration; the test scores do not rank the policies.
The adaptive candidates were not scored on the holdout, and no test result was
returned to a research agent.

The strategies took distinct, auditable paths. Agent Only submitted its
ordinary structured candidate proposal; Memory Only used a per-cell
source-checked baseline note before its candidate decision; Heuristic called
EpiPilot `rank_proposals` with a hypothetical two-step successor but trained
only the selected root candidate. Conditional called EpiPilot
`_next_action(policy="conditional")` and ranked absolute development-AUROC
forecasts against the measured baseline. It selected forest candidates after
forecasting positive expected gains of about `0.000734` and `0.001378` AUROC;
those forecasts had zero calibration samples and are heuristic model
predictions, not observed value. Both actual forest dev scores were below the
baseline.

## Interpretation and record limits

This is a completed, bounded WDBC comparison, not evidence that one method is
better at long-horizon research. There is one dataset and two paired estimator
seeds on the same split, not two independent data samples. The heuristic had
the best adaptive development scores, but both remained below the shared
baseline; the submitted policies tied because every one chose the same
baseline, whose holdout AUROC was at ceiling. The holdout result therefore
does not establish a winner.

The prior v1 ledger remains a separate run: 25 call attempts (12 successful,
12 failed, one timeout), eight completed training attempts, and no holdout
evaluation. Across v1 and v2 there are 35 CLI attempts, 24 training attempts,
and 19.322903 worker seconds. Usage is available for 22 calls and missing for
13; combined available usage is 278,431 input, 26,418 output, and 11,904
cached input tokens. Provider-side request counts and monetary cost remain
unknown / unmetered. These combined operational totals do not merge the two
protocols or their scientific outcomes.

**Timing instrumentation note:** the v2 raw runner summary reports zero in
each cell's `training_seconds` field, despite 16 successful trial-finish
events totaling `12.909826` seconds. Per-cell values above are from the
validated trial events and comparison manifest, which reconcile to the
aggregate; the portable receipt preserves that same audited source. The raw
runner summary and event ledgers remain local and are not included in the
publication.

The portable, sanitized v2 receipt is
[`RESEARCHBENCH_T1_PILOT_V2_RECEIPT.json`](RESEARCHBENCH_T1_PILOT_V2_RECEIPT.json).
It contains per-cell configurations, development outcomes, candidate hashes,
usage, final submission scores, and conditional decision values, with no
local machine paths, raw prompts or responses, holdout data, or holdout hash.
Full T1 was not run.
