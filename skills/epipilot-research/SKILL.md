---
name: epipilot-research
description: Evidence-gated and budget-aware research iteration using an existing coding agent.
---

# EpiPilot Research Skill

You are the executor. The EpiPilot CLI persists research memory, chooses a
budget-feasible next action and enforces frozen experimental protocols. Do not
spawn another coding agent or pretend that the CLI runs experiments autonomously.

## Resume or begin

1. Read the project's AGENTS.md and safety/scientific requirements.
2. Run `epi --root PATH doctor`, `epi --root PATH status` and `epi --root PATH review`.
3. If uninitialized, confirm the goal, observable metric, constraints and total
   budget with the user before `epi init`. Never infer approval from silence.
4. Run `epi onboard`; inspect actual source, historical reports, run configs and
   artifacts. Inventory is NOT evidence. Register major Unknowns and falsifiable,
   scoped Claims. Cite actual results, including negative and contradictory runs.
4a. When the research space is still poorly understood, use the optional
   bounded Scout protocol: run **epi scout prepare** read-only, then reserve
   explicit cost with **epi scout start --operation ID --reserve UNITS** before
   searching. Inspect committed, nonprivate source lines and alternative
   mechanisms. Submit an actual-cost JSON via **epi scout submit --file FILE**,
   using exact blob SHA-256 and line text for each candidate. An explicitly
   authorized reviewer must use **epi scout review --candidate ID
   --decision accepted|rejected --reason TEXT**. Accepted leads become
   *hypotheses*, never validated experiments or facts. Avoid redundant Scout
   epochs and stop when no decision-relevant novelty remains.
   See docs/SCOUT_V2.md for submission schema and limits.
5. Use `epi propose` to add useful discriminative experiments (including controls,
   risk, estimated cost, information gain and likely follow-on value). Estimates
   are heuristics, not calibrated probabilities.
6. Run `epi iterate`. If status is running, start, verify or blocked, handle that
   existing work first instead of spawning a duplicate. Otherwise select a
   proposal and implement necessary source changes before experiment registration.

## Frozen experiment contract

**Commit tracked code and configs before pre-registration.** Git HEAD, local
config/data hashes, the metric JSON path, verifier argv, baseline, controls,
decision rule, falsification criterion and maximum cost are frozen. For example:

```bash
epi preregister --proposal ACT-1 --baseline "baseline run ID" \\
  --config configs/trial.json --dataset splits/v1.json \\
  --control "matched seeds" --control "matched compute" \\
  --decision-rule "delta_success >= 0.02 supports the bounded claim" \\
  --falsification "delta_success < 0.02 rejects this prediction" \\
  --metric-rule delta_success:at_least:0.02 \\
  --artifact runs/trial/metrics.json --max-cost 2 \\
  --verifier python -m json.tool {artifact}
epi start --contract PRE-ID
```

Use a **substantive project-specific validator** rather than relying only on
`json.tool`, which proves JSON validity but not experimental correctness. The
verifier must be argv-based, receive `{artifact}`, and run within the coding
agent's ordinary sandbox/approval boundaries. A verifier may not be changed
after seeing the results. The CLI independently parses and checks metrics
against the frozen thresholds; this still does NOT establish causal validity.

Run the real experiment with the coding agent and capture reproducible artifacts.
The expected JSON shape is `{"metrics":{"delta_success":0.03}}`. Then:

```bash
epi record --contract PRE-ID --summary "matched evaluation" \\
  --outcome improved --cost 1.7 --seed 42 \\
  --metric delta_success=0.03
epi verify --experiment EXP-ID
```

The result's `actual_cost` is recorded even when it exceeds the reservation
or total budget; overrun blocks further spending. `epi abort --contract PRE-ID
--reason "..." --cost 0.5` releases an unfinished reservation while booking
incurred cost. Do not silently change code, data, thresholds or acceptance rules.

## Evidence review and continuation

Only validated measurements from a linked preregistered experiment are eligible
for `epi reflect --claim CLM-ID --verdict supported|refuted|inconclusive
--evidence EV-ID --rationale "..."`. The verdict must match the frozen metric
rule and the recorded outcome. A Claim is always provisional and scope-bound.

Resolve an Unknown only when its related hypotheses have been conclusively
reviewed using the same linked evidence and give an explicit reason via
`epi unknown resolve --id UNK-ID --evidence EV-ID --rationale "..."`.
An inconclusive outcome cannot close the Unknown.

If verification fails, it enters BLOCKED. Diagnose and make an actual
correction; record it with `epi retry --contract PRE-ID --reason "new corrective
strategy"`. Repeating the same reason fails closed. After three failed checks,
register a newly scoped experiment instead of repeating the invalid one.

Re-run `epi review` / `epi iterate` to plan the next experiment. Stop
when costs, user constraints, blocked evidence or remaining uncertainty make
continued investigation unjustified. Summarize verified gains, limitations,
contradictions and open questions; never claim automatic project acceptance.

## Scientific and security safeguards

- Never treat Agent self-report, CLI success, or a passing syntax checker as
  an automatically verified *scientific conclusion*.
- Keep the pre-registered baseline, controls, dataset versions, seeds and
  evaluation thresholds fixed. Do not optimize repeatedly on held-out data.
- Ensure artifacts actually came from the declared run; check for confounders,
  leakage, omitted trials, selective reporting, sample size and multiple testing.
- The research event stream is single-writer and append-only under
  `.research/events.jsonl`. Keep it versioned when safe; inspect for secrets
  and private data before sharing. Do not add raw private traces.
- Legacy schema-v1 events remain readable but their previously marked
  "verified" records do not become measurement-validated v2 evidence.
- No shell interpolation in verifier argv, no unapproved permissions or
  arbitrary external actions. The verifier's scientific validity still
  depends on the actual commands and data.
