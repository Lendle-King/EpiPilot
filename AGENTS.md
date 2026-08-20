# AGENTS.md

This file defines repository-wide instructions for coding agents working on EpiPilot.

## Mission

EpiPilot is an evidence-driven epistemic orchestration framework for long-horizon coding agents. It sits above executors such as Pi, Codex, Claude Code, or other coding agents. The framework owns requirements, epistemic state, planning, scheduling, context compilation, verification, and recovery. Executors do not own canonical project truth.

## Non-negotiable invariants

1. Never mark a task `PASSED` from executor self-report alone.
2. Never promote an observation to a canonical fact without validation/provenance.
3. Every task-graph mutation must be traceable to a requirement, user decision, or evidence item.
4. Never use conversational history as canonical project state.
5. Never silently overwrite auditable history; use events, versioning, or explicit supersession.
6. Never retry an unchanged failure indefinitely. A retry requires new information or a changed strategy.
7. Fail closed when authority, provenance, scope, or verification is ambiguous.

## Coding rules

- Python 3.11+.
- Public APIs must be typed.
- Prefer immutable/value-oriented domain objects.
- Use enums or explicit value objects for finite state.
- Keep domain logic deterministic and side-effect free where possible.
- Do not put database, network, subprocess, or LLM calls inside core domain models.
- Use ports/interfaces for external systems and adapters for implementations.
- Avoid generic `dict[str, Any]` across module boundaries unless the boundary is intentionally schema-less; use typed models otherwise.
- Preserve timezone-aware UTC timestamps.
- Do not suppress lint/type errors broadly.
- Do not add a dependency when the standard library or an existing dependency provides a clear, maintainable solution.

## Required development sequence for behavioral changes

1. Identify the invariant or contract affected.
2. Add or update tests that demonstrate expected and rejected behavior.
3. Implement the smallest coherent change.
4. Run formatting, lint, type checks, and tests.
5. Update documentation if a public contract, schema, state transition, or architecture boundary changed.

## State and event rules

- Canonical state transitions occur through explicit functions/services.
- Important transitions emit typed events.
- Events are append-only after persistence.
- Replay must reconstruct equivalent state.
- Derived views may be rebuilt; canonical events/evidence must not depend on those views for meaning.
- Entity IDs must remain stable across replay and serialization.

## Epistemic rules

Keep these concepts distinct:

- `Observation`: raw report or measured output not yet promoted.
- `Evidence`: validated, provenance-carrying observation usable for reasoning.
- `Hypothesis`: falsifiable proposition under investigation.
- `Fact`: canonical claim justified to the project's configured evidence threshold.
- `Unknown`: decision-relevant unresolved question.
- `Decision`: an action/choice with authority and rationale.

An executor statement such as "the bottleneck is rollout" is an observation, not a fact.

## Task rules

Tasks should define:

- objective;
- preconditions;
- allowed and forbidden scope;
- expected outputs;
- verification/acceptance contract;
- required resources;
- linked requirements/hypotheses/evidence where relevant.

A task may become `SUPERSEDED` because the plan changed; do not misclassify this as execution failure.

## Context rules

A context bundle is a compiled projection of canonical state, not the state itself. Context retrieval must prioritize:

1. hard requirements and user decisions;
2. task contract;
3. currently relevant verified facts and active hypotheses;
4. relevant evidence;
5. procedures and structural code information;
6. selected episodic lessons.

Do not dump entire transcripts or the entire repository into the executor context.

## Security and privacy

- Never commit secrets, credentials, tokens, cookies, private transcripts, or private user data.
- Treat logs, prompts, screenshots, model traces, and artifacts as potentially sensitive.
- Test fixtures must be synthetic unless a file is explicitly approved for repository inclusion.
- Avoid logging raw external responses when structured/redacted logging suffices.
- New external-command execution paths require explicit argument handling and must avoid shell injection.

## Git discipline

- Make focused changes.
- Do not modify unrelated files.
- Do not rewrite public history.
- Keep generated artifacts out of Git unless intentionally versioned.
- Conventional Commit-style messages are preferred.

## Definition of done

A change is not done because the code compiles or an agent says it works. It is done only after the relevant automated or independent verification contract passes.
