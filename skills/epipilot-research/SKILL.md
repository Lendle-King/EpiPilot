---
name: epipilot-research
description: Evidence-grounded, cost-aware autonomous research iteration using the existing coding agent.
---

# EpiPilot Research Skill

You are the EXECUTOR. The EpiPilot CLI supplies durable research knowledge,
selection policy, and verification boundaries. Do not spawn another coding agent.

## Activation

Use for scientific/algorithmic work involving unknown mechanisms, expensive
experiments, iterative optimization, or resuming a previously studied repository.

## Operating loop

1. Read AGENTS.md and project scientific constraints. Locate project root.
2. Run epi --root PATH status, then epi --root PATH onboard if not yet indexed.
3. For fresh projects ask the user to confirm goal, metrics, and experiment budget.
   Run epi init ONLY for a user-approved goal, not an inferred preference.
4. Read primary code, historical experiment reports, raw metrics, and baselines.
   Register unknowns using epi unknown add and scoped defeasible claims using epi claim.
   An imported report is an observation until its provenance is checked.
5. Build discriminative proposals with epi propose. Estimates [0,1] are heuristics;
   make rationales, expected observations, controls and acceptance checks explicit.
6. Run epi iterate; implement the returned task yourself. Prefer cheap diagnostics,
   static analysis and matched low-fidelity trials before expensive training/backtests.
7. Run experiments, record actual costs, data version/split, code SHA, seeds,
   metrics and real artifact paths using epi record. Do not invent results.
8. Run an independent acceptance command using epi verify --experiment EXP --check ... .
   This only validates the check, not the scientific conclusion. Investigate if it fails.
9. If evidence is relevant and quality sufficient, run epi reflect --claim ... or
   epi unknown resolve ... . Keep counterexamples, negative results and scope.
10. Re-run epi review / epi iterate, without repeating unchanged failures.

## Scientific safeguards

- Never promote a coding agent's own report to a verified Fact.
- Treat even supported research claims as provisional, scope-conditional knowledge.
- Preserve historical events rather than overwriting contradicted claims.
- Never use held-out tests repeatedly for model selection; account for multiple testing.
- Keep experiment commands as argv, not shell interpolated strings.
- Do not commit API tokens, PII, raw private data, or proprietary traces.
- Stop on budget exhaustion or blocked evidence; report the unresolved frontier.
- Protect an independent verification budget and halt on repeated identical failures.
