# ResearchBench-T1: 95%-only whole-Mac memory guard and locked model

## User-approved execution settings

For the experimental macOS runner, **only global physical memory
usage is a resource hard limit**. The limit is **95% used**; on a 24-GiB Mac,
the agent must leave at least **1.2 GiB available** at sampling points.

We have removed all of the former resource thresholds for:
- Per-process or subprocess-tree RSS (no 1-GiB/1.5-GiB limits).
- Expected peak RSS + extra 0.5-GiB free-memory reserve.
- CPU utilization (no 80%-launch or 90%-sustained refusal).
- Child process count.

We retain a single-job lock, fixed task timeouts, and the benchmark's
frozen trial protocol so two research jobs cannot overlap and model usage
cannot increase without a new explicit request. Low CPU scheduling priority
and deterministic single-threaded ML trial settings are not independent
resource-usage refusal thresholds.

## Whole-machine memory-only monitor

Before launching each subprocess, `psutil.virtual_memory()` is checked:
`available_bytes / total_bytes >= 0.05`. During execution the monitor
samples the same whole-machine metric every 0.35 seconds. If total memory
usage rises above 95% while the child process group is running, the group
is terminated. The sanitized receipt reports maximum sampled whole-Mac
used percentage and minimum sampled free GiB. No process RSS is polled.

This monitor is **best-effort and sampled**. Short overshoots may happen
between samples, and `available` is an operating-system estimate rather
than a kernel-enforced private memory quota. Do not rely on it to guarantee
that swap pressure can never occur.

## Strict Codex model selection

Every T1 Codex command MUST specify the exact model slug `gpt-6-luna` and
the independent setting `-c 'model_reasoning_effort="max"'`. The pilot
runner also uses `--no-daemon` and `--ephemeral`. The literal
`gpt-6-luna(max)` is not a model slug. The current Remote-Control request
validator still needs review before that workflow is reused. If the local
Codex version or configured provider does not support the model/effort pair,
the experiment fails closed: **no fallback or substitution**.

One local minimal CLI invocation using the corrected model slug and maximum
reasoning setting returned `OK` on 2026-10-27. This verifies that the local
CLI path accepted that request; CC Switch or another upstream gateway could
remap model names, so it is not independent proof of the underlying backend.
Provider request count and billing remain unobservable from this CLI receipt.

## Cost and research-validity boundaries

The four-policy Pilot permits at most four serial Codex CLI attempts per cell
and 32 across eight cells. Every attempt has a 150-second wall timeout, is
accounted before launch, and is not automatically retried. A nonzero exit,
invalid response, tool event, timeout, or resource refusal remains in the
ledger. Usage data from Codex JSONL is recorded when present; provider request
count and billing remain unobservable.

The Pilot invocation disables the shell feature, web search and MCP servers,
uses a read-only sandbox and ephemeral session, and runs in a fresh empty
directory. It sends only a structured task, candidate schema, current-cell
development summaries, and verified Memory Only notes. It does not send
holdout rows or metrics. The trusted local controller uses the original
stratified split and delays holdout materialization and evaluation until all
eight cells have submitted and all calls have closed. This is a controlled
same-user workflow; it is not a separate OS account or a cryptographic
boundary against arbitrary malicious code running with the user's identity.

The whole-Mac guard samples physical memory before and during each subprocess
and refuses or terminates work above 95% used memory. A single-run `flock`
prevents overlapping cooperating T1 controllers. It does not establish that
no unrelated process is active on the Mac. Full T1 remains paused; prior
default-model results are not comparable to this locked-model protocol.

No full 36-cell study is launched automatically. A failure in authentication,
model availability, memory budget, result schema, or execution is a stop
condition, not permission to downgrade the model.
