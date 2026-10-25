# ResearchBench-T1: 95%-only whole-Mac memory guard and locked model

## User-approved execution settings

For the experimental macOS self-hosted runner, **only global physical memory
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

Every T1 Codex command MUST specify exactly `--model 'gpt-6-luna(max)'`
in its argv. The T1 CLI rejects `default`, `gpt-6`, any other name, and
omission is resolved only to the locked model. Remote-Control's request
validator also requires that exact model identifier. If the Codex version
or configured provider does not support this identifier, the experiment
fails closed: **no fallback or substitution**.

The literal model identifier has not been verified against a published
Codex model catalog; acceptance must be confirmed using an actual pinned
Mac run. CC Switch or any upstream gateway could remap names without
revealing the true backend. A CLI exit code alone therefore establishes
only that the requested model name was accepted by the local Codex path,
not independent proof of the underlying model identity.

## Cost and research-validity boundaries

A paid step permits at most ONE Codex CLI process, with its own bounded
wall timeout and no automatic retry; provider-side API request count and
billing remain unobservable. The workflow runs on the single private
`epipilot-t1` Mac runner and exports sanitized metrics only. It does not
expose the final holdout. The four-arm experiment remains incomplete; any
results from previously executed Codex `default` runs are
**not comparable as same-model results** to the locked-model protocol.

No full 36-cell study is launched automatically. A failure in authentication,
model availability, memory budget, result schema, or execution is a stop
condition, not permission to downgrade the model.
