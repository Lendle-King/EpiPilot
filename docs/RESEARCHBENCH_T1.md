# ResearchBench-T1: low-cost, real-data research-policy qualification

**Status: experiment only.** This benchmark measures whether a research *process*
can choose better experiments at fixed cost. It is **not** a result claiming that
EpiPilot outperforms a Coding Agent. The smoke and same-candidate tests are
algorithm/evaluator checks, not four independently run agent sessions.

The default and smoke stages make no model calls. The explicit local Mac
`pilot` mode launches bounded calls through the user's existing Codex CLI;
provider request count and cost remain unobservable to T1. No API key is
added, no local GPU job is used, and no model call occurs without the explicit
`RUN_FOUR_POLICY_PILOT` operation.

## Stages and hard experiment caps

| Stage | Research datasets | Policies | Seeds | Max trials per cell | Trial timeout | Independent cell training budget | Stage safety ceiling | Max Codex decision calls per cell | Final evaluator timeout per cell |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `smoke` (default) | WDBC (built into scikit-learn) | `offline_baseline` | 42 | 2 | 12 s | 24 s | 30 s | 0 | 15 s |
| `pilot` (explicit opt-in) | WDBC | Agent, Memory, EpiPilot heuristic, EpiPilot conditional | 42, 137 | 2 | 15 s | 30 s | 240 s | 4 (32 across eight cells) | 20 s |
| `full` (explicit opt-in) | Adult, Bank Marketing, MAGIC Gamma Telescope | same four policies | 42, 137, 271 | 2 | 20 s | 40 s | 900 s — underfunded | not authorized | 35 s |

To avoid leaking information from the already inspected smoke holdout,
each stage freezes a **different stratified train/dev/final-test split seed**
(smoke = 20271025, pilot = 20272025, full = 20273025). Every policy within
the same stage shares exactly the same split. Formal pilot/full results must
not reuse the smoke split or earlier same-candidate dry-run scores.

Each research cell has an independent trial-count and worker-time allowance.
The stage ceiling is a final safety cap and must be large enough for every
cell to receive its full allowance. Pilot reserves 30 worker seconds for each
of eight cells (240 seconds total), with at most four Codex decision-call
attempts per cell; failed invocations and retries count. The harness does not
meter provider API requests, tokens, or cost. Full remains blocked: 36 cells
at 40 seconds require 1,440 seconds, exceeding the frozen 900-second cap.
Setup rejects an underfunded stage before creating the workspace. Do not
increase the Full cap without a user budget decision. These are upper bounds,
not target allocations. A failed or crashed trial consumes budget, and a
lost execution can be reconciled only by explicitly recording a conservative
full-timeout charge (never by silently relaunching it).

For a multi-arm pilot or full evaluation, record **all** policy/seed outcomes,
including genuine failures. Do not cherry-pick successful episodes.

## Installation / zero-fee live smoke

Run at the root of a clone of the experimental branch:

```bash
python -m pip install -e '.[t1]'  # one-time local scikit-learn, numpy, pandas
python experiments/researchbench_t1/t1.py smoke \
  --work /tmp/t1-smoke-work --vault /tmp/t1-smoke-vault
```

Smoke performs exactly **two real training/validation attempts**:
a frozen Logistic Regression and a Random Forest. It chooses the better
candidate by development AUROC, retrains on train+dev, and invokes the
sealed reference holdout evaluator once. The Wisconsin Diagnostic Breast
Cancer dataset from scikit-learn needs no download. It is deliberately an
easy engineering preflight with a ceiling effect; it is not part of the
pre-registered official three-dataset research-efficiency comparison.

Re-running with the same workspace is intentionally rejected. Use fresh
workspace and vault paths for an independent replication.

## Optional private macOS remote execution

