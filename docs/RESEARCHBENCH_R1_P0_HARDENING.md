# ResearchBench-R1 P0 hardening: task validity, sealed evaluation and next runs

Status: **P0_BLOCKED**. This branch provides reference-free, richer PUBLIC
task prototypes and a prediction-only scoring contract, but it does NOT yet
satisfy the independent reference, secure private evaluator or P1 budget gates.
Do not spend model tokens or describe this as a successful Agent-Only comparison.

## What changed

- `p0_tasks.py`: deterministic two-server queue with a shared accelerator,
  three workload regimes (deadline pressure, accelerator contention and priority
  tradeoffs), a legality-scored schedule interface and three fixed heuristics;
  classification with multiple train/dev/shift regimes, interacting features,
  a trainable regularized logistic baseline and worst-group error.
- `p0_audit.py`: reports reproducible public calibration performance by regime
  and cheap control policies. Always returns `P0_BLOCKED`. It deliberately
  cannot infer a valid oracle/reference, difficulty pass or private isolation.
- `p0_guarded.py`: runs the zero-model calibration behind a stable per-user
  Mac lock, whole-host sampled 95% RAM guard and subprocess timeout.
- `sealed_eval.py`: **trusted scorer** reads only small, strict JSON
  predictions/placements and separately mounted private rows. Candidate code
  is never loaded by that scorer.

Old `calibration.py`, `tasks.py`, `explore_tasks.py` and historical receipts
remain archival public prototypes. Their reference code and their public
simulation seeds **must not be distributed as a hidden evaluation**. New
`p0_tasks.py` is also a developer-only task generator, **not** an agent
workspace package. A formal agent task package must contain only frozen,
allowlisted task API, baseline, public train/dev data and an editable solution
entry point. It must exclude private generation parameters, private labels,
reference answers, runner credentials and scorer runtime.

## P0-A: offline task difficulty calibration

Use a Mac with psutil available, no OPENAI_API_KEY required:

~~~bash
python -m pip install psutil
python -m experiments.researchbench_r1.p0_guarded \
  --cases-per-regime 4 --output /tmp/r1-p0-first.json
~~~

Exit code **2** is expected: the output is a descriptive public calibration,
not a P0 pass. Use a fresh output name for each independent run; do not refill
or overwrite results. The emitted guard receipt records sampled whole-machine
memory and elapsed time. Threshold 95% is checked on sampled data; monitor
cannot guarantee absence of transient peaks. Lock is stable across cooperating
jobs for one Mac user, but cannot lock all unrelated programs.

For the scheduling family, freeze stronger baselines beyond EDF and single
weighted-duration priority; compare all heuristics by independent task *regime*
and find reproducible reversals before constructing a formal scientific task.
For classification, compare a trainable, regularized multifeature baseline
against fixed three-draw development-selected controls on *shift* populations.
Treat a large cheap-control gain as a **task difficulty problem**, not as R1
success. A second independent reference implementation and calibration splits
must be created outside the agent's visible workspace. Random-search outcomes,
variance and task-level margins determine whether a task survives P0.

### Suggested P0-A gate (freeze before selecting cases)

- Two truly independent executable problems from two different families, with
  real modifiable code, valid constrained actions, at least two plausible
  hypotheses and meaningful diagnostics.
- Reproducible per-task strong baseline and independently implemented reference;
  prespecified positive reference gap (e.g. >=0.10 on a fixed [0,1]
  task-normalized quality scale) on fresh calibration instances, including
  paired uncertainty/variance estimates.
- Three-trial randomized or hand-coded cheap-search controls should not capture
  >=50% of reference gap in a *large majority* of instances. This is an
  engineering difficulty filter, not a theorem about scientific intelligence.
- Include some easily solvable NEGATIVE CONTROL instances to test if EpiPilot
  can avoid costly research when exploration is unnecessary.
- Independence is by task/data-generation family, not estimator seeds on one
  dataset. Split by task ID and regime, never select tasks by agent test score.

