# T1 Mac resource-safe staged qualification

## Intent and evidence boundary

This experimental extension protects a user's **active macOS workstation**
while evaluating EpiPilot. The limits are intentionally conservative and
triggered *before* model inference. A resource-blocked task is not a failed
scientific hypothesis and does not justify another automatic model call.
It is not a statistically valid comparison of four research policies.

## Default safety limits

| Dimension | Bounded behavior |
| --- | --- |
| Scheduling | One T1 GitHub Actions run at a time via a single concurrency group, plus an exclusive local file lock |
| Host free memory before run | At least the larger of 2 GiB or **18%** of total physical RAM |
| Prelaunch headroom | ALSO reserve the full permitted process-tree RSS (1 or 1.5 GiB) and an extra **0.5 GiB** *before* launching |
| Host CPU at launch | Refuse if measured utilization exceeds **80%** |
| Sustained CPU during run | Terminate monitored group after **4 consecutive samples above 90%** |
| T1/Python training subtree RSS | Maximum **1024 MiB** (including descendants) |
| Codex subtree RSS | Maximum **1536 MiB** (including descendants) |
| Process count | Maximum **12** in each launched subprocess tree |
| Scheduling priority | `nice -n 10` for child commands |
| Numerical thread count | `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `VECLIB_MAXIMUM_THREADS=1`, `NUMEXPR_NUM_THREADS=1`; tokenizers parallelism off |
| Model inference | Maximum one Codex CLI invocation, **150 seconds**, no retry/fallback |
| CPU development evaluation | Frozen pilot T1 trial timeout **15 seconds**, at most 2 candidates per seed and no GPU |
| Holdout | **Never** revealed or evaluated by the bounded Agent-runner mode |
| Extra software | `psutil` optional `[t1]` dependency installed only in disposable `uv` environment |
| Task evidence | Upload sanitized summary; never upload raw Codex output, auth state, private vault, or arbitrary system logs |

**Important:** psutil uses sampled RSS, not kernel memory cgroups. A
subprocess can briefly exceed the cap between samples; OS-level global memory
pressure and other users' processes are external to this monitor. macOS does
not provide the same reliable RLIMIT_AS behavior as Linux cgroups. For
high-stakes workloads use an isolated VM with enforced memory/CPU quotas.
Monitor other non-EpiPilot jobs separately; this controller cannot prevent
other tasks from saturating the Mac.

For example, the 24-GiB Mac previously reported approximately 5.84 GiB
available while the system safety reserve was 4.32 GiB. A 1.5-GiB Codex
subprocess plus 0.5-GiB uncertainty requires **6.32 GiB** free at launch;
the orchestrator must refuse the paid step at that pressure level. The
resource_probe reports both its baseline safety status and whether a
worst-case model/worker launch can fit.

A research step blocked by CPU, memory, missing psutil, unknown process,
invalid telemetry, or time limit **must not be automatically rerun**. Keep
the failure reason and cost separately; inspect the Mac resource pressure
before scheduling a new, explicitly bounded step.

## Small incremental strategy trial

First dispatch **resource_probe**: no model request, no ML training. Only if
headroom passes, dispatch at most **one** agent-only pilot seed `137`.
Existing prior agent-only seed 42 and this additional seed are **not** a
completed comparison: former sanitized artifacts omitted raw development
metrics, there is no blind final test, and actual provider-request count/
billing is not observable from the Codex CLI process.

For meaningful comparisons, keep T1 work state and history isolated by
arm, implement genuine policy compliance for memory-only and two EpiPilot
routing arms (the existing Mac runner supports agent_only qualification
only), record all failures, then run the sealed final evaluator under a
different account/security boundary. Advance to that stage only after
the resource-safe one-cell qualification is independently reviewed.
