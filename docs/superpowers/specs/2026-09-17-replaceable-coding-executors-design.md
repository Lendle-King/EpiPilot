# Replaceable Coding Executors Design

## Goal

Make the coding-agent backend a first-class replaceable dependency of EpiPilot. The project control/state/epistemic layers must be able to select Pi, Codex, DeepSeek Harness (DSH), or a future executor without changing project logic.

## Design principle

**Own the cognition; reuse the execution.**

EpiPilot owns project understanding, requirements, hypotheses, experiments, evidence, planning, verification, retry/recovery, and canonical state. Coding agents are non-authoritative execution backends. An executor may edit files and report observations, but its self-report never creates a canonical project fact or marks a task `PASSED` without independent verification.

## Scope

This slice adds:

1. a configurable executor selection contract;
2. a registry/factory that maps backend names to adapters;
3. built-in backends named `pi`, `codex`, and `dsh`;
4. a Codex headless adapter using `codex exec --json --ephemeral`;
5. a DSH headless adapter using `dsh --profile headless --json`;
6. preservation of the existing Pi JSONL RPC adapter;
7. command overrides for tests and non-standard installations;
8. documentation that backend choice is runtime configuration, not project-domain logic.

This slice does **not** add multi-agent parallel scheduling, a user-facing CLI, credential management, model routing, or executor-specific canonical evidence.

## Stable executor port

`CodingAgentExecutor` remains the control-plane port:

```python
class CodingAgentExecutor(Protocol):
    async def start_task(self, task: Task, context: str) -> str: ...
    async def inspect(self, session_id: str) -> ExecutorObservation: ...
    async def interrupt(self, session_id: str, reason: str) -> None: ...
    async def terminate(self, session_id: str) -> None: ...
```

The project runtime depends only on this protocol.

## Selection model

Add a small immutable configuration object:

```python
@dataclass(frozen=True, slots=True)
class ExecutorConfig:
    backend: str
    cwd: Path | None = None
    command: tuple[str, ...] | None = None
    shutdown_timeout_seconds: float = 5.0
```

`backend` is a string rather than a closed enum so third-party adapters can register names without modifying EpiPilot core.

`ExecutorRegistry` owns factories:

```python
ExecutorFactory = Callable[[ExecutorConfig], CodingAgentExecutor]

class ExecutorRegistry:
    def register(self, name: str, factory: ExecutorFactory, *, replace: bool = False) -> None: ...
    def create(self, config: ExecutorConfig) -> CodingAgentExecutor: ...
    def names(self) -> tuple[str, ...]: ...
```

`default_executor_registry()` returns a fresh registry containing `pi`, `codex`, and `dsh`. `create_executor(config)` is the convenience entrypoint using that default registry.

## Backend contracts

### Pi

Keep `PiRpcExecutor` as the built-in `pi` backend. The default command remains:

```text
pi --mode rpc --no-session
```

Pi `agent_end` maps only to `ExecutorState.REPORTED_DONE`. Interactive extension requests remain `BLOCKED` rather than being auto-approved.

### Codex

Use the official non-interactive JSONL surface:

```text
codex exec --json --ephemeral <task>
```

The adapter appends a single compiled task/context prompt as the positional task and runs in the configured workspace. It parses top-level JSONL events. `turn.completed` maps to `REPORTED_DONE`; `turn.failed`, top-level `error`, malformed JSONL, unexpected stdout closure, or a non-zero process exit before a terminal success map to `FAILED`.

The adapter does not trust Codex's completion claim as verification; EpiPilot's verifier still decides whether the task passes.

### DSH

Use DSH's official headless machine-readable mode:

```text
dsh --profile headless --json <task>
```

The adapter parses the JSONL stream. A `final` event is remembered but is not sufficient by itself; the subprocess must also exit with status 0 before the executor reports `REPORTED_DONE`. A top-level `error`, malformed JSONL, missing `final`, or non-zero exit maps to `FAILED`.

DSH remains an execution runtime, not the owner of EpiPilot's hypothesis/evidence/project state.

## Shared subprocess behavior

Codex and DSH share one internal one-shot JSONL subprocess lifecycle helper. It owns:

- process startup without a shell;
- stdout JSONL parsing;
- stderr draining to avoid backpressure;
- process/session lifecycle;
- bounded graceful shutdown followed by kill;
- explicit interrupt/terminate cleanup;
- no promotion of raw stderr or model text into canonical EpiPilot state.

Backend subclasses own only argv construction and event interpretation.

## Security and isolation

Executor adapters must accept a caller-supplied working directory. They do not provide a security boundary. EpiPilot should continue toward isolated Git worktrees/containers as described in the roadmap. DSH/Codex/Pi permissions are executor-local controls, not substitutes for EpiPilot workspace isolation.

## Error semantics

- Unknown backend name: `ValueError` listing available built-ins.
- Empty backend/command/context: `ValueError` before process creation.
- Missing executable: allow `FileNotFoundError` to surface to the caller; do not silently fall back to another executor.
- Executor terminal self-report: `REPORTED_DONE`, never `PASSED`.
- User-owned interaction that an adapter can detect: `BLOCKED`.
- Malformed machine output: `FAILED` with a stable adapter-specific summary.

## Tests

Unit tests use small Python subprocess fixtures rather than real external agents. Tests must cover:

- registry selection for `pi`, `codex`, and `dsh`;
- custom backend registration and duplicate protection;
- command override propagation;
- Codex `turn.completed` => `REPORTED_DONE`;
- Codex `turn.failed`/malformed stream => `FAILED`;
- DSH `final` + exit 0 => `REPORTED_DONE`;
- DSH `final` + exit 1 and missing `final` => `FAILED`;
- cleanup/termination does not leak subprocesses;
- existing Pi executor tests remain unchanged and passing.

## Compatibility

The existing `TaskRuntime(executor=...)` dependency-injection API does not change. Existing callers may continue constructing `PiRpcExecutor` directly. The registry is additive and becomes the preferred construction path for configuration-driven deployments.
