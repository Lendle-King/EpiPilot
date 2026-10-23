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
| [#17](https://github.com/Lendle-King/EpiPilot/pull/17) | Budget-reserved, source-grounded Scout Unknown/Proposal discovery and explicit operator review | No autonomous researcher; citation authenticity is not scientific evidence |

The `main` stream remains `.research/events.jsonl` schema-v2, with read-only
replay of schema-v1 histories. The UI stages its separate local SQLite
workspace and cannot write or assert verified v2 research evidence.

## Historical PRs and branches

- **Closed or superseded:** PR #5 Hermes frontend, #9 supervised browser handoff,
  #10 standalone workspace, #8 alternate project workbench, and #11 obsolete
  stacked semantic/experimental engine. Their unique historical work is
  retained through GitHub PR history, explicitly named commits, and archival
  Git tags where relevant. Closing them is **not** a claim of full feature parity.
- **Open, unique work not yet integrated:** PR #4 checkpoint/recovery;
  PR #6 native Codex MCP/marketplace installation; PR #7 replaceable executors.
  The project-local Codex/Kimi skill installer does not replace native MCP.
- **Preserved working branch:** `epipilot/research-scout-v1` extends the
  old PR #11 code by additional Scout and forecast-calibration commits.
  Only the bounded conditional decision-value part was ported. This
  branch cannot directly replace `main` without bypassing newer
  pre-registration, budget and linked-Evidence rules.
- **Historical backup:** `legacy/main-before-research-plugin`.

Integrated development branches `dev/research-intelligence`,
`dev/agent-integrations`, and `dev/research-workspace` have no outstanding
unique commits and need not remain as active remote branch refs. Future work
should start from current `main`. Retain `epipilot/research-scout-v1` while
its remaining nonportable research modules are audited. Never delete the
head/base of an open PR or an unarchived unique working tree.

Remaining items are tracked in [integration follow-up #18](https://github.com/Lendle-King/EpiPilot/issues/18).

## Deferred work / acceptance gates

1. **Full Scout compatibility:** bounded source-grounded Scout intake is available,
   but public literature retrieval, automatic semantic knowledge graphs,
   domain-specific experiment runners and complete old outcome management
   remain separate. Continue selective typed, evidence-gated migration; do not
   overwrite the canonical v2 CLI.
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
