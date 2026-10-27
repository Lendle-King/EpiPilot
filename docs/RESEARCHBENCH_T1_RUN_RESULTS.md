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
