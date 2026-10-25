# ResearchBench-T1: low-cost, real-data research-policy qualification

**Status: experiment only.** This benchmark measures whether a research *process*
can choose better experiments at fixed cost. It is **not** a result claiming that
EpiPilot outperforms a Coding Agent. The smoke and same-candidate tests are
algorithm/evaluator checks, not four independently run agent sessions.

The benchmark adds **no network client, API key, nested Coding Agent, GPU job,
or automatic LLM calls**. All outputs and data are local. Agent sessions, if
eventually enabled, run using the user's existing Codex/Kimi setup and are
not launched or billed by this harness. Costs from third-party agents cannot
be metered by T1 and need explicit provider-side limits and independent audit.

## Stages and hard experiment caps

| Stage | Research datasets | Policies | Seeds | Max trials per cell | Trial timeout | Shared development wall budget | Final evaluator timeout per cell | AI API calls by harness |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `smoke` (default) | WDBC (built into scikit-learn) | `offline_baseline` | 42 | 2 | 12 s | 30 s | 15 s | 0 |
| `pilot` (explicit opt-in) | WDBC | Agent, Memory, EpiPilot heuristic, EpiPilot conditional | 42, 137 | 2 | 15 s | 120 s | 20 s | 0 |
| `full` (explicit opt-in) | Adult, Bank Marketing, MAGIC Gamma Telescope | same four policies | 42, 137, 271 | 2 | 20 s | 900 s | 35 s | 0 |

To avoid leaking information from the already inspected smoke holdout,
each stage freezes a **different stratified train/dev/final-test split seed**
(smoke = 20271025, pilot = 20272025, full = 20273025). Every policy within
the same stage shares exactly the same split. Formal pilot/full results must
not reuse the smoke split or earlier same-candidate dry-run scores.

The staged ceilings limit this research *evaluator*, not the tokens spent
in manually launched Codex/Kimi sessions. In particular, `full` is **not**
recommended until `pilot` is complete and reviewed. These are upper bounds,
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
  **one** Codex `exec` call for the `agent_only / wdbc / seed 42` cell, require
  an allowlisted JSON candidate, run one real development trial, and submit
  the best candidate. This cannot support a four-policy efficiency claim.

The source is pinned to an exact EpiPilot commit in the remote workflow so
a new public PR commit cannot change what executes on the trusted Mac.
Codex/CC Switch authentication is configured on the Mac itself; no secrets
are copied to GitHub. `gpt-6-luna(max)` is an unverified local model selector
and should never silently fall back to another model. A call that fails after
it starts may still consume quota or money; the runner records an attempted
call and never retries it automatically. No third-party billing cap can be
enforced by this Python benchmark; use provider-side limits.

Only the low-cost qualification can be performed on an ordinary same-user
runner account. A formal blind holdout must remain inaccessible to the
Coding Agent through a separate account/host/security boundary. No final
test is opened by the one-cell command.

## Pilot: four controlled policy slots, no automatic paid model calls

First initialize the matched pilot (manual approval is required for the
stage; initialization alone does not invoke any Agent):

```bash
python experiments/researchbench_t1/t1.py init \
  --stage pilot --allow-agent-stage \
  --work /tmp/t1-pilot-public --vault /tmp/t1-pilot-secret

python experiments/researchbench_t1/t1.py task \
  --work /tmp/t1-pilot-public --arm epipilot_heuristic \
  --dataset wdbc --seed 42
```

`task` prints instructions that can be supplied to a **separately launched**
Codex or Kimi session. For each cell, the underlying Agent proposes a
candidate JSON from the allowlisted model/preprocessing space:

```json
{"model":"logistic","feature_mode":"all","C":1.0,"class_weight":"none"}
```

or:

```json
{"model":"forest","feature_mode":"all","n_estimators":64,"max_depth":7,"min_samples_leaf":2,"class_weight":"balanced"}
```

The Agent cannot expand the search to arbitrary code or access the sealed
holdout through the `trial` API. Numeric-only features, class balancing,
model capacity and regularization offer bounded competing mechanisms.

```bash
python experiments/researchbench_t1/t1.py trial \
  --work /tmp/t1-pilot-public --arm epipilot_heuristic \
  --dataset wdbc --seed 42 \
  --candidate experiments/researchbench_t1/candidates/logistic.json

python experiments/researchbench_t1/t1.py submit \
  --work /tmp/t1-pilot-public --arm epipilot_heuristic \
  --dataset wdbc --seed 42
```

All four policies get identical data, estimator choices, evaluation
protocol, seeds and CPU budgets. They should share the same coding model,
model reasoning settings, allowed tools, instruction budget and provider
spend ceiling, but **must not share each other's research memory**.

- `agent_only`: plain coding agent, no persistent research history or EpiPilot
  planner, beyond the current trial's development feedback.
- `memory_only`: may call the `memory` command to write short, source-linked
  lessons after successfully validated trials. The next `task` packet includes
  up to six lessons from **this policy only**, without promoting notes to facts.
- `epipilot_heuristic`: use a separate Git research worktree and its own
  `.research` log, and actually use `epi iterate` to choose experiments.
- `epipilot_conditional`: likewise isolated, but preregister a valid
  four-outcome decision table and call `epi iterate --policy conditional`.
  Do not quietly fall back to the heuristic if the table lacks net value.

**Important:** the T1 runner controls ML experiments; it does not automatically
establish that a manually operated Agent obeyed the declared policy. For
scientific comparison, retain policy-specific `epi` logs, prompts, session
transcripts/usage summaries and frozen candidate choices, then audit them
before treating results as four strategy observations.

### Submit and evaluate only after all arms finish

Run `submit` for **every** frozen policy/dataset/seed cell before accessing the
final evaluator. Submissions select the best observed development AUROC; they
cannot overwrite a previous submission. Rejected/missing results do not
magically become successful experiments.

A separate operator must fill an audit JSON object with one exact key per
cell (`agent_only__wdbc__42`, etc.), each with explicit nonnegative integers:

```json
{
  "agent_only__wdbc__42": {
    "human_interventions": 0,
    "repeated_experiments": 0,
    "unsupported_claims": 0
  }
}
```

This is only one illustrative entry, not a complete pilot audit. Counts must
be derived from real session records; zero is **not** a default or an unknown
value. The benchmark refuses incomplete audits.

Then the operator (not the Agent) runs:

```bash
python experiments/researchbench_t1/t1.py finalize \
  --work /tmp/t1-pilot-public --vault /tmp/t1-pilot-secret \
  --audit /tmp/t1-pilot-audit.json

epi --root /tmp/t1-pilot-public benchmark \
  --manifest manifest.json --min-cases 2
```

Finalization seals **all** preselected candidates before opening any test
labels, verifies public/vault checksums, evaluates each cell with a hard
process timeout and emits evaluator artifacts plus a manifest accepted by
EpiPilot's existing equal-budget benchmark comparator. The final evaluation
is single-use by design, including after an interrupted evaluation; automatic
reruns are disallowed to prevent repeated test-set fishing.

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
