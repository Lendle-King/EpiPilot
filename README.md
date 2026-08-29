# EpiPilot

**EpiPilot** is an evidence-driven epistemic orchestration framework for long-horizon coding agents.

EpiPilot sits above coding agents such as Pi, Codex, Claude Code, and other executors. It manages project requirements, unknowns, hypotheses, evidence, dynamic task graphs, context compilation, verification, and replanning so that long-running work remains auditable and evidence-driven.

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

- **Interface plane** — CLI/API/UI surfaces for goals, decisions, progress, graphs, and agent sessions.
- **Control plane** — requirements, epistemics, planning, scheduling, context compilation, supervision, verification, and recovery.
- **Execution plane** — replaceable coding-agent adapters running in isolated workspaces.
- **State plane** — requirements, decisions, unknowns, hypotheses, evidence, task graph, memory, events, and artifacts.

Architecture and planning documents:

- [`docs/FRAMEWORK.md`](docs/FRAMEWORK.md) — overall EpiPilot methodology and system responsibilities.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — staged plan from the current V0 foundation to V1.0.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — module boundaries, state ownership, runtime flow, and invariants.
- [`docs/MEMORY.md`](docs/MEMORY.md) — canonical state vs. memory vs. working context.
- [`docs/CODEX_PLUGIN.md`](docs/CODEX_PLUGIN.md) — Codex plugin installation, MCP tools, research loop, and limitations.

## Codex plugin

The `feat/codex-epistemic-research-plugin` branch contains an initial usable Codex Agent Plugin. Codex becomes the interactive research frontend while EpiPilot owns durable canonical state.

The plugin supports:

```text
ProjectContract
  -> Unknown
  -> falsifiable Hypotheses
  -> preregistered Experiment
  -> RUN_EXPERIMENT
  -> independently verified Evidence
  -> Experiment conclusion / Belief update
  -> Unknown resolution
  -> next research directive
```

One-command development-branch install:

```bash
uvx --from "git+https://github.com/Lendle-King/EpiPilot.git@feat/codex-epistemic-research-plugin" \
  epipilot-install-codex --ref feat/codex-epistemic-research-plugin
```

The bootstrap persistently installs the Python runtime, adds/upgrades the EpiPilot Git marketplace, installs the Codex plugin, and verifies through Codex JSON output that the plugin is installed and enabled. Then start a new Codex thread.

See [`docs/CODEX_PLUGIN.md`](docs/CODEX_PLUGIN.md) for the manual fallback, update instructions, verification workflow, and troubleshooting.

The plugin deliberately does not let Codex self-report become verified evidence, does not expose arbitrary command execution through MCP, and does not turn research-frontier exhaustion into automatic project acceptance.

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
- a replaceable coding-agent executor protocol;
- a concrete headless Pi JSONL RPC executor using `pi --mode rpc`;
- Pi `agent_end` mapped only to `AGENT_REPORTED_DONE`, never directly to `PASSED`;
- interactive Pi confirmation/input requests surfaced as `BLOCKED` rather than auto-approved;
- a single-task runtime from `READY` through independent verification with guaranteed executor cleanup;
- a sequential project-level DAG runner that unlocks successors only after verified predecessor completion and can continue independent branches;
- failure-signature-aware supervision that forbids unchanged blind retries and escalates repeated failures;
- regression tests for executor self-certification, graph cycles, stale event writers, memory scope leakage, context truncation, verification bypasses, Pi RPC control flow, retry loops, task-scope violations, and DAG execution semantics.

## Next V0 milestones

The broader framework still needs:

1. checkpoint/resume and external-state reconciliation;
2. artifact metadata/store contracts and revision-aware repository indexing;
3. runtime enforcement of `TaskContract` path/resource boundaries;
4. evidence-driven `LOCAL_PATCH / SUBGRAPH_REPLAN / GLOBAL_REPLAN` integration;
5. project-level acceptance wiring;
6. Git worktree isolation and resource locks for safe parallel execution;
7. PostgreSQL Event Store for multi-process/server deployment;
8. stable CLI/API/UI surfaces after the control/state contracts mature.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the full milestone plan through V1.0.

## Project status

EpiPilot remains early V0 research software. The Codex plugin is intentionally described as initially usable rather than production-ready: its durable research loop is functional, while broader acceptance, artifact, worktree, and distributed-runtime guarantees remain under development.

## License

EpiPilot is licensed under the [Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE) for attribution information.
