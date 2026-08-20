# EpiPilot Architecture

## Purpose

EpiPilot is an orchestration layer above coding agents. Its job is not merely to delegate work but to maintain a durable, auditable model of project intent, uncertainty, evidence, execution, and verification over long horizons.

## Control loop

```text
Goal
  -> Requirements
  -> Unknowns / Hypotheses
  -> Plan
  -> Schedule
  -> Compile Context
  -> Execute
  -> Observe
  -> Verify
  -> Evidence
  -> Belief Update
  -> Replan
```

## Four planes

### Interface plane

CLI, API, and future UI surfaces for goals, decisions, progress, task graph, hypotheses, evidence, and executor sessions.

### Control plane

Owns orchestration policy:

- Requirement Manager
- Decision Frontier
- Epistemic Manager
- Planner / Experiment Designer
- Scheduler
- Context Compiler
- Supervisor
- Verifier
- Failure Manager
- Project Acceptance

### Execution plane

Contains replaceable coding-agent adapters and isolated workspaces. Executors may run code, edit files, and report observations, but do not own canonical project truth.

### State plane

Durable project data:

- Requirement Ledger
- Decision Ledger
- Unknown Registry
- Epistemic Graph
- Versioned Task Graph
- Procedure/Skill Store
- Structural Code Index
- Episodic Memory
- Immutable Event Store
- Artifact Metadata

## Dependency rule

The domain layer must remain independent of infrastructure.

```text
core/domain
  ^
requirements  epistemics  planning  verification
  ^              ^           ^
context       scheduler    supervisor
  ^              ^           ^
executors/adapters   runtime/application
  ^
api/cli/ui
```

Concrete executor SDKs, database clients, subprocess handling, web frameworks, and LLM provider SDKs belong outside `core`.

## Canonical state versus context

Canonical state is durable and auditable. Executor context is a bounded projection of canonical state compiled for one task.

A context bundle may include:

- project contract;
- task contract;
- relevant verified facts;
- active hypotheses;
- selected evidence;
- relevant procedures;
- structural code map;
- selected episodic lessons.

Conversation transcripts are archival inputs, not canonical state.

## Epistemic model

These types are intentionally distinct:

- **Observation**: raw report or measurement.
- **Evidence**: validated observation with provenance.
- **Hypothesis**: falsifiable proposition.
- **Fact**: canonical proposition promoted according to evidence policy.
- **Unknown**: unresolved, decision-relevant question.
- **Decision**: choice with authority, rationale, and dependencies.

No executor can directly promote an observation to a fact.

## Task model

EpiPilot uses a versioned AND/OR task graph rather than an unstructured todo list.

Task node kinds include:

- action;
- experiment;
- analysis;
- verification;
- user decision;
- merge;
- checkpoint;
- deliverable.

Important terminal/non-running states include `PASSED`, `FAILED`, `SUPERSEDED`, `CANCELLED`, and `INVALIDATED`.

`SUPERSEDED` means that changing knowledge or requirements made a task no longer worth executing; it is not execution failure.

## Verification boundary

Executor completion reports are not authoritative. The normal flow is:

```text
RUNNING
  -> AGENT_REPORTED_DONE
  -> VERIFYING
  -> PASSED | FAILED
```

Only verification policy may produce `PASSED`.

Verification should prefer deterministic checks over semantic judgment:

1. deterministic tests and schema checks;
2. reproducible runtime measurements;
3. artifact inspection;
4. independent semantic verifier;
5. executor self-report (observation only).

## Events and replay

Important state changes emit immutable typed events. Durable state should be reconstructable by replaying events and referenced immutable artifacts/evidence.

Examples:

- `RequirementAdded`
- `DecisionMade`
- `UnknownRegistered`
- `HypothesisCreated`
- `EvidenceRecorded`
- `TaskCreated`
- `TaskStarted`
- `ExecutorObservationRecorded`
- `VerificationPassed`
- `TaskSuperseded`
- `PlanVersionCreated`

Derived views may be rebuilt. Audit history must not be rewritten silently.

## Failure policy

Failures are classified before retrying:

- implementation error;
- missing context;
- wrong assumption;
- environment/infrastructure failure;
- invalid task contract;
- invalid approach.

The same failure signature must not be retried indefinitely. A repeated attempt requires new evidence, additional context, or a changed strategy.

## Parallelism

Parallelism is scheduler-owned, not free-form agent collaboration.

Each task declares resource requirements such as repository write scope, worktree, GPU, port, dataset, and external API budget. Parallel execution is allowed only when lock/resource contracts do not conflict.

Separate coding-agent sessions must not edit the same mutable working tree concurrently.

## Initial implementation strategy

The first implementation is intentionally a Python modular monolith with:

- Python 3.11+;
- typed domain models;
- PostgreSQL-ready persistence interfaces;
- Git/worktree-backed source isolation;
- async application runtime;
- pluggable executor adapters;
- deterministic tests for invariants.

Distributed queues, graph databases, Kubernetes, and dedicated vector stores are deferred until concrete scaling requirements justify them.
