# EpiPilot Framework

## 1. Purpose

EpiPilot is an evidence-driven epistemic orchestration framework for long-horizon coding agents. It sits above coding-agent executors such as Pi, Codex, Claude Code, or other backends and owns the durable project-level reasoning and control loop.

The framework is not intended to be a thin prompt wrapper. Its role is to preserve user intent, model uncertainty explicitly, turn uncertainty into testable hypotheses and experiments, supervise coding-agent execution, verify outcomes independently, update project beliefs from evidence, and revise the task graph without losing provenance.

The core loop is:

```text
Goal
  -> Requirement Elicitation
  -> Decision Boundary
  -> Unknowns / Hypotheses
  -> Plan / Experiment
  -> Execute
  -> Observe
  -> Verify
  -> Evidence
  -> Belief Update
  -> Replan
  -> Verified Goal
```

## 2. System responsibilities

EpiPilot owns:

- project goals, success criteria, constraints, preferences, and user decisions;
- the distinction between user-owned decisions and system-resolvable technical unknowns;
- unknowns, hypotheses, predictions, falsification conditions, evidence, and canonical facts;
- versioned task-graph topology and runtime task-state projections;
- task scheduling, information-gain prioritization, resource claims, and dependency checks;
- task-scoped context compilation from canonical state and long-term memory;
- coding-agent supervision, interruption, cleanup, and failure handling;
- independent verification and evidence collection;
- append-only event history, replay, checkpoints, and recovery;
- artifact provenance and repository revision grounding;
- memory consolidation and retrieval policy;
- project-level acceptance.

Coding-agent executors do not own canonical project truth. They may propose observations, changes, candidate hypotheses, artifacts, and completion reports, but EpiPilot decides what becomes evidence, fact, verified completion, or a plan mutation.

## 3. User decision boundary

The framework must not ask the user every technical question. Every unresolved question should be classified into one of three categories.

### 3.1 User-owned decision

Ask the user when the unresolved issue is a value, preference, policy, irreversible risk, budget choice, goal conflict, or major scope decision. Typical examples include:

- target quality or performance level;
- time, GPU, API, or monetary budget;
- whether a breaking change is acceptable;
- whether production deployment or destructive actions are allowed;
- accuracy-versus-latency or quality-versus-cost preferences;
- whether the task definition itself may change.

A user interrupt should normally require all of the following:

```text
user-owned
AND high impact
AND cannot be safely inferred
AND cannot be cheaply tested
AND no safe reversible default exists
```

### 3.2 System-resolvable unknown

Technical facts such as the cause of a bottleneck, the better implementation strategy, or the effect of a configuration should normally become unknowns and hypotheses to investigate rather than questions for the user.

### 3.3 Safe reversible default

Low-risk implementation details may use an explicit default when the choice is reversible and has low decision impact. The default must still be recorded when it affects later reasoning.

The initial requirement phase is complete when the remaining uncertainty is system-resolvable, not when every detail is known.

## 4. Canonical project contract

Each project should have a canonical `ProjectContract` containing at least:

```text
goal
success criteria
hard constraints
soft preferences
budget
forbidden actions
user-owned decisions
```

A project must not enter autonomous execution without a meaningful goal and explicit success criteria. Hard constraints and forbidden actions are authoritative context and must never be silently dropped because of token pressure or semantic retrieval ranking.

## 5. Epistemic state

The framework must keep the following concepts distinct:

- `Observation`: raw or executor-reported information that is not yet authoritative;
- `Unknown`: a decision-relevant unresolved question;
- `Hypothesis`: a falsifiable proposition under investigation;
- `Evidence`: a validated, provenance-carrying observation that can support reasoning;
- `Fact`: a canonical claim justified to the configured evidence threshold;
- `Decision`: an authorized choice with rationale and traceability.

An executor statement such as "the rollout path is the bottleneck" is not a fact by itself.

### 5.1 Hypothesis requirements

An active hypothesis should carry:

```text
statement
rationale / prior
observable predictions
falsification condition
linked unknowns
linked experiments/tasks
supporting evidence
contradicting evidence
current status / confidence
impact if true
impact if false
```

A hypothesis must not silently become a fact. Promotion requires validation and provenance.

### 5.2 Evidence strength

Evidence should prefer deterministic and reproducible sources over semantic or executor self-report. A practical ordering is:

```text
deterministic check / measurement
> reproducible runtime experiment
> static inspection
> independent semantic verifier
> executor observation
> executor self-report
```

Executor self-report alone must never authorize a canonical fact or task completion.

