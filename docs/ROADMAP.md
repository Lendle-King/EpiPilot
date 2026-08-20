# EpiPilot Roadmap

## 1. Delivery strategy

EpiPilot should advance through vertical, verifiable slices rather than by adding disconnected modules. Every milestone should extend a complete chain:

```text
Input
  -> Canonical State
  -> Policy Decision
  -> Executor / Action
  -> Evidence
  -> State Transition
  -> Persistence
  -> Replay / Recovery
```

A feature is not considered complete if it exists only as an interface or data model without being wired into this chain.

The planned progression is:

```text
Bootstrap / CI Gate
  -> Replayable Canonical State
  -> Crash-safe Resume
  -> Artifact + Repository Grounding
  -> Enforced Task Contracts
  -> Supervised Coding-Agent Execution
  -> Project DAG Runtime
  -> Epistemic Loop
  -> Evidence-driven Replanning
  -> Parallel / Async Execution
  -> CLI / API
  -> Web UI
  -> Learning / RL
```

## 2. Gate 0: establish a trusted baseline

The current bootstrap branch should first become a known-good baseline before new architectural work continues.

### Scope

- run the repository CI on Python 3.11 and 3.12;
- fix formatting, linting, type-checking, and test failures only;
- verify README, architecture, memory, and license metadata consistency;
- keep Apache-2.0 and NOTICE metadata aligned;
- avoid adding unrelated product features during this gate.

### Required checks

```text
ruff format --check .
ruff check .
mypy src
pytest
```

### Exit criteria

- Python 3.11 CI green;
- Python 3.12 CI green;
- all unit/contract tests green;
- documentation and package metadata consistent;
- branch can be squash-merged as a clean V0 bootstrap baseline.

After this gate, `main` should represent a known-good starting point and each subsequent milestone should use a focused PR.

## 3. Milestone 1: typed event payloads and deterministic project-state replay

This is the highest-priority architectural milestone after CI stabilization.

### Goal

Make the append-only event stream sufficient to reconstruct canonical project state deterministically.

### Work

Add an event payload layer such as:

```text
src/epipilot/events/
  payloads.py
  codec.py
  registry.py
```

Each important event should use a typed payload rather than ad-hoc dictionaries serialized in individual runtime methods.

Examples include:

```text
RequirementAddedPayload
DecisionMadePayload
UnknownRegisteredPayload
HypothesisCreatedPayload
EvidenceRecordedPayload
TaskCreatedPayload
TaskStatusChangedPayload
PlanVersionCreatedPayload
```

Add:

```text
src/epipilot/state/
  project.py
  reducer.py
  replay.py
  errors.py
```

Define a `ProjectState` aggregate containing the canonical projection of:

```text
ProjectContract
Requirements
Decisions
Unknowns
Hypotheses
Facts
Evidence
Plan topology
Task execution state
Agent/session metadata where authoritative
Artifact metadata
Current event version
```

### Invariants

- reducers are deterministic and side-effect free;
- reducer code performs no database, network, subprocess, filesystem, or LLM calls;
- unknown event type or schema version fails closed;
- malformed payload fails closed;
- replay preserves stable entity identities;
- replayed state must be equivalent to live state.

### Required tests

```text
test_replay_is_deterministic
test_replay_reconstructs_live_state
test_unknown_schema_version_fails_closed
test_malformed_payload_fails_closed
test_illegal_transition_fails_replay
test_duplicate_event_is_rejected
test_event_order_is_significant_or_rejected
```

### Exit criteria

A fresh process can rebuild a complete project state solely from the event stream.

## 4. Milestone 2: checkpointing and crash-safe resume

### Goal

Make long-running projects recoverable after process interruption without treating snapshots as canonical truth.

### Checkpoint model

A checkpoint should contain at least:

```text
project_id
last_event_version
serialized_project_state
schema_version
checksum
created_at
```

### Recovery flow