## P0-B: genuine PRIVATE evaluation boundary (not yet audited)

Use the **private** `Lendle-King/Remote-Control` repository for orchestration
and separately provisioned sealed labels, NOT the public repository. The
OpenAI API key is injected only into the model-calling controller job, never
into evaluation jobs or candidate-code sandboxes.

There are **three trust domains**:

1. Agent/controller worktree: only approved public task package, scoped
   development feedback and budget; agent edits its own candidate code.
   No private labels or private test configuration.
2. Untrusted prediction job: ephemeral isolated runner/container with
   read-only unlabeled evaluation inputs, one frozen candidate hash, no
   credentials/network access and no private labels. Generates **only**
   a strict JSON artifact with sample_id -> prediction or job placements.
   Real OS/container escape resistance requires a separate security audit.
3. Trusted scoring job: fresh separate runner; verified frozen scorer SHA and
   private inputs+labels. It downloads only the JSON submission artifact,
   validates its schema, identity, complete row set and candidate provenance,
   runs `sealed_eval.py` without importing the candidate module, writes
   aggregate metrics and hashes, and never returns private examples/labels
   or per-row errors to the agent.

Never run candidate source code in the scorer process, never mount hidden
labels/secrets into the untrusted job, and do not rely on chmod or worktrees
under the same identity as an isolation boundary. A self-hosted Mac agent
with GitHub credentials and private workspace files is **not** automatically
a safe host for untrusted candidate execution. Consider distinct ephemeral
GitHub-hosted runners and hard network/secret isolation where available.

### Prediction submission contract

Both modes require the immutable JSON object keys:

- `schema_version=1`, `task_id`, `kind`, `candidate_sha256`, `rows`.
- Classification rows: `{"sample_id":"S00001","prediction":0}`.
- Scheduling rows: `{"job_id":"J0001","server":0,"start":1.234}`.

All IDs must match private input rows exactly; bools, nonfinite numbers,
duplicate JSON keys, duplicate/missing/extra predictions, impossible
schedule placements or accelerator conflicts must fail closed.
No code execution or external data/network access is performed by
`sealed_eval.py`. It does not independently prove that a supplied
candidate SHA matches the program that generated the submission: the private
orchestration layer must attest the frozen runner source and record hashes.

Inside the **trusted, isolated scorer job only**, after the untrusted
prediction process has exited:

~~~bash
python -m experiments.researchbench_r1.sealed_eval \
  --submission submission.json \
  --private-rows private-rows.json \
  --task-id r1-example-0001 --kind classification \
  --output aggregate-private-score.json
~~~

`private-rows.json` contains schema_version, task_id, kind, rows. Its
classification rows contain id, stable_a, stable_b, nuisance, group and
label; scheduling rows contain id, release, duration, deadline, weight
and accelerator. Keep private features confidential as appropriate and
do not upload hidden rows or per-row predictions to PUBLIC artifacts.

### Mandatory adversarial isolation acceptance tests

- Candidate attempts to open scorer paths, private labels or GitHub token.
- Candidate tries arbitrary env variables, path traversal, symlink and
  workspace-mount escapes, network egress or log dumping.
- Candidate produces duplicate, missing, NaN, malformed or forged row IDs,
  incorrect task_id/SHA and conflicting schedule/resource allocations.
- Runner crash between submission upload and scoring; no double-execution
  or stale artifact substitution.
- Trusted scorer can read only pinned JSON and private rows and never
  executes any untrusted Python/repository hooks.

Actual private jobs and these security audits **do not exist in this
public branch**; they must be provisioned and checked before P0 can pass.

## P0-C: instrumentation and budgets before P1

The guarded public diagnostic does not call models. The next step is a frozen
P1 Controller with all of these invariants, with regression tests:

- Equal pinned `gpt-6-luna` + `model_reasoning_effort=max` for A0/A3;
  reject unsupported model without fallback.
