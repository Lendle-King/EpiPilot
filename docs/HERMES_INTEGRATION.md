# Hermes frontend integration

EpiPilot can be loaded as a native Hermes plugin so Hermes provides the human-facing
conversation surface while EpiPilot retains canonical project state and control authority.
The same integration now also exposes a bounded `HermesExecutor` adapter for EpiPilot-owned
coding attempts.

## Authority boundary

```text
Human
  |
  v
Hermes CLI / Gateway                 (interface only)
  |
  |  /epipilot commands + bounded context projection
  v
EpiPilot Hermes frontend adapter
  |
  +--> profile-scoped pointer state (non-canonical)
  |
  v
EpiPilot EventStore -> Replay -> ProjectState
  |
  v
ProjectRuntime -> TaskRuntime
  |
  v
HermesExecutor child                 (non-authoritative editor)
  |
  v
ExecutorObservation -> VerifierPipeline -> PASSED | FAILED
```

Hermes does not become the owner of requirements, evidence, task state, verification, or
completion. Conversation history remains non-canonical. Explicit project-intake commands create
typed EpiPilot events with user provenance. A Hermes final response is an executor observation,
never completion evidence by itself.

## Install into Hermes

Install EpiPilot into the same Python environment that runs Hermes:

```bash
python -m pip install -e .
hermes plugins list
```

EpiPilot publishes the `hermes_agent.plugins` entry point named `epipilot`. Restart Hermes
after installing or upgrading the package, then use:

```text
/epipilot start Implement the repository feature
/epipilot success pytest passes
/epipilot constrain do not modify generated files
/epipilot status
/epipilot exit
/epipilot resume <project-id>
```

`start`, `success`, and `constrain` are explicit operator actions and therefore may create
canonical `RequirementAdded` events. Ordinary chat messages do not silently mutate canonical
project state.

## Persistence

The Hermes state bridge stores only frontend bookkeeping such as the active and last project
IDs. Canonical events are stored separately in:

```text
<HERMES_HOME>/plugin-data/<epipilot-plugin-namespace>/epipilot-events.sqlite3
```

The database uses EpiPilot's `SqliteEventStore`. Project status is reconstructed by
`replay_project()` on every integration read rather than trusted from conversational memory or
a cached assistant summary. The plugin resolves `ctx.state.data_dir` at operation time so
Hermes profile isolation remains the host responsibility instead of being frozen at plugin
registration time.

## Frontend versus executor

The interactive Hermes session remains interface-only. While an EpiPilot project is active,
its `pre_tool_call` hook blocks ordinary frontend tool execution. Repository mutation must be
initiated by EpiPilot `TaskRuntime` through `HermesExecutor`.

`HermesExecutor` launches a fresh Hermes single-query child for one task attempt. The child uses
Hermes' file-based query transport rather than putting EpiPilot context in process arguments:

```text
hermes chat --query-file <0600-temp-file> -Q \
  --in <workspace> --ignore-rules \
  -t epipilot_executor,file --source tool --max-turns <N>
```

The prompt file is owner-only where the platform supports POSIX modes and is removed during
executor cleanup. `--ignore-rules` prevents ambient Hermes AGENTS/SOUL/memory/preloaded-skill
state from silently becoming EpiPilot executor context. The child receives only the context
compiled by EpiPilot plus the current task objective.

The `epipilot_executor` toolset contains a child-only guard sentinel. If the EpiPilot plugin is
not loaded, Hermes cannot resolve the requested toolset and the child fails instead of silently
falling back to an unguarded file surface.

## Bounded child tool policy

The current executor slice intentionally exposes only Hermes' native file tools:

- `read_file`
- `search_files`
- `write_file`
- `patch` with `mode="replace"` only

Every child tool call is checked again by EpiPilot's Hermes `pre_tool_call` policy. Paths are
resolved against the authenticated workspace, symlink escapes are rejected, `.git` metadata is
blocked, and Hermes' `cross_profile` escape is unavailable. Terminal/process execution,
network-capable tools, delegation, memory mutation, and multi-file patch payloads are blocked.

This is deliberately narrower than full Hermes coding capability. EpiPilot does not yet pass a
full `TaskContract` into the executor protocol, so command/resource authority cannot be enforced
precisely enough to expose an unrestricted shell. Deterministic test and acceptance commands
remain the responsibility of EpiPilot's independent verifier.

## Workspace attribution

By default `HermesExecutor` requires:

1. a Git working tree whose repository root is exactly the configured workspace; and
2. a clean worktree before the child starts.

That lets EpiPilot attribute post-run tracked and untracked changes to the executor attempt. The
adapter reports changed paths as an `ExecutorObservation`; the runtime currently persists only
bounded observation metadata, not raw child stdout/stderr.

## Lifecycle mapping

Hermes never returns `PASSED` directly:

```text
child running                 -> ExecutorState.RUNNING
child exit 0 + final response -> ExecutorState.REPORTED_DONE
non-zero / empty response     -> ExecutorState.FAILED
EpiPilot interrupt            -> ExecutorState.BLOCKED
```

`REPORTED_DONE` means only that Hermes stopped and supplied a final report. `TaskRuntime` then
moves through `AGENT_REPORTED_DONE -> VERIFYING`, and only `VerifierPipeline` may produce
`PASSED`.

Raw child stderr is intentionally not copied into an executor observation because provider
errors and tool diagnostics may contain sensitive data. Captured process output is drained to
avoid pipe deadlock but retention is bounded.

## Remaining execution work

The next executor expansion should pass an enforceable task/workspace contract into the adapter,
including allowed read/write paths, forbidden paths, approved command classes, resource locks,
and repository revision. Only then should terminal/process execution be enabled. Git worktree
isolation should also be scheduler-owned before parallel Hermes children are allowed to edit the
same repository.

## Failure behavior

The integration fails closed when canonical project pointers/replay are malformed, when the
Hermes child guard is unavailable, when the workspace is not attributable, when a child asks for
an out-of-scope tool/path, or when post-run workspace inspection cannot be completed. `/epipilot
exit` changes only the frontend pointer; it never deletes the canonical project stream.