```text
load latest checkpoint
  -> validate schema + checksum
  -> replay events after checkpoint
  -> reconstruct ProjectState
```

If the checkpoint is invalid:

```text
discard checkpoint
  -> full event replay
```

### External-state reconciliation

Recovery must distinguish logical state from external reality. A task recorded as `RUNNING` before a crash cannot automatically remain running after restart.

The runtime should reconcile:

```text
recorded task/session state
+
actual executor/worktree/process state
-> recovery decision
```

A stale running attempt should become an explicit recovery state or recovery-required condition rather than being silently continued.

### Required tests

- restart from checkpoint plus tail events;
- corrupted checkpoint falls back to full replay;
- stale running executor is detected;
- idempotent resume does not duplicate task execution;
- recovery preserves evidence and plan provenance.

### Exit criteria

A killed EpiPilot process can restart, reconstruct its project state, identify interrupted work, and continue from a justified state without losing provenance.

## 5. Milestone 3: artifact store and evidence provenance

### Goal

Make outputs such as diffs, test logs, metrics, profiler traces, benchmark results, configs, and reports first-class auditable objects.

### Suggested modules

```text
src/epipilot/artifacts/
  models.py
  store.py
  filesystem.py
  hashing.py
```

### Artifact metadata

```text
artifact_id
kind
path / URI
sha256
size
created_at
producer task_id
producer session_id
producer event_id
repository revision
metadata
```

### Evidence integration

Evidence should reference artifacts instead of embedding large raw output in a natural-language summary.

### Exit criteria

A verifier can produce evidence that points to immutable, hashed artifacts, and replay can recover those references.

## 6. Milestone 4: revision-grounded repository structural index

### Goal

Ground context compilation and planning in the actual repository rather than stale summaries.

### Suggested modules

```text
src/epipilot/repository/
  snapshot.py
  index.py
  symbols.py
  changes.py
  git.py
```

### Initial deterministic index

Track at least:

```text
Git commit SHA
tracked files
language/file type
file size
important symbols
imports/dependencies
test-to-source relationships
changed files
```

Avoid an embedding-first architecture. Embeddings can be added later as one retrieval signal.

### Invariant

Every structural memory item is pinned to a source revision. When repository revision changes, stale structural memory must be invalidated or rebuilt.

### Exit criteria

Context and planning can retrieve repository structure with explicit revision provenance.

## 7. Milestone 5: runtime enforcement of TaskContract

### Goal

Turn task scope from documentation into an actual execution policy.

### Worktree isolation

Add a workspace layer such as:

```text
src/epipilot/workspace/
  worktree.py
  scope.py
  diff.py
```

Each task should run in an isolated worktree/branch tied to its contract and repository revision.

### Enforcement

Before execution:

- resolve allowed read/write paths;
- resolve forbidden paths;
- validate repository revision;
- allocate declared resources.

After execution:

```text
changed_files subset_of allowed_write_paths
changed_files intersect forbidden_paths = empty
```

Any unauthorized write should invalidate the attempt even if tests otherwise pass.

### Required tests

- write outside allowed scope rejected;
- forbidden-path write rejected;
- repository-revision mismatch rejected;
- isolated worktree cleanup after success/failure;
- executor cannot escape workspace by path traversal.

### Exit criteria

A coding agent can no longer violate TaskContract scope without EpiPilot detecting and rejecting the attempt.

## 8. Milestone 6: supervisor and no-blind-retry runtime integration

### Goal

Integrate failure signatures, progress signals, and retry policy into the real task runtime.

### Failure classes

Support at least:

```text
IMPLEMENTATION_FAILURE
VERIFICATION_FAILURE
MISSING_CONTEXT
TASK_CONTRACT_FAILURE
ENVIRONMENT_FAILURE
ASSUMPTION_FAILURE
```

### Progress signals

Ground progress in concrete deltas such as:

```text
new diff
new commit
new passing test
new artifact
new evidence
new diagnosis
new hypothesis
```

