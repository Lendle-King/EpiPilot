# SERVER CODING AGENT — Benchmark-First EpiPilot research track

**Role:** you are the Coding Agent running on the research server.
**Repository:** `https://github.com/Lendle-King/EpiPilot`.
**Starting branch:** `experiment/benchmark-first-backbones`, or its reviewed PR
commit. Create an isolated working branch derived from the exact reviewed
revision; do not force-push or rewrite `main`, `epipilot/r1-pilot` or T1 receipts.

## Mission

We are changing the development strategy. Do **not** build more untested
EpiPilot meta-planner features first. Instead, reproduce and evaluate mature
existing research agents (AIDE ML, AIRA-dojo, an Agent-Only control, current
EpiPilot; optionally autoresearch and evidence-heavy systems) and **only then**
build new EpiPilot mechanisms on the winning reproducible strong baseline.

Your authorization is **engineering-only S0** for now. You may install public
dependencies in an isolated environment (subject to repository and machine
permissions), inspect code and upstream documentation, implement adapters,
build synthetic/offline test fixtures and run zero-model offline checks.
**You are NOT authorized to make paid model/API calls or conduct S1+ without a
separate explicit approval and measured cost cap.** In particular, do not
assume ChatGPT or Codex subscriptions authorize OpenAI API charges.

## Step 0 — source and operating checks

1. Read `docs/BENCHMARK_FIRST.md`, current research skill and the S0
   example. Run tests before making changes:
   `ruff format --check . && ruff check . && mypy src && pytest`.
2. Verify repo HEAD, current Git branch, Python/CUDA, optional Docker and
   the identity/available resources of the execution machine. Do not
   assume the server is the previously configured Mac. Check model/provider
   access separately; do not print or log secrets.
3. Clone upstream frameworks into separate **unprivileged** directories
   under `/srv/epipilot-benchmark-workspace/checkouts` (change root only if
   necessary). Pin and record immutable commit SHAs. No `main` / `master`
   floating branch refs for actual experiments.
4. Read upstream LICENSE / permitted usage (AIRA-dojo reports CC BY-NC 4.0),
   dependency lock, hardware and model provider requirements. Inspect scripts
   for external downloads, shell/network calls and risky executable code;
   perform installation inside a sandbox. Default skip unsafe/unavailable
   frameworks; report `NOT_QUALIFIED`, not a fake successful replacement.
5. Verify `epi-benchmark validate` and `preflight`. The sample manifest
   contains placeholder task/evaluator hashes and cannot authorize experiments.
   Replace with a task-specific immutable manifest only after writing real
   tasks and independently checked references.

## Step 1 — implement S0 framework adapters

Define a common, minimal adapter protocol, with one implementation per
framework: `prepare(public_task, model, budget, sandbox)`,
`run_dev_experiment(candidate, dev_evaluator)`,
`export_frozen_candidate()`, `finalize_receipt()`. Do not force
frameworks into an interface that changes their core search strategy; expose
native diagnostics and unsupported capabilities. Never implement an
"AIDE" adapter that merely invokes a generic Codex prompt.

**First wave:**
- A0 Agent-Only: current permitted Coding Agent with no EpiPilot planning.
- A1 AIDE ML: preserve its code-tree search, candidate history and feedback.
  Upstream: https://github.com/WecoAI/aideml
- A2 AIRA-dojo: preserve a native Greedy policy first, then separate
  MCTS/EVO arms if the installation and model interfaces can be reproduced.
  Upstream: https://github.com/facebookresearch/aira-dojo
- A3 Current EpiPilot: include its existing evidence-gated heuristic and
  exploration only as they actually operate. Do not invent missing abilities.
- Optional: autoresearch minimal hill-climbing control. Its original benchmark
  is a specific GPU/LLM training workload; report nonportable constraints.
  Upstream: https://github.com/karpathy/autoresearch

If an upstream framework requires a materially different model/provider or
does not support the server's verified transport, it may join the **native**
practicality track. It MUST NOT enter the matched architecture track with
an unverified model substitution. Native and matched results are different
tables. Freeze adapters before any outcome-driven selection.

S0 PASS requires: import and CLI capability smoke (no paid calls), actual
offline task artifact generation, safe error handling, pinned Git revision,
run log provenance, independent edit worktrees and a clean reset for every
cell. Each adapter must preserve its defining search mechanism.

## Step 2 — use pre-existing research tasks

Avoid declaring superiority based on the R1 easy WDBC/feature-shrinkage
synthetic tasks. Use real community tasks and strong baselines when their
license, dataset distribution and independent evaluation are available:
- MLE-bench: https://github.com/openai/mle-bench
- AIRS-Bench: https://arxiv.org/abs/2602.06855
- RE-Bench: https://github.com/METR/RE-Bench

Start with 2–3 **independent** small or moderately priced development tasks,
across at least two task families. Each task requires frozen baseline,
scoring direction, editable files, real training/dev feedback and a separate
sealed test. Keep train/dev/hidden tests distinct; do not repeatedly inspect
or tune to official hidden outcomes. Dataset downloads and licenses require
explicit review, not a blind workflow download.

Tasks should be non-ceiling and have more than one plausible improvement
strategy. Run simple heuristic search and strong reference baselines on a
calibration set *before* selecting official comparison tasks. Preserve easy
tasks as negative controls, not as proof of deep scientific reasoning.