- Two independent tasks x two fresh sessions x two arms = **8 cells**;
  <=4 agent call attempts/cell, <=3 dev experiments/cell,
  <=30 minutes/cell, and a global upper bound of 32 model attempts.
- Independent, private unmodified final evaluator; EpiPilot's only extra
  authority is its disclosed discovery skill and local research state.
- Every attempted model call, timeout, null result, crash, no-op, duplicate
  and training run consumes its budget as defined *before execution*.
  Record token usage, prompt/cache counts if available, real elapsed time,
  partial spending and failures; unknown provider dollar spend stays unknown.
- Stable per-user Mac execution lock, sampled whole-host RAM <=95% at
  observations, subprocess timeout and fail-closed crash recovery; no
  automatic relaunch or unauthorized remote action.
- All eight cells reach an auditable terminal status; failures are not
  excluded; the private score never influences subsequent agent choices.

Do **not** launch P1 from this PR. First freeze the private setup and
P0-A reference calibration; run source-pinned and adversarial P0-B tests;
implement a receipt-backed P1 controller; then record an explicit
P0 PASS decision by an independent reviewer. A green pytest run,
positive reference improvement, memory telemetry or the existence of an
API secret is not a P0 PASS.

## Engineering-only checks

~~~bash
ruff format --check .
ruff check .
mypy src
pytest -q tests/test_researchbench_r1.py tests/test_researchbench_r1_p0.py
pytest
~~~

## Public Mac calibration, 2026-11-02

The first four-cases-per-regime guarded run used revision
`1dbe23437952c7d74af990dc9cbf182e3d917e85` and saved
`/private/tmp/r1-p0-first.json` (SHA-256
`53c46ed4131e04e28a2b6931e564a80106dd17a2b50d530fa88d65ba9151fe6b`).
The process returned the expected 2, with 0.713 seconds and peak sampled memory
75.19%. All 12 queue cases ranked EDF first. This scheduling comparison is
**invalidated as a comparison of online policies**: the original implementation
sorted all jobs by priority even before release, causing future jobs to reserve
resources and unnecessarily delaying available work. The receipt is retained,
not rewritten.

The corrected implementation chooses among released, resource-eligible jobs at
each arrival or completion. The regression tests demonstrate the former defect.
A second guarded run on the same public seeds produced
`/private/tmp/r1-p0-online-formatted.json` (SHA-256
`db9f9154cd7a5d0847d4776240eb7e2a4d44cd3bbde2b5c404178361c967bc77`);
the exact `p0_tasks.py` SHA-256 at measurement time was
`9b4e1b66fdafcc92fb22bfe1efc2c39b07260f541b31b91680aff9dc8bfc6497`.
Its expected exit code was 2, elapsed time 0.712 seconds, and peak sampled
whole-host usage 75.15%. The intermediate pre-format run has identical JSON.
Both outputs remain local; source hashes and metrics here provide a compact
index rather than a hidden or independent evaluation.

| Public regime | EDF loss | WSPT loss | Hybrid loss | Wins EDF/WSPT/Hybrid |
| --- | ---: | ---: | ---: | ---: |
| tight | 16.881 | 5.829 | 5.874 | 0/4/0 |
| contention | 9.729 | 4.412 | 4.491 | 0/3/1 |
| weighted | 4.033 | 1.465 | 1.469 | 2/2/0 |

The four-case samples are too small to establish stable regime reversals or
reference quality. In classification, baseline versus three-candidate control
worst-group error was 0.572 versus 0.183 (spurious), 0.410 versus 0.179
(interaction), and 0.601 versus 0.193 (mixed). No independent reference gap is
available, so the fraction of a reference gap captured cannot be computed.
The cheap control's large gains justify further difficulty investigation; they
do not establish an R1 result. No model calls, private labels, private scoring,
or paid pilot ran. P0 remains blocked on the gates above.

After this measurement, the equivalent expression `set((0, 1))` was changed
to `{0, 1}` to satisfy lint. The measurement's source digest above remains the
actual measured bytes, not the post-lint source digest.