Do not treat natural-language claims such as "still working" as sufficient progress.

### Retry policy

The same failure signature may be retried only if there is:

```text
new evidence
OR changed strategy
OR materially changed context/environment
```

After a configured same-signature cap, escalate rather than retry indefinitely.

### Exit criteria

TaskRuntime itself enforces no-blind-retry and can distinguish implementation, context, environment, contract, and assumption failures.

## 9. Milestone 7: ProjectContract and Decision Frontier wired into project runtime

### Goal

Make user/system decision ownership a real execution gate.

### Project entry flow

```text
Project submitted
  -> evaluate ProjectContract
  -> evaluate Decision Frontier
  -> one of:
       USER_DECISION_REQUIRED
       INVESTIGATE
       EXECUTE
```

Technical uncertainty should normally produce investigation work rather than a user interrupt.

### Decision batching

When user input is required, group the currently blocking user-owned questions. Each decision request should include:

```text
question
why it matters
available choices/default
consequences
reversibility
```

### Exit criteria

The project runtime does not start unsafe autonomous execution while high-impact user-owned decisions remain unresolved, but it can proceed autonomously on system-resolvable technical unknowns.

## 10. Milestone 8: epistemic update engine

### Goal

Make evidence update hypotheses and facts through explicit policies rather than free-form LLM rewriting.

### Suggested modules

```text
src/epipilot/epistemics/
  update.py
  policy.py
  contradictions.py
  promotion.py
  experiment.py
```

### Initial state model

Use explicit states first:

```text
PROPOSED
ACTIVE
SUPPORTED
REFUTED
INCONCLUSIVE
SUPERSEDED
```

Confidence may be an auxiliary value, but transitions should remain rule-governed and auditable.

### Promotion constraints

For example, `SUPPORTED` should require independent supporting evidence and no unresolved decisive contradiction according to configured policy.

### Exit criteria

New verified evidence can deterministically change epistemic state with a recorded reason and without silently overwriting history.

## 11. Milestone 9: experiment designer and Unknown-driven investigation

### Goal

Turn unresolved high-impact unknowns into minimum discriminative experiments.

### ExperimentContract

An experiment should define before execution:

```text
objective
competing hypotheses
controlled variables
measurements
predictions
falsification criteria
decision rule
cost/resource estimate
```

### Policy

Prefer experiments with high expected information value relative to cost, especially when they can eliminate expensive implementation branches.

### Exit criteria

For a project with multiple competing technical explanations, EpiPilot can create a controlled experiment that produces evidence useful for deciding between them.

## 12. Milestone 10: evidence-driven replanning

### Goal

Close the central epistemic loop:

```text
Evidence
  -> Belief Update
  -> Task Graph Mutation
```

### Replan scopes

```text
LOCAL_PATCH
SUBGRAPH_REPLAN
GLOBAL_REPLAN
```

Use the smallest scope consistent with the evidence. Global replan should be reserved for goal infeasibility, major user constraint change, or root-assumption invalidation.

### PlanDiff

Every replan should record:

```text
from_version
to_version
basis requirements/decisions/evidence
created nodes
superseded nodes
changed dependencies
unchanged nodes
```

### Exit criteria

When a hypothesis is refuted, dependent tasks are superseded and a justified replacement subgraph is created without rewriting unrelated work.

## 13. Milestone 11: project acceptance contract

### Goal

Make project completion independently verifiable.

### Acceptance condition

A project should be accepted only when:

```text
all hard requirements verified
AND success criteria verified
AND required deliverables present
AND blocking unknowns resolved or explicitly accepted
AND required tasks resolved
AND final integration verification passed
```

### Exit criteria

Neither the planner nor executor can directly declare the overall project complete without the acceptance contract passing.

## 14. Milestone 12: resource locks and parallel DAG scheduling

### Goal

Add safe concurrency only after state, worktree, artifact, and verification contracts are stable.

