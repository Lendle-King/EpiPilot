# Memory Architecture Contract

EpiPilot treats memory as a typed, scoped, provenance-aware subsystem. Memory is not a transcript dump and is not equivalent to the context sent to a coding agent.

## 1. Three different things

The implementation must keep these concepts separate:

1. **Canonical project state** — requirements, decisions, facts, unknowns, hypotheses, evidence, tasks, plan topology, and events.
2. **Long-term memory** — durable references, consolidated experiences, procedures, and structural indexes used to retrieve useful information later.
3. **Working context** — a bounded projection compiled for one concrete executor task.

The invariant is:

```text
Canonical state != Long-term memory != Working context
```

Conversation history is an archival input source, not canonical project state.

## 2. Memory classes

EpiPilot uses six logical classes.

### Normative

Goals, hard constraints, policies, and user-owned decisions. Normative memory should normally reference canonical requirement/decision entities rather than duplicate their prose.

Normative items have high authority. Relevant hard constraints are mandatory context and must never be silently dropped to satisfy a token budget.

### Semantic

Evidence-backed project facts. Semantic memory references canonical Fact entities. A textual copy of a fact must not become an independent source of truth.

### Epistemic

Unknowns, assumptions, hypotheses, confidence state, supporting evidence, and contradicting evidence. Epistemic items remain distinguishable from facts so a tentative claim cannot silently become established truth.

### Episodic

Consolidated high-value experiences with an explicit structure:

```text
problem -> attempt -> outcome -> lesson
```

Raw session transcripts are not episodic memory. Consolidation should retain only experiences that are reusable, diagnostically valuable, or materially affect future decisions.

### Procedural

Reusable skills, playbooks, and runbooks. Every procedural memory has an explicit activation trigger and scope. Procedures are retrieved when their trigger matches the current task; they are not injected globally by default.

### Structural

Generated repository/system maps such as symbol indexes, dependency maps, test mappings, and ownership maps. Structural memory must be pinned to a source revision so stale indexes can be detected after the repository changes.

## 3. Canonical references instead of stale copies

Normative, semantic, and epistemic memory should normally use `CanonicalMemoryRef`:

```text
MemoryRef
  -> kind
  -> entity_id
  -> scope
```

The Context Compiler resolves the current canonical entity at compile time.

This prevents the failure mode:

```text
Fact v1 copied into memory
Fact v1 superseded by Fact v2
old memory copy continues to enter prompts
```

## 4. Hierarchical scope

Memory visibility follows a hierarchy:

```text
project
  -> repository
      -> workstream
          -> task
```

A broad project item can be visible to a child task. A task-local item must not leak to a sibling task or to a broader scope.

Scope is an authorization/relevance boundary, not merely a retrieval hint.

## 5. Hot, warm, and cold are retrieval tiers

Hot/warm/cold describe context-access policy, not semantic memory classes.

### Hot

Small mandatory set that is normally visible for the current task:

- current goal;
- hard constraints;
- current task contract;
- relevant user decisions;
- active high-impact hypothesis/unknown.

### Warm

Retrieved on demand:

- relevant facts and evidence;
- episodic lessons;
- procedures;
- structural indexes;
- related hypotheses.

### Cold

Complete archival material:

- immutable event streams;
- raw executor transcripts;
- large logs;
- old artifacts;
- superseded historical state.

Cold data is not injected directly into an LLM context without an explicit retrieval/consolidation step.

## 6. Memory write pipeline

Executors cannot write canonical facts directly.

Required flow:

```text
Executor observation
        -> memory/state candidate
        -> classify
        -> validate
        -> canonical commit or consolidation
```

Examples:

- `Pi says rollout is the bottleneck` -> Observation or Hypothesis.
- `Profiler reproducibly shows rollout consumes 71% wall time` -> Evidence, then potentially a Fact.
- `Three similar incidents reveal the same recovery pattern` -> candidate Episodic or Procedural memory after consolidation.

## 7. Provenance and temporality

Every durable claim must be traceable to a source. Facts and structural indexes must support stale/superseded detection rather than silent overwrite.

Belief changes should be represented as transitions backed by evidence:

```text
H-17 confidence 0.70
   -> EV-42 contradicts
H-17 confidence 0.25
```

Do not destroy the previous belief state in a way that makes the update unauditable.

## 8. Context compilation

Working context is created from current project state plus retrieved memory:

```text
Context_t = Compile(
    goal,
    task contract,
    hard constraints,
    relevant facts,
    active hypotheses/unknowns,
    evidence,
    procedures,
    structural code context,
    high-value episodes,
    token budget,
)
```

Optional items are ranked using relevance, authority, confidence, freshness, scope match, and token cost. Mandatory authoritative state is never removed just because a semantically similar optional memory scores highly.

If mandatory context exceeds the available budget, compilation must fail closed and request a larger/different context strategy instead of truncating requirements silently.

## 9. Contradictions and supersession

Contradictions are explicit data. Do not resolve them by overwriting the older item.

Prefer relationships such as:

```text
Fact A --superseded_by--> Fact B
Evidence X --contradicts--> Hypothesis H
Procedure P2 --supersedes--> Procedure P1
```

The current view may hide stale items from normal retrieval, but the history remains reconstructable from canonical state and events.

## 10. Consolidation policy

Do not retain every interaction as a reusable memory. Promote an episode or procedure only when at least one of these is true:

- it prevented or explained a meaningful failure;
- it is likely to recur;
- it changes future task selection or verification;
- it captures a stable repository/project convention not already represented canonically;
- it materially reduces future investigation cost.

Consolidation should deduplicate equivalent items and preserve provenance to the source episodes/events.

## 11. Security and sensitive data

Memory persistence increases the lifetime of data, so durable memory requires stricter treatment than transient context.

Do not persist secrets, credentials, tokens, private keys, or unnecessary raw user data in semantic/episodic/procedural memory. Store references to secured artifacts where possible. Redaction must happen before durable consolidation, not only before display.

## 12. Implementation invariants

1. Canonical requirements/facts/hypotheses are referenced, not silently copied into independent free-form truth.
2. Executor self-report cannot promote an item to canonical Fact.
3. Task-local memory cannot leak across scope boundaries.
4. Structural memory must identify the source revision it describes.
5. Procedural memory must have an activation trigger.
6. Mandatory normative context cannot be silently truncated.
7. Raw conversation history is archival data, not canonical memory.
8. Supersession and contradiction remain auditable.
