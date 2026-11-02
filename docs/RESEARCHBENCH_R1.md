# ResearchBench-R1: exploratory calibration and P0 status

Status: **P0 NOT PASSED**.

## P0 hardening checkpoint (2026-11-02)

The new zero-model, reference-free calibration and prediction-only evaluator
contract live in [RESEARCHBENCH_R1_P0_HARDENING.md](RESEARCHBENCH_R1_P0_HARDENING.md).
They introduce multi-regime schedule legality checks, trainable shift baselines,
a development-only fixed small-search comparator, strict per-row private
scoring, a guarded P0 diagnostic launcher and fail-closed readiness report.
The old exploratory numbers below remain immutable historical observations.

These additions **do not** constitute an independent reference implementation
or a security-isolated private evaluator. Run the adversarial scorer isolation
audit and private task calibration before an operator can mark P0 passed.
The official private P1 controller and research-policy comparisons have not run.

 No R1 agent session, private evaluation, or paid model call
has been run. The code under `experiments/researchbench_r1` contains public diagnostic
prototypes, not frozen research tasks or a hidden evaluator.

## Objective and comparison

The target comparison is Agent-Only (A0) versus EpiPilot Discovery (A3) on research
problems with code editing, experiments and equal tools, model, feedback, runtime and
compute. Basic Memory (A1), Current (A2) and a matched diagnostic-cost control (A4)
are for later attribution. A3 uses the existing Discovery CLI through the coding agent;
it does not assume an automatic meta-controller. All candidate history must be frozen
before a private evaluator scores any candidates. The evaluator must not feed hidden
results back into sessions.

The proposed model is `gpt-6-luna` at `max`, with no substitution. P1 is eight cells
(two independent tasks × two fresh sessions × two arms), at most four model calls,
three development experiments and 30 minutes per cell. All failures and stops remain
in the analysis. P2 and P3 require their own frozen configurations; P3 remains subject
to the separate approval and power-analysis condition in the request.

## Public calibration prototypes

`calibration.py` provides deterministic synthetic simulations for testing plumbing:

* A single-server arrival queue compares earliest-deadline-first with a weighted
  processing-time priority, using mean weighted tardiness. This is not yet the
  requested multi-resource environment; the priority rule is visible in source.
* A classification simulation compares two fixed linear scoring rules under a
  generated nuisance-feature shift. It has no trainable model or training dataset yet.

Exploratory seeds 0–99 on the current implementation give mean scheduling loss
10.0153 for the baseline and 3.0408 for the reference, and mean classification error
0.49391 and 0.10610 respectively. Each reference wins on all 100 seeds. These are
same-distribution, public exploratory simulations, not independent holdout evidence;
the reference was revised after initial feedback. The extremely weak classification
baseline and obvious visible reference make this unsuitable for a formal task or a
claim about research difficulty. The initial deadline/weight scheduler actually lost on
the first ten seeds and was replaced. These observations help identify unfinished work,
not demonstrate that P0 has passed.

Reproduce exploratory values with:

```sh
python -c 'import statistics as s; from experiments.researchbench_r1.calibration import scheduling_loss, classification_loss; [(print(f.__name__, s.mean(f(i,"baseline") for i in range(100)), s.mean(f(i,"reference") for i in range(100)), sum(f(i,"reference") < f(i,"baseline") for i in range(100)))) for f in (scheduling_loss, classification_loss)]'
```

`progress_area` computes the normalized integral of validated best-so-far quality,
holding the last quality to the budget endpoint. It rejects unordered or out-of-budget
timestamps, nonfinite values and scores outside [0, 1]. The caller is responsible for
binding times to the moment the candidate was produced, not the later private scoring
time; independently establishing score validity; and freezing baseline, reference,
normalization and failure treatment. A score alone is not evidence of provenance.

## Follow-up prototypes and exploratory difficulty check

`tasks.py` adds a nonpreemptive, two-server queue with a shared accelerator, plus a
trainable regularized logistic model on synthetic feature-shift populations. Candidate
implementations and reference answers are visible in the same source file: neither is
a sealed task package. On 30 public cases, scheduling EDF has mean weighted tardiness
2.1930, versus 1.1710 for the reference; the reference wins 26/30, and the paired
difference has standard deviation 1.1714. Classification baseline error is 0.3939,
reference error 0.1560, and a development-selected three-draw random shrinkage search
achieves 0.2509 error; 21/30 cases obtain at least half the reference gain. This is
strong evidence that the current simplistic classification design needs a harder and
better-controlled formulation, not evidence of research-agent capability. Neither
population is the hidden test and the reference is not independently implemented.

The exact exploratory procedure is `python -m experiments.researchbench_r1.explore_tasks`.
One initial run was made without the required memory monitor and exclusive lock, so it
is retained as an exploratory protocol deviation and cannot qualify P0. It was repeated
with `resource_guard.single_runner_lock` and `guarded_run`: return code 0, elapsed
0.706 seconds, peak sampled whole-Mac use 75.08%, minimum sampled availability
5.98 GiB. The monitor samples rather than enforcing a kernel limit. The new guard is
adapted from the T1 branch, uses the same cooperating-process lock and refuses launch
when `psutil` is unavailable. Its guard must wrap all subsequent experiments; tests
and ordinary engineering commands are not experiment cells. Same-process lock discipline
cannot prevent unrelated jobs from using the machine.

## P0 gates still open

1. Construct two real executable, independently parameterized task packages with
   strong baselines, train/development inputs, editable algorithm interfaces, explicit
   constraints and reproducible independent reference implementations. Include true
   resource contention for scheduling and trainable models for distribution shift.
2. Freeze distinct calibration and private test populations before formal sessions;
   demonstrate nontrivial, repeatable improvement and bounded variance, and measure a
   predefined random-search comparator. Do not use the public prototype seeds or
   exposed reference code as the hidden test. Include authentic open-source research
   code in the later formal suite rather than generalizing from synthetic puzzles.
3. Build a separate evaluator and prove by adversarial tests that candidate code and
   agent tools cannot read its code, hidden inputs, answers, credentials or feedback.
   Separate directories, Git worktrees, chmod under the same identity, and scope-diff
   checking are not sufficient isolation. The evaluator must also safely execute
   candidate code without exposing private material; merely hiding files from the
   agent process does not protect secrets passed into untrusted candidate code.
4. Independently audit matched agent capabilities, frozen runtime and feedback,
   candidate snapshots, timestamps, resource accounting, failure records and absence
   of leakage. Ensure a shared verified baseline fallback and all terminal outcomes.
   An exclusive cooperating-job lock, a sampled whole-machine memory guard at 95%,
   and task timeouts are needed; sampled monitoring cannot guarantee zero overshoot.
5. Freeze per-task raw metrics and normalization, distinguish diagnostic truth from
   model-generated claims, and record all candidate and evidence provenance. Only
   after all gates pass can a P1 controller run with the authorized model and limits.

AstaBench emphasizes comparable tools, model costs and end-to-end scientific tasks;
FIRE-Bench evaluates rediscovery of independently verifiable research findings. They
informed these design goals, but no task from either benchmark is implemented here:
[AstaBench](https://allenai.org/asta/bench),
[FIRE-Bench](https://proceedings.mlr.press/v306/wang26d.html).