### Resource model

Track resources such as:

```text
GPU
CPU
memory
port
repository write scope
dataset
service
external API budget
```

### Parallel eligibility

Two tasks may run concurrently only when:

```text
no dependency conflict
AND no incompatible write-scope conflict
AND resource locks compatible
AND workspaces isolated
```

Do not introduce uncontrolled peer-to-peer agent coordination. Parallel executors coordinate through EpiPilot canonical state.

### Exit criteria

Independent DAG branches can execute concurrently without shared-worktree races, resource conflicts, or canonical-state divergence.

## 15. Milestone 13: Context Compiler V2 and context manifests

### Goal

Make context selection auditable, revision-aware, and memory-aware.

### Additions

- trigger matching for procedural memory;
- stale memory filtering;
- structural revision checks;
- conflict resolution;
- task-contract sections;
- deterministic section ordering;
- explicit token-budget accounting.

### ContextManifest

Record for every compile:

```text
included item IDs
excluded item IDs
selection/rejection reasons
scores
token estimates
source revisions
compiler version
```

### Exit criteria

A failed executor attempt can be analyzed to determine whether the agent made a poor decision or EpiPilot failed to provide relevant context.

## 16. Milestone 14: memory consolidation

### Goal

Convert selected verified trajectories into reusable episodic and procedural memory without creating a second source of canonical truth.

### Pipeline

```text
session/events
  -> memory candidate
  -> classify
  -> validate
  -> deduplicate/conflict check
  -> commit episodic/procedural memory
```

Initially, automatic consolidation should create only episodic lessons and procedural candidates. Canonical facts remain governed by the epistemic/evidence layer.

Procedures should use explicit triggers rather than always-on injection.

### Exit criteria

Repeated high-value experiences can become reusable scoped memory without stale duplication of requirements, facts, or hypotheses.

## 17. Milestone 15: CLI

### Goal

Expose the stabilized control/state model before building a web product surface.

### Initial commands

```text
epipilot init
epipilot submit
epipilot status
epipilot decisions
epipilot graph
epipilot hypotheses
epipilot evidence
epipilot run
epipilot resume
epipilot events
epipilot why <entity-id>
```

`epipilot why` should explain provenance chains such as why a task exists, why it was superseded, or which evidence supports a decision.

### Exit criteria

A complete V1 demo can be driven without a web UI.

## 18. Milestone 16: API

### Goal

Expose canonical project state and live events through a stable service boundary.

### Suggested surfaces

```text
/projects
/projects/{id}/state
/projects/{id}/tasks
/projects/{id}/hypotheses
/projects/{id}/evidence
/projects/{id}/decisions
/projects/{id}/events
```

Use SSE/WebSocket only for live event delivery. Canonical data continues to come from the state/event system.

## 19. Milestone 17: Web UI

Build the web UI only after the control and state contracts are stable.

Initial views:

### Project Overview

```text
Goal
Success criteria
Current phase
Progress
Blocking decisions
Major unknowns
Current work
```

### Task Graph

```text
READY
RUNNING
BLOCKED
PASSED
FAILED
SUPERSEDED
```

### Knowledge / Epistemic View

```text
Facts
Unknowns
Active hypotheses
Refuted hypotheses
Evidence
```

### Agent Sessions

```text
executor
task
worktree
recent progress
verification state
resource usage
```

## 20. First full demonstration

The first convincing EpiPilot demo should contain genuine technical uncertainty. Avoid a simple greenfield application that a normal coding agent can complete directly.

A suitable example is:

```text
Goal:
Improve throughput of an existing Python inference/training service by >= 20%.

Constraints:
Do not modify the evaluator.
Do not reduce correctness.
Respect a bounded compute budget.
```

Expected autonomous flow:

```text
clarify user-owned success constraints
-> reproduce baseline
-> register major unknowns
-> create competing hypotheses
-> run profiling experiments
-> collect evidence
-> refute unsupported hypotheses
-> supersede dependent work
-> implement the supported approach
-> benchmark
-> independently verify >= 20% gain
-> run integration acceptance
-> produce auditable completion
```

