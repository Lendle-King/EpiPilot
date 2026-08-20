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
3. Every plan mutation must trace to a requirement, decision, or evidence item.
4. Canonical project state is separate from the LLM context compiled for a task.
5. The same failure may not be retried without new information or a changed strategy.

## Architecture

EpiPilot is organized around four planes:

- **Interface plane** — CLI/API/UI surfaces for goals, decisions, progress, graphs, and agent sessions.
- **Control plane** — requirements, epistemics, planning, scheduling, context compilation, supervision, verification, and recovery.
- **Execution plane** — replaceable coding-agent adapters running in isolated workspaces.
- **State plane** — requirements, decisions, unknowns, hypotheses, evidence, task graph, memory, events, and artifacts.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the architecture contract.

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

- typed task states and legal transitions;
- explicit evidence and provenance;
- separation of observations, hypotheses, facts, and unknowns;
- an append-only event-store contract with optimistic concurrency;
- a deterministic user/system Decision Frontier;
- immutable, versioned, traceable task DAGs;
- a Context Compiler that never silently drops mandatory state;
- an evidence-gated verification pipeline;
- a replaceable coding-agent executor protocol;
- a minimal single-task runtime from `READY` through independent verification;
- regression tests for executor self-certification, graph cycles, stale event writers, context truncation, and verification bypasses.

The next V0 milestones are persistent PostgreSQL event storage, project-state reduction/checkpointing, a concrete Pi adapter, failure-signature supervision, and a project-level scheduler loop over runnable DAG nodes.

## Project status

EpiPilot is in early V0 development. Architecture contracts and quality gates are intentionally being stabilized before adding UI or multi-agent parallelism.

## License

EpiPilot is licensed under the [Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE) for attribution information.
