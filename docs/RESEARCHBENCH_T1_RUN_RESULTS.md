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
