# EpiPilot

**Understand the project. Identify what matters. Advance toward verified results.**

EpiPilot is an evidence-driven epistemic orchestration framework for long-horizon coding agents. Its product goal is to help you understand a project clearly, complete the current goal efficiently, and distinguish worthwhile improvements from speculative work.

EpiPilot sits above coding agents such as Pi, Codex, DeepSeek Harness (DSH), and other executors. It manages project requirements, unknowns, hypotheses, evidence, dynamic task graphs, context compilation, verification, and replanning so that long-running work remains auditable and evidence-driven.

## Project workbench UI

A local, project-first Web UI is available with five views: **总览 / 项目认知 / 目标推进 / 优化机会 / 成果与证据**.

```bash
python -m pip install -e '.[web]'
python -m epipilot.web --repo /path/to/project
```

Open `http://127.0.0.1:8765`. The repository must have a Git commit. No Node build, CDN, API key, or LLM call is needed to inspect committed documents and source structure.

To also display canonical project goals, tasks, unknowns, hypotheses, and evidence, connect an existing EpiPilot SQLite event stream:

```bash
python -m epipilot.web \
  --repo /path/to/project \
  --events-db /path/to/project-events.sqlite \
  --project-id your-exact-aggregate-id
```

The UI reads real data. Missing events remain missing; task verification never substitutes for project acceptance. Sources carry revision/scope information, and stale or unlinked evidence is explicitly qualified.

This first UI slice is **read-only**. It supports source inspection, keyword search, evidence details, Markdown reports, and proposal-only task handoffs with a selectable Codex/Pi/DSH preference. It does not start agents, approve execution, modify repositories, or migrate sessions. It is a local single-user tool, not a publicly deployable authenticated service.

See [`docs/UI.md`](docs/UI.md) for the Chinese user guide, security boundaries, testing, and design references.

## Core loop

```text
Goal -> Requirements -> Unknowns/Hypotheses -> Plan -> Execute
     -> Observe -> Verify -> Evidence -> Belief Update -> Replan
```

## Core invariants

1. No completion without evidence.
2. Executor self-report alone can never create a canonical fact.
3. Every structural plan mutation must trace to a requirement, decision, or evidence item.
4. Canonical project state is separate from long-term memory and from the LLM context compiled for a task.
5. The same failure may not be retried without new information or a changed strategy.

## Architecture

EpiPilot is organized around four planes:

- **Interface plane** — project-centered Web UI and future execution-control CLI/API surfaces.
- **Control plane** — requirements, epistemics, planning, scheduling, context compilation, supervision, verification, and recovery.
- **Execution plane** — replaceable coding-agent adapters running in isolated workspaces.
- **State plane** — requirements, decisions, unknowns, hypotheses, evidence, task graph, memory, events, and artifacts.

Architecture and planning documents:

- [`docs/FRAMEWORK.md`](docs/FRAMEWORK.md) — overall EpiPilot methodology, system responsibilities, epistemic loop, task planning, execution, verification, memory, recovery, replanning, and non-negotiable invariants.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — detailed staged plan from the current V0 foundation to V1.0, including milestone scope, gates, tests, and acceptance criteria.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — module boundaries, state ownership, runtime flow, and invariants.
- [`docs/MEMORY.md`](docs/MEMORY.md) — canonical state vs. memory vs. working context, memory classes, scope, consolidation, and retrieval rules.
- [`docs/UI.md`](docs/UI.md) — runnable workbench, read-model semantics, capability limits, and browser testing.

## Coding-agent selection

Project logic depends only on the stable `CodingAgentExecutor` protocol. Built-in executor names are currently `pi`, `codex`, and `dsh`, and deployments select one through runtime configuration rather than changing project-domain code.

```python
from pathlib import Path

from epipilot.executors.registry import ExecutorConfig, create_executor

workspace = Path("/path/to/project-worktree")
executor = create_executor(ExecutorConfig(backend="codex", cwd=workspace))
```

Changing the backend is intentionally small:

```python
pi_executor = create_executor(ExecutorConfig(backend="pi", cwd=workspace))
dsh_executor = create_executor(ExecutorConfig(backend="dsh", cwd=workspace))
```

The selected executable must already be installed and configured in the runtime environment. The built-in defaults are:

