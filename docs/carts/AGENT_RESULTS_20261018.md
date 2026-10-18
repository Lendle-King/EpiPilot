# Agent-native integration validation — 2026-10-18

Implementation: `8875b57` (adapted workflow from plugin branch `78c6266` over existing
canonical CARTS; no competing JSONL truth store). Local integration is on
`feat/semantic-research-onboarding`. No remote write or trading action was performed.

## Actual agent trajectory

The coding agent inspected existing committed ETF reports, constructed exact Git anchors
and competing explanations, preregistered a complete hashed-input contract, invoked the
public agent CLI, inspected measured output, and submitted the next unresolved frontier.
This was a real host-agent workflow, not a scripted assertion of profitable alpha.

Research repository source revision: `91bfa7ecd9339ae3ca51226b57559c3cfb0237b1`.
Anchor: `docs/auto_iteration/carts_qualification_20261018.md:36` (exact UTF-8 line).
Question: are at least 99% of scale gaps in the pinned 28-ETF panel before 2019-01-01?
Alternative: missingness is materially distributed in recent acquisition windows.

Contract: proposal `agent-scale-boundary-20261018`, `missingness` adapter, EXPLORE purpose,
28 SHA-256-pinned CSVs, >=37,712 observations, cutoff 2026-10-16,
`before_share_of_missing >= .99`, definitive negative preregistered, estimated 2 wall_seconds.

| Measurement | Result |
|---|---:|
| Panel rows | 37,712 |
| Missing scale observations | 731 |
| Missing before 2019-01-01 | 729 |
| Missing on/after boundary | 2 |
| Before-boundary share | 99.726402% |
| Files with missing scale | 4 |
| Measured execution | 0.059068 s |
| Independent adapter recomputation | 0.060465 s |
| Settled total for this experiment | 0.119533 s |

The two later gaps are both 2025-05-19: 159509.SZ and 159941.SZ. Before-boundary gaps
are 243 each for 159915.SZ, 159941.SZ and 159949.SZ. A separately written CSV counting
script, without MeasurementAdapter imports, reproduced all totals and dates.

**New scoped claim:** at least 99% of scale-missing rows in this pinned retrospective
current-universe panel precede 2019. Its canonical hypothesis is SUPPORTED; its targeted
unknown is resolved. Evidence ID: `7a080b4f-aba3-53c5-9de8-1249449a1b12`, quality
MEASUREMENT, scope integrity, promotion-qualified false. This describes the measured
panel distribution; it does not establish why the provider has gaps or validate a strategy.
The new report claim is anchored at research Git `71f0d10`,
`docs/auto_iteration/agent_frontier_20261018.md:15`; the extracted quote was checked
byte-for-byte against that committed Git blob. This source quote proves the report wording;
the separate measurement evidence supplies its empirical justification.

After measurement, the bounded packet required new discovery (version 117), temporarily
showing optimization because its one registered question had been answered. Reflection
then registered an important publication-time/historical-universe qualification unknown.
The latest phase became mixed, action blocked (version 122). Thus new uncertainty prevents
remaining in optimization merely because an earlier diagnostic passed.

Current blocker: original release vintages and historical constituent availability remain
unproven. No independent forward CAGR >=15% / MDD <=20% result was established.
No inferred provider explanation was promoted from the distribution alone. The two
coincident recent gaps are an actionable next acquisition investigation, not permission
to fabricate missing observations or source timestamps.

## Canonical and frozen-state checks

- Existing 95 project events remain an identical prefix; 27 new canonical events appended.
- Replay equals live state, including measurements, review, settlement and frontier.
- Existing 498 workspace events and 152 route runs retained identical row hashes.
- Forward log SHA-256 unchanged during the pilot; no backfill or edit.
- V2.5 candidate SHA-256 unchanged:
  `9bd9312d5e7c712119b70c82fdda1117453b88b27bf1f88c72b132c16b2cbf67`.
- Canonical CARTS now has five settled checks total; 0.304790 wall_seconds spent of 120.
  This excludes host-agent reasoning, API use, development and independent audit script cost.

Detailed requests, source quotes, input hashes, packets, independent counts, invariants,
actual artifacts and a reproducible local pilot script are in the orchestration workspace:
`agent_integration/20261018/`. They are not copied into public Git as raw traces.
Do not rerun that script against the same canonical project as a new experiment; discovery
IDs and unchanged measurement guards intentionally prevent duplicate execution.

## Automated verification and review

Full gate: ruff format --check, ruff check, mypy src, pytest — **269 passed**.
One existing Starlette/httpx deprecation warning remains; no frontend code changed.
Independent reviewer reran **72 focused tests** and found no remaining material issue for
shipped adapters. A final CLI/API artifact-directory regression and independent review
also passed after integration. Fixed issues include low-quality scientific promotion, self-labelled
independent scope, historical-evidence reuse, refuted-question rechecks, incomplete restart
packets, mutex graph batches, partial verification commits, settlement/reconciliation
recovery and alias-based sample/retry inflation.

Regression fixtures exercise changed-input counterevidence, failed scientific execution,
quality/scope/sample gates, exploration protection and unaffordable lookahead. They are
scripted semantic fixtures, not evidence that a model spontaneously discovers every unknown.
Operating-skill pressure review recovered usable commands and an honest budget tradeoff;
its baseline initially inspected the wrong checkout, so it is not a controlled skill efficacy
experiment. Earlier CARTS benchmark results are not relabelled as benchmarks of this layer.

## Practical boundary

The host coding agent runs semantic discovery, code/data repair and the loop. The protocol
owns canonical memory, budgets, selection, independent measurement and resumability.
A new domain needs a reviewed Computer or runner/verifier implementation. Built-in
adapters are offline CSV diagnostics; they cannot qualify forward alpha. An unattended
scheduler and total token/API cost accounting require explicit host integration. There
is no demonstrated universal efficiency or goal-attainment guarantee.