This demo should explicitly show why EpiPilot is more than a long prompt or a generic multi-agent wrapper.

## 21. Test strategy

Maintain four test layers:

```text
Unit
Contract
Integration
End-to-end
```

### Unit

Reducers, state machines, epistemic policies, scheduling, memory scope, retry rules.

### Contract

Executor, EventStore, ArtifactStore, verifier, repository index, worktree, and codec boundaries.

### Integration

SQLite/event replay, Pi fake RPC, Git worktrees, checkpoint/resume, command verifier.

### End-to-end

A complete project from submitted goal through verified acceptance.

### Fault-injection matrix

Cover at least:

```text
Pi crash
EpiPilot crash
SQLite reopen
malformed event
unknown event schema
corrupted checkpoint
stale structural memory
worktree scope violation
verification timeout
duplicate event
concurrent stale writer
interactive Pi request
repeated same failure
mandatory context overflow
artifact hash mismatch
repository revision drift
```

## 22. PR definition of done

Every behavioral PR should answer:

```text
Which invariant changes?
Which states/transitions change?
Which canonical entities change?
Which events/schemas are added or modified?
Can state still replay deterministically?
What new failure modes exist?
What tests prove fail-closed behavior?
Does context behavior change?
Is backward compatibility affected?
```

No milestone is complete solely because code compiles or a coding agent reports success.

## 23. Version plan

A practical version sequence is:

| Version | Primary capability |
| --- | --- |
| V0.1 | CI green + typed events + reducer/replay |
| V0.2 | checkpoint/resume + artifact store |
| V0.3 | repository grounding + Git/worktree + enforced task contract |
| V0.4 | Pi + verifier + supervisor integrated runtime |
| V0.5 | requirement gate + project DAG runtime |
| V0.6 | epistemic update + experiment contracts |
| V0.7 | evidence-driven replanning |
| V0.8 | memory consolidation + context manifest |
| V0.9 | resource locks + parallel DAG execution |
| V1.0 | CLI/API + complete verified demonstration |

## 24. Priority ordering

### P0

```text
CI / invariant correctness
Event replay
Crash-safe resume
```

### P1

```text
Artifact provenance
Task-contract enforcement
Worktree isolation
Independent verification
Supervisor
Requirement / Decision Gate
Epistemic update
Evidence-driven replanning
```

### P2

```text
Memory consolidation
Structural index improvements
Context optimization
Parallel execution
```

### P3

```text
CLI/API
Web UI
```

### P4

```text
Learned orchestration policies / RL
```

## 25. Immediate next PRs

### PR 1: bootstrap-v0

Scope:

```text
current repository standards
Apache-2.0
current typed domain/runtime skeleton
CI fixes only
```

Goal: establish a green, squash-merged baseline.

### PR 2: state-replay

Scope only:

```text
typed event payloads
event codecs
ProjectState
reducers
replay
schema-version fail-closed behavior
determinism tests
```

No unrelated Pi or UI work.

### PR 3: checkpoint-resume

Scope only:

```text
checkpoint model
snapshot storage
resume
external-state reconciliation
restart integration tests
```

After this PR, EpiPilot should be able to restart after interruption and know what it was doing, what it believed, why it believed it, and what work is safe to continue.

## 26. Learning / RL after V1.0

Do not prioritize RL before the deterministic framework produces trustworthy trajectories and outcomes.

After V1.0, logged trajectories naturally provide:

```text
state -> decision -> action -> observation -> outcome
```

Candidate learned policies include:

```text
ask-user policy
experiment-selection policy
task-priority policy
context-retrieval policy
agent-intervention policy
replanning-scope policy
stopping policy
```

The deterministic framework and event/evidence system should remain the safety and audit substrate even when orchestration policies become learned.
