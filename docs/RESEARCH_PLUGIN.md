# Agent-native Research Plugin (MVP)

Branch: `main` (original development branch: `feat/research-plugin-cost-aware-tree-search`)

## Scope

This is a LIGHTWEIGHT project-local research decision and memory layer. Codex,
Kimi Code, or another coding agent performs semantic analysis, edits code, runs
experiments and chooses appropriate independent verification commands.

The plugin currently implements:
- strict research goal/claim/unknown/proposal/experiment/evidence contracts;
- append-only, replay-validated repository-local event memory;
- cost-aware scoring with uncertainty-dependent exploration/optimization weights,
  dependency gates, budget gates and one-step lookahead;
- repository inventory onboarding (NOT automated historical science synthesis);
- an agent-facing plan and iteration interface; and
- explicit independent argv checks required before reviewing claims.

It is NOT a separately-running scientist, a trainable MCTS policy, a complete
research-space model, or an autonomous experiment executor. The semantic
identification of unknowns and hypotheses remains with the coding agent.

## Install

From EpiPilot source: python -m pip install -e .

Run inside your research repository or pass --root path/to/research/repo.
State is stored in .research/events.jsonl, intentionally visible to Git. Inspect
for sensitive data before committing, and do not store private raw traces.
Single-writer execution only; no shared remote queue or parallel write sessions.

Use python -m epipilot.research in place of epi if console scripts are unavailable.
Copy skills/epipilot-research/SKILL.md into the target coding agent's supported
skills directory, or copy its operating loop into that project's AGENTS.md.
The latter approach is portable across agents with repository instructions.

## Example

    epi init --goal "Study deployable ETF strategy improvements" --budget 30 \
      --metric "out-of-sample return and drawdown" \
      --constraint "no future information in historical feature construction"

    epi onboard
    epi unknown add --id UNK-1 --question "Does global ranking suppress diversification?" \
      --importance 0.9 --sensitivity 0.9
    epi claim --id CLM-1 --statement "Cross-category ranking may cause slot competition" \
      --scope "ETF universe, frozen research protocol"
    epi propose --id ACT-1 --title "Matched ranking ablation" --kind explore \
      --rationale "Discriminate competition from position sizing" \
      --acceptance "Reproducible run with frozen data and matched costs" \
      --unknown UNK-1 --claim CLM-1 --ig 0.9 --gain 0.2 \
      --unlock 0.7 --novelty 0.6 --cost 1 --risk 0.2

    epi next
    epi iterate

The CODING AGENT executes the returned plan with the real project harness,
then records actual measurements:

    epi record --proposal ACT-1 --summary "Measured ranking comparison" \
      --outcome inconclusive --cost 0.5 \
      --config configs/rank-ablation.yaml --dataset "ETF data snapshot and split V1" \
      --seed 42 --metric "delta_return=0.001" --artifact runs/rank-ablation.json

The command returns an EXP id and EV id, each of which is immutable and
automatically linked. For example (substitute your returned ID):

    epi verify --experiment EXP-ID --check python -m pytest tests/test_rank.py
    epi reflect --claim CLM-1 --verdict inconclusive --evidence EV-ID

Note: a passing pytest command only independently checks what that test
actually asserts. It does NOT independently establish a causal finding or
generalizable Alpha. Review validity, confounders, leakage, random seeds,
sample size and multiple testing before updating a Claim.

    epi review
    epi iterate

## Limits and next steps

The planner currently performs deterministic 2-step heuristic ranking, not
probabilistic tree search. The information-gain and improvement estimates are
agent-supplied ordinal estimates with no empirical calibration. True cost-aware
search should add experiment outcome likelihoods, learned cost distributions,
conditional value of information, and deeper budgeted lookahead AFTER real
research trajectories have been collected.

Repository inventory is bounded and never treats document titles as verified
facts. Agent-led onboarding must interpret historical experiment reports and
identify evidence; that semantic extraction is not yet automated in the CLI.

When experiments are expensive, evaluate effectiveness at equal compute budget
against random exploration, greedy optimization, and static task priority.
Reserve independent held-out evaluation and never select models on held-out
data. Never silently migrate or repair an invalid event stream.
