# EpiPilot branch and integration status

**Stable source of truth:** [`main`](../README.md). Scientific results must
still pass the v2 research contract and independent measurement gates. This
document records exactly which former development features were integrated,
not a blanket endorsement of autonomous research quality.

## Completed integrations (2026-10-23)

| PR | New main functionality | Boundary |
| --- | --- | --- |
| [#15](https://github.com/Lendle-King/EpiPilot/pull/15) | Local-first Research Workspace from former PR #10 | Workspace notes are staging, not canonical evidence |
| [#13](https://github.com/Lendle-King/EpiPilot/pull/13) | Project-level Codex/Kimi research skill installer | No Codex marketplace plugin or background executor |
| [#14](https://github.com/Lendle-King/EpiPilot/pull/14) | Scout-style four-outcome conditional value-of-information and frozen/calibrated forecasts | Advisory; default `epi iterate` heuristic unchanged; not full Scout agent |
| [#16](https://github.com/Lendle-King/EpiPilot/pull/16) | Source-anchored semantic onboarding, manual import, explicit opt-in remote extraction | Human-reviewed candidate notes only; scientific fact promotion forbidden |

The `main` stream remains `.research/events.jsonl` schema-v2, with read-only
replay of schema-v1 histories. The UI stages its separate local SQLite
workspace and cannot write or assert verified v2 research evidence.

## Historical PRs and branches

- **Closed, code retained:** PR #5 Hermes frontend, PR #9 supervised browser
  handoff, PR #10 prior standalone workspace. Closing a PR did not remove
  its Git branch or historical work.
- **Open, unique work not fully integrated:** PR #4 checkpoint/recovery;
  PR #6 older Codex-native MCP/marketplace integration; PR #7 replaceable
  executors; PR #8 evidence workbench; PR #11 large stacked semantic/
  experimental-engine feature set.
- **Preserved working branch:** `epipilot/research-scout-v1` extends the
  old PR #11 code by additional Scout and forecast-calibration commits.
  Only the bounded conditional decision-value part was ported. This
  branch cannot directly replace `main` without bypassing newer
  pre-registration, budget and linked-Evidence rules.
- **Historical backup:** `legacy/main-before-research-plugin`.

Three optional development lines:
`dev/research-intelligence`, `dev/agent-integrations`,
`dev/research-workspace`. Their merged commits are in main; keep those
refs only when genuinely developing new work. Do not delete an open PR's
base/head or a branch with unique, unreviewed commits.

## Deferred work / acceptance gates

1. **Full Scout compatibility:** review the remaining source-discovery,
   canonical semantic graph, experiment runner and outcome-management
   code in `epipilot/research-scout-v1`; migrate by typed adapter and
   evidence-gated tests rather than overwriting the CLI.
2. **Codex native packaging:** if a real MCP marketplace installation is
   required, port PR #6's bounded tool surface to v2 contracts; never
   claim that project-local `epipilot-install-agent` does this.
3. **Crash-safe runtime resume:** review PR #4 and re-use its event-driven
   recovery only after reconciling the current research operation FSM.
4. **UI consolidation:** selectively harvest remaining static evidence
   diagnostics from PR #8 while avoiding a second maintained frontend.
5. **Research benchmark:** compare agent-only, memory-only, greedy,
   current deterministic ranking and conditional decision-value at
   equal verified experiment budget on held-out task outcomes.
6. **Operational safety:** require real Codex/Kimi end-to-end sessions,
   actual model-provider redaction/consent tests, and interruption recovery
   before claiming unattended operation or scientifically verified gains.

These are outstanding implementation and validation tasks, not completed
features. Preserve old branches until individually migrated, rejected with
documented reason, or archived with an explicit recovery reference.