## 6. Unknown registry and experiment design

The system should explicitly record what it does not know. An unknown may include:

```text
question
impact
blocking decisions/tasks
candidate hypotheses
resolvable-by mechanism
expected information value
```

When multiple plausible hypotheses exist, the planner should prefer the minimum discriminative experiment that can change the decision. Experiments should define predictions and falsification criteria before execution to reduce post-hoc interpretation.

The objective is not to eliminate all uncertainty. Exploration should stop when additional information is unlikely to change the current decision enough to justify its cost.

## 7. Dynamic task graph

EpiPilot should maintain a versioned DAG or AND/OR-style task graph rather than a flat to-do list.

Typical node kinds include:

```text
ACTION
EXPERIMENT
ANALYSIS
VERIFY
USER_DECISION
MERGE
CHECKPOINT
DELIVERABLE
```

Task topology and runtime execution state are distinct. A `RUNNING -> VERIFYING` transition does not create a new plan version. A structural mutation such as adding, removing, replacing, or rewiring nodes does.

Every structural plan mutation must be traceable to at least one of:

```text
Requirement
Decision
Evidence
```

Tasks whose premise has become invalid should normally become `SUPERSEDED`, not `FAILED`.

## 8. Task contract

A task is a bounded execution contract, not merely a natural-language prompt. It should define:

```text
objective
preconditions
repository revision
allowed read paths
allowed write paths
forbidden paths
expected outputs
acceptance / verification rules
resource claims
linked requirements / hypotheses / evidence
rollback or workspace basis
```

The runtime must enforce these limits. After execution, changed files must be checked against allowed and forbidden scopes. A passing test does not excuse a scope violation.

## 9. Scheduler

The planner decides what work exists. The scheduler decides what work should run now.

Scheduling should consider dependency readiness, risk, cost, resources, and information value. A useful initial heuristic is:

```text
Priority(T) =
    Impact(T)
    * Unblocking(T)
    * InformationGain(T)
    * Urgency(T)
    / (Cost(T) * Risk(T))
```

The factors should be explicit scheduling inputs rather than hidden LLM intuition. This lets small discriminative experiments outrank expensive implementation when they can eliminate incorrect branches cheaply.

## 10. Executor boundary

Coding agents are replaceable executors behind a stable adapter interface. EpiPilot should support backends such as Pi without coupling domain logic to one provider.

A coding-agent executor may:

- start a task-scoped session;
- report progress and observations;
- produce changes and artifacts;
- accept steering or interruption;
- terminate and clean up.

It may not:

- write canonical facts directly;
- mark a task `PASSED` directly;
- silently approve user-owned interactive decisions;
- mutate the project graph without an authorized basis.

For Pi RPC, `agent_end` maps only to `AGENT_REPORTED_DONE`; interactive confirmation/input requests should surface as blocked or user-decision-required unless an explicit policy authorizes them.

## 11. Verification

Completion is evidence-gated. The normal lifecycle is:

```text
RUNNING
  -> AGENT_REPORTED_DONE
  -> VERIFYING
  -> PASSED | FAILED
```

`PASSED` requires independent completion evidence. Deterministic checks should be preferred when possible. Semantic verification should be reserved for criteria that cannot be decided mechanically.

A verifier pipeline must fail if any required acceptance check fails. One successful check must not mask another failed check.

## 12. Supervisor and failure handling

The supervisor controls execution rather than acting as another conversational agent. It should detect meaningful progress, missing context, stalls, repeated failure signatures, scope violations, and important new observations.

Progress should be grounded in concrete deltas such as:

```text
new diff
new commit
new passing test
new artifact
new evidence
new diagnosis
new hypothesis
```

A repeated failure must not be retried unchanged. The same failure signature requires new information or a changed strategy, and repeated identical failures should eventually escalate rather than burn tokens indefinitely.

Failure handling should distinguish at least:

```text
implementation failure
verification failure
missing context
task-contract failure
environment failure
assumption failure
```

## 13. Memory architecture

Canonical state, long-term memory, and working LLM context are separate concepts.

```text
Canonical State != Long-term Memory != Compiled Context
```

The memory system should use multiple classes:

- normative memory: goals, policies, constraints, user decisions;
- semantic memory: references to verified facts;
- epistemic memory: references to unknowns, hypotheses, and evidence;
- episodic memory: selected high-value success/failure experiences;
- procedural memory: trigger-based skills, playbooks, and runbooks;
- structural memory: repository maps and code-structure information pinned to a source revision;
- archival/event history: complete immutable events, logs, and artifacts, not injected by default.