The private [Remote-Control](https://github.com/Lendle-King/Remote-Control)
repository can host a **manual-only**, self-hosted macOS GitHub Actions workflow.
Its three modes are:

- `diagnose`: no ML fit and **zero** model calls; checks that the job actually reached
  a Mac runner and whether its Codex CLI is present/authenticated.
- `offline_smoke`: run this branch's existing frozen WDBC smoke on the Mac;
  **zero** model calls.
- `pilot_one_cell`: only after explicit `RUN_ONE_CELL` confirmation, issue
  **one** Codex `--no-daemon exec --model 'gpt-6-luna' -c
  'model_reasoning_effort="max"'` call for one frozen
  `agent_only / wdbc / seed 42 or 137` cell, require an allowlisted
  candidate, run one real development trial, and submit it.
  This cannot support a four-policy efficiency claim.
- `resource_probe`: no model call; inspect **whole-Mac physical memory only**,
  declining requests that would exceed 95% used memory.

The source is pinned to an exact EpiPilot commit in the remote workflow so
a new public PR commit cannot change what executes on the trusted Mac.
Codex/CC Switch authentication is configured on the Mac itself; no secrets
are copied to GitHub. The exact model slug `gpt-6-luna` and independent
`model_reasoning_effort="max"` setting are required. The old literal
`gpt-6-luna(max)` is not a model slug. `default` and alternate names are
rejected, and an unsupported name is a hard stop, never a reason to fall back.
The underlying provider identity is not independently attested by this CLI
argument. The current Remote-Control request validator still needs review
before that workflow is reused. Whole-Mac physical memory
is the only resource usage limit (95% used); task locking and timeouts remain
for correctness and spending safety. See `RESEARCHBENCH_T1_MAC_RESOURCE_LIMITS.md`.
A call that fails after it starts may still consume quota or money; the
runner never automatically retries or substitutes a different model.

Only the low-cost qualification can be performed on an ordinary same-user
runner account. A formal blind holdout must remain inaccessible to the
Coding Agent through a separate account/host/security boundary. No final
test is opened by the one-cell command.

## Pilot: four controlled policy slots

The explicit Mac controller runs the eight preregistered cells sequentially
in the randomized order stored in `meta.json`. It sends each model a
constrained JSON decision request in a fresh empty working directory, with
read-only sandboxing, shell/web/MCP tools disabled, and automatic request and
stream retries set to zero. Every call must name `gpt-6-luna` and set
`model_reasoning_effort="max"`. The exact argv, call purpose, outcome, turn
count, tool events, and token usage when available are kept in the append-only
cell ledger. Missing usage stays explicitly missing. Provider-side API calls
and billing remain unknown.

Each cell has the same candidate space, at most **two** real training trials,
15 seconds per trial, 30 worker seconds total, and a ceiling of **four model
calls**, including memory-note calls, failures and retries. The independent
per-cell budgets total 240 worker seconds; the global cap is only a safety
ceiling. No cell receives more than its own allocation. The descriptive dev
target is AUROC 0.95, frozen before any calls; it is not a hypothesis test.

The four policies use distinct procedures:

- `agent_only` receives only its current cell's development trial history and
  proposes the next allowlisted candidate.
- `memory_only` receives evidence-linked notes generated from validated runs
  within that cell. Notes must exactly match the source run, candidate digest,
  and measured dev AUROC. Notes never cross seed cells.
- `epipilot_heuristic` proposes two candidate paths and passes them through
  EpiPilot's actual `rank_proposals` two-step lookahead. Scores supplied by the
  model are uncalibrated heuristics.
- `epipilot_conditional` supplies the four canonical outcome probabilities and
  branch utilities to EpiPilot's existing `_next_action(policy="conditional")`
  decision code. Its net information value is explicitly uncalibrated; a
  nonpositive result stops without fallback.

Research prompts include only the dataset name, allowed parameter schema,
candidate choices, development AUROC history, and (for Memory Only) the
cell-scoped validated notes. They do not contain train/dev rows, holdout rows,
holdout scores, vault paths, or private manifests. The source split remains
the original stratified 60/20/20 split. The trusted local controller reads
labels to reproduce the split and freeze its digest, but creates no holdout
file and evaluates no holdout metric until all eight candidates are submitted
and all model calls are closed. It then materializes the holdout for the
reference evaluator exactly once. This is a controlled same-user workflow,
not an OS-level boundary against a malicious process with the user's account.

First freeze and inspect the manifest. This phase makes no model calls:

```bash
python experiments/researchbench_t1/mac_agent_runner.py \
  --mode pilot_prepare --provider ccswitch_codex --model gpt-6-luna \
  --results /path/to/local/T1-Pilot-Results
```

It records the committed code revision, fixed stratified split and hashes,
randomized eight-cell order, and equal per-cell budgets. The `pilot` mode
requires that untouched preparation directory and refuses changed code,
manifest, data, or any prior ledger activity. After reviewing it, start the
integrated controller:

```bash
python experiments/researchbench_t1/mac_agent_runner.py \
  --mode pilot --provider ccswitch_codex --model gpt-6-luna \
  --confirm RUN_FOUR_POLICY_PILOT \
  --results /path/to/local/T1-Pilot-Results
```

The runner writes the execution order, split/source hashes, budgets, call and
trial ledger, submissions, audit counts, holdout manifest, and final metrics
under that results directory. A holdout evaluation runs only if all eight
cells are terminal and submitted; a stopped cell without a candidate leaves
the holdout sealed. Failed requests and training attempts remain in the
ledger and are never silently replaced. The final evaluation is single-use;
no automatic rerun is allowed.

### Full T1: original three real UCI data sets

No automatic downloads are performed. Supply the unmodified locally
obtained UCI originals in a data directory **outside** the public agent
workspace. Check dataset licenses and versions before use.

```bash
python experiments/researchbench_t1/t1.py init \
  --stage full --allow-agent-stage \
  --work /tmp/t1-full-public --vault /tmp/t1-full-secret \
  --source-adult /data/uci/adult.data \
  --source-bank /data/uci/bank-full.csv \
  --source-magic /data/uci/magic04.data
```

Original UCI references (external data must be downloaded/validated separately):
Adult `https://archive.ics.uci.edu/dataset/2/adult`;
Bank Marketing `https://archive.ics.uci.edu/dataset/222/bank+marketing`;
MAGIC Gamma Telescope `https://archive.ics.uci.edu/dataset/159/magic+gamma+telescope`.
Each source byte hash and the train/dev/holdout byte hashes are frozen on
initialization. Bank `duration` is intentionally dropped to avoid a feature
unavailable before the call outcome. Both classes must be present.

## Design caveats and expected scientific claims

The reference split uses a fixed stratified 60% train / 20% dev / 20%
held-out partition, independent of Agent seed, and a deterministic
preprocessing pipeline. Candidate evaluation sees only the public
train/dev files; final retraining uses train+dev and is scored once on
heldout data by a separate operator command.

`/tmp/t1-*-secret` must be protected by OS/container permissions. Merely
placing it outside the public workspace is **not** a cryptographic access
boundary; an unrestricted shell-capable Agent may read it, so genuine
blind evaluation requires a separate account/container/host and independent
operator access. An Agent could also bypass the `trial` endpoint by running
its own training. Enforce a restricted tool/sandbox policy when collecting
claims of fairness or fixed experiment budget.

Even identical candidate+seed giving equal AUROC across policy slots is
only an evaluation consistency test. It says **nothing** about the actual
research strategies. The 2-seed pilot is a qualification gate, not a
statistically powered result. Only after repeated real sessions, audited
provider costs, policy compliance, matched evaluation and correct heldout
handling should comparisons be treated as evidence about EpiPilot.

The default policy does not automatically open `pilot` or `full` and never
starts Codex/Kimi on its own. Keep API spend at **$0 by default**; when
explicitly running research agents, use provider-level caps and record token
and dollar consumption separately, because T1's CPU wall accounting cannot
enforce third-party billing limits.
