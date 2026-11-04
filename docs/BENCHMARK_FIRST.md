# Benchmark-First: evaluate mature research agents before evolving EpiPilot

**Status:** S0 engineering adapters and controlled offline native smoke are now available;
**zero benchmark provider calls**. The overall S0 gate remains FAIL. See
[the S0 gate report](benchmark_first/S0_GATE_REPORT.md) for pinned sources,
actual receipts, known gaps and the unstarted S1 design.
This branch does not select a winning agent, supersede the existing research plugin,
prove an isolated evaluator, or retrospectively modify the ResearchBench-T1/R1 data.

## Goal

Replace "invent a new complete research agent and then discover whether it helps"
with "reproduce existing strong research agents, compare under meaningful
research tasks and equal constraints, then evolve the strongest reproducible
base through controlled component ablations."

There are two different leaderboards:

- **Native practicality track:** each framework uses its documented model and
  native tools. Measure real cost, deployment friction and quality. DO NOT
  attribute differences to the agent architecture.
- **Matched architecture track:** same model/provider/transport, prompts' task
  information, hardware, editable files, tools, dev-evaluator feedback, per-cell
  budget, and reset procedures. Only implementations with **verified** equivalent
  model interfaces can enter. No silent provider/model fallback.

## Frameworks and source checks

The S0 registry includes:

| Candidate | Source | Role |
| --- | --- | --- |
| Agent-Only | https://github.com/Lendle-King/EpiPilot | coding-agent baseline |
| AIDE ML | https://github.com/WecoAI/aideml | lean iterative code tree search |
| AIRA-dojo | https://github.com/facebookresearch/aira-dojo | greedy, MCTS, evolution under one harness |
| autoresearch | https://github.com/karpathy/autoresearch | minimal fixed-budget research loop (original GPU workload) |
| EpiPilot Current | https://github.com/Lendle-King/EpiPilot | controlled original policy |
| EvoCast | https://github.com/18e0-x/EvoCast | domain-specific architecture evolution |
| YouRA | https://github.com/PrayPrey/Your-Research-Agent | evidence and recovery control |
| AI Scientist v2 | https://github.com/SakanaAI/AI-Scientist-v2 | end-to-end scientist comparison |

The first S0 sample pins 5 candidate source revisions. Its task IDs, dataset
hashes, evaluator hashes, baseline and reference scores are **placeholders**,
not experiment evidence. Run `epi-benchmark validate`, then clone the sources
into private or isolated server worktrees and run `epi-benchmark preflight`.
A matched Git SHA does not mean the adapter has been successfully integrated.
AIRA-dojo currently lists CC BY-NC 4.0: audit permitted use before adapting
or redistributing. Some frameworks require specific GPU stacks, Claude CLI,
Docker, Slurm or LaTeX; check this at S0 rather than disguising unsupported
features with fallback implementations.

## Design and data

```text
Frozen task & source manifests
   |                                  +-- trusted private evaluator
   v                                  |     (not implemented in this PR)
Source/adapter qualification --> task-scoped agent worktrees
   |                              +--> native track             +--> matched-model track
             \                          /
              \-- isolated, complete run ledgers --/
                                |
                    descriptive research-progress scores
                                |
               developer selection + genuinely held-out confirmation
```

Existing `epi benchmark` remains a descriptive scalar results comparator.
The `validate`, `preflight` and `compare` commands remain provider-neutral schema/source
checks and descriptive receipt analysis. New `s0-run` executes pinned native cores
with scripted model responses in offline Docker; `s0-reconcile` settles interruptions
without retrying. Neither starts real training, authenticates a provider, meters paid
requests or authorizes S1. See the implementation checklist in
[SERVER_CODING_AGENT_BENCHMARK_FIRST.md](SERVER_CODING_AGENT_BENCHMARK_FIRST.md).

### CLI

```bash
python -m pip install -e '.[dev]'
epi-benchmark catalog
epi-benchmark validate --manifest experiments/benchmark_first/s0_example.json
# Under a separate workspace with all listed repos cloned at pinned commits:
epi-benchmark preflight \
  --manifest experiments/benchmark_first/s0_example.json \
  --workspace /srv/epipilot-benchmark-workspace
# Only after trusted scoring produces a complete ledger:
epi-benchmark compare --manifest frozen-real-plan.json --ledger results.jsonl
```