```text
pi    -> pi --mode rpc --no-session
codex -> codex exec --json --ephemeral
dsh   -> dsh --profile headless --json
```

`ExecutorConfig.command` can override these commands for custom installations or wrappers. Executor completion remains non-authoritative: Pi `agent_end`, Codex `turn.completed`, and a successful DSH `final` only become `REPORTED_DONE`; the independent EpiPilot verifier decides whether a task is actually `PASSED`.

The registry is extensible, so future coding agents can be added without modifying project state, epistemics, planning, or verification logic.

## Repository standards

Before contributing, read:

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution workflow, Python standards, testing rules, and architecture boundaries.
- [`AGENTS.md`](AGENTS.md) — non-negotiable instructions for coding agents working in this repository.
- [`SECURITY.md`](SECURITY.md) — security and sensitive-data requirements.

The repository enforces formatting, linting, type checking, and tests through CI.

## Development

EpiPilot currently targets Python 3.11+.

```bash
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
mypy src
pytest
```

For automatic local checks:

```bash
pre-commit install
pre-commit run --all-files
```

Browser integration tests are separate from the default test run:

```bash
python -m pip install -e '.[dev,web,browser]'
python -m playwright install chromium
EPIPILOT_BROWSER_TESTS=1 pytest tests/test_web_browser.py -q
```

## V0 foundation

The current V0 foundation intentionally starts with contracts that are difficult to retrofit safely later:

- canonical `ProjectContract`, requirements, decisions, and a deterministic user/system Decision Frontier;
- typed task states and explicit legal transitions;
- explicit observations, unknowns, falsifiable hypotheses, evidence, temporal facts, and provenance;
- append-only Event Store semantics with optimistic concurrency plus durable local SQLite persistence;
- immutable, versioned, traceable task DAG topology separated from runtime task-state projections;
- information-gain-aware scheduling with explicit impact, unblocking, urgency, cost, and risk inputs;
- bounded `TaskContract` models for repository revision, read/write/forbidden paths, expected outputs, resources, and independent acceptance commands;
- scoped typed long-term memory with canonical references, episodic lessons, trigger-based procedures, and revision-pinned structural memory;
- a Context Compiler that never silently drops mandatory authoritative state;
- an evidence-gated verification pipeline and independent argv-based command verifier;
- a replaceable coding-agent executor protocol plus configuration-driven registry;
- built-in Pi, Codex, and DSH executor adapters with command overrides;
- Pi `agent_end`, Codex `turn.completed`, and successful DSH headless completion mapped only to `AGENT_REPORTED_DONE`, never directly to `PASSED`;
- interactive Pi confirmation/input requests surfaced as `BLOCKED` rather than auto-approved;
- a single-task runtime from `READY` through independent verification with guaranteed executor cleanup;
- a sequential project-level DAG runner that unlocks successors only after verified predecessor completion and can continue independent branches;
- failure-signature-aware supervision that forbids unchanged blind retries and escalates repeated failures;
- a read-only project cognition workbench backed by committed Git metadata and optional canonical event replay;
- regression tests for executor self-certification, graph cycles, stale event writers, memory scope leakage, context truncation, verification bypasses, executor control flow, retry loops, task-scope violations, and DAG execution semantics.

## Next V0 milestones

The read-only UI provides early visibility without bypassing the remaining control-plane work:

1. checkpoint/resume on top of typed event replay;
2. artifact metadata/store contracts and stronger revision-aware repository understanding;
3. runtime enforcement of `TaskContract` path/resource boundaries;
4. wiring `ProjectContract.execution_ready`, Decision Frontier interrupts, and retry policy into the project runtime;
5. Git worktree isolation and resource locks as prerequisites for parallel execution;
6. PostgreSQL Event Store adapter for multi-process/server deployment;
7. version-checked, authorized command services before browser execution/approval controls;
8. evidence-grounded natural-language project explanations and optimization analysis.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the longer-term milestone plan; this read-only UI slice does not claim completion of its execution-control or autonomous research milestones.

## Project status

EpiPilot is in early V0 development. The project workbench is a usable local read-only surface; fully autonomous goal completion and browser-based execution control remain under development.

## License

EpiPilot is licensed under the [Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE) for attribution information.
