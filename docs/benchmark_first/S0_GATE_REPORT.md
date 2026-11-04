# S0 gate report — 2026-11-04

**Overall S0: FAIL. S1: NOT AUTHORIZED. No winning framework selected.**
The engineering implementation and its automated checks are ready for review; framework
qualification is deliberately incomplete. AIRA's required upstream CLI still fails, the real
Agent-Only executable/model transport is unqualified, and private hidden evaluation/model parity
have not been independently established. This report does not promote smoke outputs to facts
about scientific efficiency.

## Source and executable scope

Base: `d20c891a00e4904ff9a262b1eeadc3328cd7f776`, from
`Lendle-King/EpiPilot:experiment/benchmark-first-backbones`. Source revisions, licenses, image IDs
and gaps are in `S0_SOURCE_AND_LICENSE_AUDIT.md`, `S0_ADAPTER_CAPABILITY_MATRIX.json` and the
artifact index. The final adapter bundle digest is in the final run summaries. The Git delivery
commit is the PR head; recording it in its own contents would be self-referential.

Implemented typed prepare(task, mock model, budget, sandbox), native dev execution, frozen
candidate export and append-only completion receipts. Common synthetic task input is frozen;
each cell has separate source/task worktrees and an identical deterministic baseline Git commit.
No existing task checkout is reset, reused or refilled. No external framework algorithm is copied
or replaced with a generic prompt. No paid runner exists in this API.

| Arm | Native offline core | Native startup/CLI | Specific S0 status |
|---|---|---|---|
| Agent-Only | Scripted executor generates/modifies Python and predictions | Offline seam CLI help | INCONCLUSIVE: real coding-agent binary/version/backend not qualified |
| AIDE | Original Agent.step, draft/debug/improve policy, journal/feedback; native serialize/reload | Original aide.run, zero steps, fixed metric direction, reports off | PASS_OFFLINE_ONLY; full real-task ML dependencies/live model not qualified |
| AIRA Greedy | Original Greedy.step, operators/memory/policy, journal and save/load checkpoint | Original dojo.main_run --help fails: missing mlebench | NOT_QUALIFIED; core artifact generation is not top-level runner qualification |
| Current EpiPilot | Original heuristic ranking, canonical research store, preregistration/record/verify/abort | Original epi research CLI help and synthetic lifecycle | PASS_OFFLINE_ONLY; existing exploration behavior only, no new planner capabilities claimed |
| autoresearch | Not run | Not run | NOT_QUALIFIED: no NVIDIA GPU and no LICENSE in pinned tree |

All native core functions use scripted responses at the provider boundary only. AIRA's native
prompts contain its original assumed GPU environment; this does not match this Mac and is another
reason no Matched eligibility is asserted. No provider/model substitutions were made.

## Executed checks

Before changes: `ruff format --check .`, `ruff check .`, `mypy src`, `pytest -q`: **202 passed**.
After changes: formatting, lint, strict mypy (67 source files), **218 tests passed** in 32.15 s.
One existing FastAPI/Starlette deprecation warning remains. Managed delivery repeats these checks
against the exact integrated candidate in an independent worktree. GitHub CI must be inspected
on the PR; local results must not be described as remote CI results.

Tests cover positive/negative budget, recovery, duplicate, strict artifact, source identity,
metric direction/normalization and complete-receipt contracts. Real Docker boundary tests verify
no host sealed-file access/credentials/socket/network, read-only source, a wall-limit kill and a
cgroup OOM kill. See `S0_BUDGET_GUARD_TESTS.md` and `S0_PRIVATE_EVALUATOR_THREAT_MODEL.md`.

Final normal and intentional-crash configurations: 8 synthetic engineering cells, **28 mock
request attempts, 16 synthetic dev trials**, four intentional failed trials, and one frozen
prediction export execution per cell. Four cores produce code and numeric predictions in both
modes. AIRA's CLI failures prevent full cell qualification. Mock best-so-far scores on this
ceiling toy are **not** included in any architecture ranking, hidden quality or AURP result.