`preflight` makes no network or model calls: it checks local HEAD and worktree
cleanliness. Missing worktrees are reported, never auto-cloned.

`compare` requires **every** (adapter, task, replicate) cell, checks exact
evaluator SHA claims and ceilings, rejects unsupported checkpoint claims,
and reports descriptive quality-versus-wall-time progress. Every failure and
timeout stays in the ledger; missing dollar prices stay null. Score hashes
and a `sealed_scoring=true` flag **must not be treated as cryptographic proof
of evaluator independence**, since a malicious untrusted writer could forge
the whole receipt. Independent source-bound scorer attestations and actual
sandbox isolation are still required. This is not a full statistical test.

## Primary progress metric

For each task, freeze a strong baseline J0 and independent reference Jref
before comparing the agents. Convert performance (with its declared direction)
to Q=(J-J0)/(Jref-J0), clipping to [0,1] and retaining raw scores.
Plot verified best-so-far Q against a *common* budget axis; time AURP is:

```text
AURP = (1/B) * integral_{0 to B} Q_best(t) dt
```

This favors early verified progress. Hidden scores for frozen historical
checkpoints may only be computed after the agent sessions close. Use actual
elapsed wall-clock time B for the primary time curve; also report token and
training cost axes separately. If a model call fails or a cell stops early,
carry forward the last valid candidate rather than omitting the cell.

## Gates

**S0 (zero paid calls)**: exact source SHA/licensing/model capability review;
code-import, CLI help and short reproducible *offline* framework smoke;
real editable task interface and hidden-test separation; all third-party
code sandboxed. At least Agent-Only, AIDE and one AIRA policy fully connected
before a meaningful S1. Never call paid models merely to pass S0.

**S1 (developer screening)**: about three *nontrivial* real tasks, 4 candidate
arms, one isolated reset per arm/task, with a small explicit cost cap.
No significance claim; choose which frameworks work and warrant S2.
Require an externally checked model/backbone parity report for matched runs.

**S2 (model and mechanism selection)**: approximately six independent
development tasks x two restarts, original strong agents compared fairly.
Add cost-only and memory-only controls. Freeze the winning base and its
version **after** the development-only comparison.

**S3 (EpiPilot incrementality)**: winner B0, +verified memory B1,
+problem model B2, +adaptive research controller B3; each module paired
with a matched extra-computation control and separate ablation. Do not copy
all existing EpiPilot modules indiscriminately.

**S4 (confirmation)**: independent tasks from 3+ families, power-planned
sample size (8–12 tasks is a planning floor, not a proof of statistical
power), fresh tasks and blind evaluation; task-cluster CI on paired
differences with no reuse of development hidden-test feedback.

## Security and cost boundaries

- The public EpiPilot repository must never hold API secrets, test labels,
  private runner credentials or raw private trajectories.
- If using private Remote-Control Actions, separate the untrusted
  candidate-execution job from trusted private scoring, and never make
  private data or keys accessible to candidate code.
- Use fixed source commits and inspect dependencies before installation.
  Runtime flags alone do not prove a provider supplied the same model.
- A process timeout, job lock and sampled whole-machine RAM guard are
  useful operational protections but cannot enforce token or API spending
  inside an arbitrary external CLI. Require a separately audited request
  proxy/adapter metering and per-cell budget ledger before paid S1.
- Whole-machine Mac RAM should stay within the previously approved 95%
  ceiling. If used on a server, define independent CPU/GPU/VRAM caps and a
  global job schedule with no resource contention across arms.
- Record every model attempt including failures, tokens if available, start
  / finish timestamps, dev training, retries and no-ops. No dollar savings
  claim when provider billing is missing.
- Framework-level architecture rankings are **not** supported by comparing
  unrelated paper headline scores.

## Acceptance checklist

S0 PASS only if all selected adapters run controlled offline test artifacts
with pinned source, reproducible task input, isolated worktree, transparent
failure modes, no hidden test read, licensing cleared and model availability
documented. S1 MUST NOT start merely because the schema preflight passed.

The server agent's actionable implementation checklist and required output
artifacts are in [SERVER_CODING_AGENT_BENCHMARK_FIRST.md](SERVER_CODING_AGENT_BENCHMARK_FIRST.md).