Authoritative state should be referenced rather than copied into another free-form memory representation. This reduces stale duplicate truth.

## 14. Context compiler

The executor context is a bounded projection dynamically compiled from project state and memory. It is not a transcript dump.

A task context should normally prioritize:

```text
1. goal
2. hard constraints
3. user decisions
4. current task contract
5. active hypotheses
6. relevant verified facts
7. relevant evidence
8. repository structure
9. procedures/playbooks
10. selected episodic lessons
```

Mandatory authoritative items must never be silently dropped because they exceed the token budget. In that case the compiler should fail closed and require a different representation or budget.

Optional retrieval should consider relevance, authority, confidence, freshness, scope match, and token cost rather than embedding similarity alone.

Each compile should eventually produce a `ContextManifest` that records included and excluded item IDs, ranking reasons, source revisions, token accounting, and compiler version.

## 15. Event sourcing and durable state

Important state changes should emit typed append-only events. Project state should be reconstructable as:

```text
State_n = Reduce(State_0, Event_1, ..., Event_n)
```

Reducers should be deterministic and side-effect free. Event payloads must be schema-versioned and fail closed on unknown or malformed versions.

Checkpoints are recovery optimizations, not canonical truth. A corrupted checkpoint should be discardable so that the state can be reconstructed from the event stream.

## 16. Artifact-first execution

Important outputs such as code diffs, test logs, metrics, traces, benchmark results, configurations, and reports should be first-class artifacts with provenance and content hashes.

Evidence should reference artifacts rather than duplicating large outputs into free-form summaries. Artifacts should record producer task/session/event IDs and repository revision where applicable.

## 17. Repository grounding and isolation

Repository structure should be indexed from the real repository and pinned to a Git revision. Structural memory becomes stale when the revision changes.

Execution should use isolated workspaces, ideally Git worktrees/branches per task. Parallel tasks may run only when dependency, resource, and write-scope constraints are compatible.

Multiple coding agents should coordinate through EpiPilot canonical state, not by maintaining uncontrolled peer-to-peer conversational truth.

## 18. Replanning policy

Replanning should be proportional to the evidence change:

```text
LOCAL_PATCH
SUBGRAPH_REPLAN
GLOBAL_REPLAN
```

`LOCAL_PATCH` is preferred for implementation-level correction. `SUBGRAPH_REPLAN` is appropriate when a local hypothesis or approach is refuted. `GLOBAL_REPLAN` is reserved for major goal, constraint, or root-assumption invalidation.

Every replan should produce an auditable plan diff with its evidence/decision/requirement basis.

## 19. Project acceptance

A project is not complete because the planner or executor says it is. Final acceptance should require:

```text
all hard requirements verified
AND success criteria verified
AND required deliverables present
AND blocking unknowns resolved or explicitly accepted
AND required tasks resolved
AND final integration verification passed
```

## 20. Architectural planes

The system is organized into four logical planes:

```text
Interface Plane
  CLI / API / UI

Control Plane
  Requirements / Epistemics / Planning / Scheduler
  Context / Supervisor / Verification / Recovery

Execution Plane
  Pi / Codex / Claude Code / other adapters
  isolated workspaces / subprocesses / resources

State Plane
  Requirements / Decisions / Unknowns / Hypotheses / Evidence
  Task Graph / Memory / Events / Artifacts
```

Domain logic must not depend directly on databases, subprocesses, network providers, or UI frameworks. External systems belong behind ports/adapters.

## 21. Non-negotiable invariants

The project should preserve these invariants across all future versions:

1. No completion without evidence.
2. Executor self-report alone never creates canonical truth.
3. Every structural plan mutation is traceable to a requirement, decision, or evidence item.
4. Canonical state, long-term memory, and compiled context remain distinct.
5. Repeated failure cannot be retried unchanged without new information or strategy.
6. Auditable history is never silently overwritten.
7. Ambiguous authority, provenance, scope, or verification fails closed.
8. Technical unknowns should normally be investigated rather than pushed back to the user.
9. User-owned irreversible or value decisions must not be silently automated.
10. The runtime must be recoverable and replayable for long-horizon work.

## 22. V1.0 success definition

EpiPilot V1.0 should be able to accept a real project goal containing genuine technical uncertainty, clarify only the user-owned decisions, investigate unknowns, supervise a coding agent, verify outcomes independently, revise its plan from evidence, survive interruption/restart, and finish with an auditable verified result.