Across all retained exploratory/final framework runs: 21 cells, 6 failed cells, 22.169552 observed
native CPU seconds and 38.798923 observed process wall seconds. The final eight cells account
for 8.819994 native CPU seconds. Builds, coding-agent reasoning, tests and host overhead are not
included in those process totals; their aggregate CPU/GPU/financial cost is unknown. Every
reported process keeps its elapsed time, exit status and log hash. No scientific duplicate
experiments were run; deliberate engineering reruns are enumerated rather than hidden.

**Benchmark paid/API model attempts: 0; provider tokens: 0; provider charges: $0.** No real
training/GPU experiments. Interactive coding-session tokens/charges, electricity and installation
cost are unmetered/null; no total-dollar saving is claimed. Isolated dependency downloads did use
network access during image building; candidate runtimes had no network/provider credentials.

## Task/arm/seed eligibility

| Task | Seed | Agent-Only | AIDE | AIRA Greedy | EpiPilot |
|---|---|---|---|---|---|
| synthetic-linear, normal | 17 | seam only | offline pass | core pass / CLI fail | offline pass |
| synthetic-linear, first-trial crash | 17 | seam recovery | native debug/reload | native debug/reload / CLI fail | native abort/verify |
| Three real S1 candidates | proposed 17 | ineligible | ineligible | ineligible | ineligible |

No real training/dev/hidden datasets were fetched, no task difficulty was calibrated, and no
held-out scientific scores exist. The real-task proposal spans text/tabular/graph families;
strong references, licenses, data splits and compute feasibility must be validated before task
selection. Unknown hashes/prices remain null. Native and Matched each propose 12 distinct cells
and must never share a ranking or implicit spending authorization.

## Reproduction

Use Python >=3.11 and installed project dev tools; `PYTHONPATH=src` binds checks to the candidate.
Clone sources at the audited SHA in a separate workspace; do not run third-party scripts on the
host. Build both explicit images, inspect their immutable IDs, and compare resolved packages to
the recorded environment files. If image IDs differ, create a newly named/reviewed offline
config; never edit a completed run's frozen copy. The checked-in config records this machine's
actual image IDs rather than floating tags.

```bash
ruff format --check .
ruff check .
mypy src
pytest -q
python -m epipilot.benchfirst.cli validate --manifest experiments/benchmark_first/s0_example.json
python -m epipilot.benchfirst.cli preflight --manifest experiments/benchmark_first/s0_example.json --workspace "$BENCH_WORKSPACE"
docker build --target aide -t epipilot-s0:aide experiments/benchmark_first/s0
docker build --target aira -t epipilot-s0:aira experiments/benchmark_first/s0
docker image inspect epipilot-s0:aide epipilot-s0:aira --format '{{.Id}}'
python -m epipilot.benchfirst.cli s0-run --config experiments/benchmark_first/s0/config.json --workspace "$BENCH_WORKSPACE" --output "$NEW_NORMAL_RUN"
python -m epipilot.benchfirst.cli s0-run --config experiments/benchmark_first/s0/crash-config.json --workspace "$BENCH_WORKSPACE" --output "$NEW_CRASH_RUN"
python -m epipilot.benchfirst.cli s0-reconcile --workspace "$BENCH_WORKSPACE" --output "$INTERRUPTED_RUN"
```

The example's old EpiPilot pin is expected to report a mismatch against d20c891. It is a schema
example, not an experiment manifest. Source mismatches must not be silently rewritten. The new
`S0Config` is explicitly synthetic/mock-only and has no path to authorizing S1.

## Outstanding gates

1. Qualify the actual host coding-agent executable/transport without paid calls; review separate
   Native/Matched model capability records. Unknown API access is never replaced silently.
2. Install/audit AIRA's actual task runner and required MLE-bench environment on suitable hardware;
   verify license permission. Complete real ML dependency and startup tests for every selected arm.
3. Independently deploy/audit private scorer and untrusted inference isolation, request-level
   quotas, provider retry/token accounting and server resource monitors.
4. Calibrate real task references on independent development data, freeze all task/model/evaluator
   manifests, quote total financial exposure and obtain explicit approval of a revised S1 plan.

S1 v1 is a frozen **blocked design proposal**, not a request to spend now. No paid job or automation
has been created. B0/B1/B2/B3 development and unseen-task scientific confirmation remain later stages.