## Step 3 — implement common execution, fairness and cost harness

Implement a task-adapter-evaluator seam; no framework may directly control
the trusted hidden scorer. All agents get:
- Identical task statement, baseline repo SHA, initial files, tool permissions,
  dev evaluation feedback, per-cell wall time and training budget.
- Isolated checkout, clean session and optional reset seeds. Randomize
  execution order, and run jobs sequentially when compute resources might
  contend. Do not share research memory across arms.
- A source and configuration manifest (model provider, exact model ID,
  reasoning effort, client transport, version and whether availability was
  *actually confirmed*), adapter SHA, seeds and original task artifacts.
- Append-only receipts for every model *attempt*, tokens when reported,
  failures/timeouts, retries, candidate hashes, dev experiment hashes,
  CPU/GPU training time, wall time, observed memory and budget.
  Preserve empty results/failed runs. Never silently refill unused slots.
- Native cost logging including zero/unknown provider billing; do not
  claim dollars saved if server/provider charges cannot be measured.

**Hard caution:** a subprocess counter alone does NOT cap API requests of
an external CLI. Before paid execution, implement and test a quota-aware
provider proxy / an adapter-native documented request meter, or a separate
provider budget hard limit and fail-closed mechanism. Do not expose model
credentials to model-generated code or untrusted candidate execution.

For private GitHub Actions, keep secrets in a private control repo and use
separate runner identities for untrusted candidate creation and trusted
prediction scoring. Secrets must not appear in public commits or artifacts.
A `sealed_scoring` JSON flag is an assertion, not isolation proof.

## Step 4 — offline S0 acceptance and mock receipts

Without model calls, implement tests for:
- Actual framework adapter startup, environment/required flags and supported
  or unsupported backbone types.
- A clean Git SHA checkout and refusal on repo source mismatch or dirty code.
- Malformed/missing/duplicate candidate and result payloads, unexpected
  evaluator SHA, source-digest change and data leakage.
- Each metric's direction and normalization, all matched task/seed/arm
  identities, exact per-arm cost caps, failed/timeout cells, zero/unknown
  billing and no artificial omitting of failures.
- Restart/timeout reconciliation and absolute resource ceilings. Existing
  Mac whole-machine RAM cap is 95% sampled; on server enforce configured
  GPU/VRAM/CPU/physical RAM caps as appropriate, with locks and timeouts.
- A **mock-only** test of 4 attempted model calls, 3 dev trials and
  30-minute per-cell ceiling, with intentional crashes. Real S1 budget may
  change only by a recorded approval and protocol revision.

Outputs to write in an auditable, nonsecret location:
`S0_SOURCE_AND_LICENSE_AUDIT.md`,
`S0_ADAPTER_CAPABILITY_MATRIX.json`,
`S0_OFFLINE_RUN_RECEIPTS.jsonl`,
`S0_PRIVATE_EVALUATOR_THREAT_MODEL.md`,
`S0_BUDGET_GUARD_TESTS.md`, and `S0_GATE_REPORT.md`.
Every conclusion must include input SHAs, executed commands, environment,
source versions, actual costs and all failed runs.

## Step 5 — after S0 passes, request authorization for paid S1

Proposed **S1 only**, not approved: 3 independent development tasks,
4 core arms (Agent-Only, AIDE, AIRA Greedy, Current EpiPilot) and one
independent clean run per arm/task = 12 cells. Use small conservative hard
per-cell model and dev-experiment caps, freeze them from dry-run latency and
actual pricing; specify total US dollar exposure before requesting approval.
If costs are unavailable, record that and **do not launch**.

Evaluate:
- Verified best-so-far performance vs wall time (AURP), and final separate
  held-out score after all code/arms are frozen.
- Research failure, duplicate experiments, invalid runs and cost of repair.
- Evidence-based scientific conclusions and mechanism-accuracy only where
  independent domain labels/rubrics exist; never award a scientific-insight
  score for model output verbosity.
- S1 is an adapter/fairness screen, not a statistical victory claim.

S2: select two best feasible frameworks using ~6 development tasks x two
fresh runs, with cost and memory-only controls. S3: winning baseline B0,
+B1 evidence memory, +B2 problem model, +B3 adaptive research controller;
retain matching extra-compute controls. S4: independently held-out task
families and task-cluster paired CIs; pre-register statistical/effect criteria
before looking at S4 outcomes. Do not pick only successful tasks.

## Required completion report

At the end of the CURRENT engineering-only task, update the repo and open a
PR with a concise summary containing:

1. Exact code SHAs, runnable adapter capabilities, licenses and known gaps.
2. Detailed commands and CI results, including negative tests.
3. The *real* number of model/API calls (expected **zero**), dev experiments,
   elapsed CPU/GPU time and provider costs, with unknown fields explicit.
4. A task/arm/seed eligibility table and evidence about task difficulty.
5. Specific S0 PASS/FAIL/INCONCLUSIVE and blockers for each candidate.
6. Recommended S1 cost-capped manifest, but no scheduled or unattended paid
   jobs until explicit operator approval.

If an existing adapter cannot be reliably supported, document the blocker
and advance with the qualified controls rather than fake equivalence.
You may make engineering decisions independently inside these safety gates.
