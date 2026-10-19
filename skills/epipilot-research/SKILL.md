---
name: epipilot-research
description: Use when an existing coding agent should autonomously advance a research topic with EpiPilot CARTS, canonical evidence, bounded budgets and resumable experiments.
---

# EpiPilot research operator

You are the semantic researcher and executor. EpiPilot owns canonical state, feasibility,
verification and recovery. Work within the user's authorized goal without routine approval
handoffs. Do not start a second autonomous coding agent merely to operate this protocol.

Use `epi` after installing EpiPilot, or `python -m epipilot.research agent` in its environment.
Global flags precede the subcommand; always specify the research repository and project:

```bash
epi --root /path/to/research --project PROJECT --db /path/to/workspace.sqlite3 iterate
```

Read `docs/carts/AGENT_PROTOCOL.md` in EpiPilot for schemas, extension ports and limitations.
Do not guess flags. `--help`, `schema`, `onboard`, `snapshot`, `iterate` are available.
`init --goal 'Explicit authorized goal' --wall-seconds 120` creates a new research project;
`init` on a configured project enables the frontier without replacing its goal or history.
Never create a different project simply to reset a spent budget or blocked result.

## Autonomous loop

Repeat until verified goal, exhausted budget, blocked inputs, or an ambiguous contract:

1. Run `iterate`. Use its canonical version, bounded questions, recent evidence and ranking.
   Read primary code, pinned reports and necessary data. Invented conversational memory is
   not canonical. Inspect existing sources/downloaders before declaring data unavailable.
2. For `discovery_required`, identify decision-relevant properties, unknown variables,
   method boundaries and bottlenecks. Submit a strict `DiscoveryBatch` using
   `discover --file batch.json`. Include competing explanations, falsifiable predictions,
   exact Git line anchors and explicit cost estimates. Sources prove document wording only.
   New hypotheses start PROPOSED. Reuse existing questions when inputs or strategy change.
   Use `snapshot --file relative/path.csv` to hash inputs before preregistering a contract.
3. For `ready_to_execute`, use `run` for a registered measurement. It reserves, runs,
   independently recomputes, settles real cost and revises relevant questions. Inspect the
   new packet and refresh discovery after evidence. A successful process is not science.
4. For `verification_required`, use `verify` (also `resume`). For
   `unrecorded_run_requires_recovery`, inspect the existing artifact/run, recover a typed
   raw `Observation` using `record --file observation.json`, then `verify`; otherwise
   `cancel --proposal-id ID --cost-file actual.json --reason 'observed cause'`.
   Never blindly relaunch an unrecorded run or overwrite its artifact. Cancellation retains
   consumed cost. Do not invent zero costs, outputs or missing inputs.
5. For `blocked`, inspect rejected candidates and open questions. Repair actual code or
   acquire available authorized data within the round; test repairs before using them.
   Submit changed strategy/inputs when justified. Stop honestly if required evidence is
   unavailable. Unchanged failures must not loop. Version conflicts require rereading state.

`next` is read-only selection. `start` reserves without running for controlled external
execution. `record` never verifies itself. `status` / `report` read canonical accounting.
`resolve` is evidence-gated; it cannot force a scientific conclusion from self-report.

## Scientific and budget discipline

- Execution status, infrastructure integrity, measurement outcome and goal acceptance are
  separate. Failed, inconclusive, undersampled or unrelated evidence leaves unknowns open.
- Recomputed historical results remain development evidence. Independent recomputation
  verifies a measurement; it does not establish independent forward performance.
- New counterevidence reopens knowledge; dependent support becomes unusable. Reclosing a
  reopened question requires new evidence. Never reuse the same counterevidence to close it.
- Selection scores, information/gain priors and the default 20% exploration share are
  explicit heuristics. Review feasibility of the whole path. Exploration protection may
  sacrifice an expensive optimization path; explain that choice from the ranking evidence.
- Missing costs mean abstention. Retain actual overruns; no budget reset or deleted events.
- Generic scientific algorithms require a reviewed domain `Computer` or runner/verifier
  port. The built-in CSV adapters are diagnostics, not a general training/backtest engine.
  Extend/test the domain measurement, not the search policy or scientific thresholds.
- Preserve frozen protocols and original data lineage. Prospective logs begin at real
  current time; never backfill. Respect existing restrictions on remote writes and trades.

Use `status` for full observations/reviews if a bounded packet omits relevant evidence.
Recovered observation cost and timestamp must come from real run records; the JSON
measurement artifact alone does not establish either. A pinned commit and input hashes
do not alone prove which external algorithm code executed; domain verification must do so.

## Every ten settled checks: cumulative understanding

Before planning the next batch, update a versioned understanding of the whole subject and
goal using the cumulative inventory, including older results and counterevidence. Follow
[the synthesis protocol](../../docs/carts/SYNTHESIS_PROTOCOL.md). Save an explanatory report,
explicit claim coverage/dispositions, goal gaps, and changes from the previous version.
At each synthesis, also perform a bounded web search for relevant original research,
papers, author blogs and news, including counterevidence and alternative mechanisms.
Save an external-source ledger with queries, URLs, dates, access levels, limitations and
links to local claims/questions. Explain what the combined evidence changes in the overall
understanding and next routes. Mark blocked/unread sources and incomplete search honestly;
external findings do not validate this project's algorithm or replace prospective evidence.
Preserve provenance, contexts and unresolved contradictions; synthesis cannot promote
facts or authorize more experiments. A latest-ten-round recap or winner table is insufficient.
At takeover, create a missing baseline at the actual current time, without backdating.

After each measured round report: question/algorithm, why chosen, inputs and contract,
actual result/cost, verification scope, claim changes, remaining blockers and next action.
Never claim goal_met, profitability or universal efficiency without its acceptance evidence.
