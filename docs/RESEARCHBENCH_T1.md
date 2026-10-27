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
| `pilot_v2` (fixed-incumbent comparison) | WDBC | same four policies | 42, 137 | 2 total: 1 shared baseline + up to 1 adaptive fit | 15 s | 30 s | 240 s | 4 (32 across eight cells) | 20 s |
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
and billing remain unknown. On CLI failures, the ledger also keeps bounded,
redacted `error` / `turn.failed` fields and the tail of stderr; raw stdout and
stderr are not retained.

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

### Protocol v2: fixed-incumbent WDBC comparison

`pilot_v2` is a new, separate eight-cell run. It does not alter the old Pilot
or its failed-call ledger. It keeps the Pilot's stratified 60/20/20 WDBC split
seed (`20272025`) so the two runs use the same data membership. Seeds 42 and
137 are estimator seeds on that one split; they are paired repeats, not two
independent data samples. Cell order is randomized from seed `20271028` and
is frozen in the new manifest.

Every cell first receives one measured, shared incumbent fit: standardized
logistic regression, `C=1.0`, `feature_mode=all`, and no class weighting. That
fit consumes one of the cell's two training slots and is visible to each
policy through its permitted evidence path. Each policy may then request one
adaptive candidate fit, also capped at 15 seconds. Total development spend is
30 seconds per cell and 240 seconds across eight cells; the 240-second stage
limit remains a safety cap. Each cell has the same maximum of four model-call
attempts, including failures. The controller does not issue filler calls:
Agent Only, Heuristic, and Conditional need one decision call; Memory Only
can use one extra call to write its source-checked baseline note before its
candidate decision. Failed model calls are charged, with no automatic retry.
Token usage is recorded when returned, but is not forced to match.

The v2 candidate space removes WDBC's equivalent `numeric_only` feature mode;
logistic `C` is normalized to a float before candidate hashing so `1` and
`1.0` cannot appear as distinct configurations. All four arms use the same
allowlisted logistic and forest parameters, split, baseline candidate,
estimator seed, model, reasoning effort, and per-cell call and fit ceilings.
A failed or invalid adaptive decision leaves the measured baseline as a
valid submission. A real infrastructure-wide failure pauses later cells; it
does not erase the baseline or the failed-call record.
Heuristic's two-step lookahead can score a hypothetical successor, but the
controller executes only its selected root candidate because v2 reserves one
adaptive trial per cell.

Conditional v2 receives exactly two candidate forecasts, each with the four
canonical outcome probabilities and absolute candidate development-AUROC
forecasts. The controller compares expected post-outcome AUROC against the
measured baseline through EpiPilot's `_next_action(policy="conditional")`
and `rank_decision` path. The forecast is uncalibrated. The infrastructure
failure branch retains the baseline; trial and call limits are hard
constraints, so seconds are not subtracted from AUROC. A nonpositive expected
AUROC gain stops and submits the baseline. The EpiPilot `Proposal` model
requires positive estimated action cost, so promotion of an already-fitted
candidate carries a logged `1e-9`-second bookkeeping epsilon; it adds no
training slot and has no AUROC penalty.

Prepare and inspect the new protocol before making any model call:

```bash
python experiments/researchbench_t1/mac_agent_runner.py \
  --mode pilot_v2_prepare --provider ccswitch_codex --model gpt-6-luna \
  --results /path/to/local/T1-Pilot-V2-Results

python experiments/researchbench_t1/mac_agent_runner.py \
  --mode pilot_v2 --provider ccswitch_codex --model gpt-6-luna \
  --confirm RUN_T1_PILOT_V2 \
  --results /path/to/local/T1-Pilot-V2-Results
```

The first command freezes the code revision, baseline, data and split hashes,
randomized order, and equal training and call ceilings to a new local
manifest. It does not call the model or materialize test data. The second
command uses exact `gpt-6-luna`, `max` reasoning, read-only ephemeral context,
disabled tools, and zero configured retries. After all eight cells have
submitted frozen candidate hashes and all model processes have ended, the
evaluator opens the test split once. It scores the eight submitted candidates
plus the common baseline once per estimator seed (ten evaluation records).
Test scores are never sent back to a research agent or used to change a
candidate. This is one bounded WDBC comparison, not a long-horizon research
conclusion or a statistically powered test. Full T1 remains unauthorized.

### Bounded single-cell recovery

If the full Pilot stops on a local infrastructure failure, a recovery may be
prepared against both the immutable original results and the latest recovery
ledger. The `pilot_step_prepare` / `pilot_step` path is limited to
`memory_only / WDBC / seed 42` for the first repaired-call checkpoint. Each
`pilot_step` process permits at most one model decision call and at most one
15-second development trial. It records the attempted call before launch,
preserves cumulative call IDs and per-cell counts, and checkpoints a validated
candidate without opening holdout or continuing to other cells. A later step
must be started explicitly; standard `pilot` mode refuses a single-step
recovery manifest.

For local execution, launch the runner through the host's normal approved
elevated execution path so the CLI can access its existing `~/.codex` runtime
state. The runner does not change `HOME`, credentials, provider, or global
security settings. The inner Codex process remains read-only with shell, web,
and MCP tools disabled. For example:

```bash
python experiments/researchbench_t1/mac_agent_runner.py \
  --mode pilot_step_prepare --provider ccswitch_codex --model gpt-6-luna \
  --origin-results /path/to/T1_Pilot_Results \
  --prior-results /path/to/T1_Pilot_Recovery_1 \
  --results /path/to/T1_Pilot_Recovery_2 \
  --cell-arm memory_only --cell-dataset wdbc --cell-seed 42

python experiments/researchbench_t1/mac_agent_runner.py \
  --mode pilot_step --provider ccswitch_codex --model gpt-6-luna \
  --confirm RUN_ONE_PILOT_STEP \
  --results /path/to/T1_Pilot_Recovery_2 \
  --cell-arm memory_only --cell-dataset wdbc --cell-seed 42
```

### Cumulative serial recovery

When earlier recovery ledgers contain successful development trials as well as
failed calls, `pilot_resume_prepare` validates the full hash-linked chain,
imports successful candidate/metric artifacts into a fresh work ledger, and
charges their original trial count and seconds. Imported trials are evidence
only; they are never trained again. `pilot_resume` then walks the remaining
frozen cells in sequence, deriving each stable call number and training slot
from the ancestor ledgers plus the new ledger. Failed calls stay charged, the
four-call and two-trial cell caps remain cumulative, and an infrastructure
failure stops the serial pass. Holdout evaluation runs only if all eight cells
have submitted candidates after the pass; a cell with no candidate keeps the
holdout sealed and the result is marked incomplete.

For a cumulative recovery whose next eligible cell is Agent Only / WDBC / 42:

```bash
python experiments/researchbench_t1/mac_agent_runner.py \
  --mode pilot_resume_prepare --provider ccswitch_codex --model gpt-6-luna \
  --origin-results /path/to/T1_Pilot_Results \
  --prior-results /path/to/T1_Pilot_Recovery_5 \
  --results /path/to/T1_Pilot_Recovery_6 \
  --cell-arm agent_only --cell-dataset wdbc --cell-seed 42

python experiments/researchbench_t1/mac_agent_runner.py \
  --mode pilot_resume --provider ccswitch_codex --model gpt-6-luna \
  --confirm RUN_FOUR_POLICY_PILOT \
  --results /path/to/T1_Pilot_Recovery_6
```

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
