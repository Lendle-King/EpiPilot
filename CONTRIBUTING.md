# Contributing to EpiPilot

Thank you for contributing to EpiPilot. The project is intentionally strict about state integrity, provenance, verification, and module boundaries because EpiPilot coordinates long-running autonomous work where silent state corruption is more costly than local implementation inconvenience.

## Development principles

Every contribution MUST preserve these invariants:

1. **No completion without evidence.** An executor cannot mark its own task as passed.
2. **No canonical fact from executor self-report alone.** Executor output is an observation until independently validated.
3. **Every plan mutation is attributable.** A task-graph change must reference a requirement, user decision, or evidence item.
4. **Canonical state is not prompt context.** Persistent project state and the context compiled for a model call are separate abstractions.
5. **No blind retry loops.** Repeating the same failure requires either new information or a changed strategy.
6. **Append facts; do not erase history.** State changes that affect auditability should be represented as events or explicit supersession, not silent overwrite.
7. **Fail closed on authority boundaries.** Missing provenance, invalid scope, unverifiable completion, or forbidden mutation must not be treated as success.

## Repository workflow

- Branch from `main`.
- Use focused branches such as `feat/epistemic-store`, `fix/task-transition`, or `docs/context-contract`.
- Keep pull requests small enough to review semantically.
- Do not mix unrelated refactors with behavioral changes.
- Prefer squash merging unless preserving a sequence of commits materially improves auditability.
- Never commit credentials, model API keys, private datasets, user transcripts, or generated secrets.

## Commit messages

Use Conventional Commit style where practical:

```text
feat: add hypothesis evidence links
fix: reject executor-owned task completion
docs: define context compiler contract
refactor: isolate scheduler priority policy
test: cover repeated-failure guard
chore: update lint configuration
```

A commit should explain one coherent change. Avoid messages such as `update`, `fix stuff`, or `wip` on review-ready branches.

## Python requirements

EpiPilot targets Python 3.11+ unless the project configuration states otherwise.

All production Python code MUST:

- use type annotations for public functions and methods;
- pass Ruff formatting and linting;
- pass the configured type checker;
- avoid mutable module-level state for canonical project data;
- use explicit domain types instead of unstructured dictionaries at module boundaries;
- raise domain-specific exceptions when an invariant is violated;
- keep I/O at adapters/boundaries rather than mixing it into domain logic;
- avoid catching `Exception` unless re-raising with preserved causality or at a deliberate process boundary;
- preserve exception chaining with `raise ... from exc` when translating exceptions.

## Domain modeling rules

### IDs

Canonical entities use opaque typed IDs (`TaskId`, `HypothesisId`, `EvidenceId`, etc.) rather than user-facing names as identifiers.

### Time

Persist timestamps as timezone-aware UTC datetimes. Never persist naive datetimes.

### State transitions

Do not mutate task or hypothesis status ad hoc. Status changes must pass through transition functions/policies that validate legal transitions and emit an event.

### Provenance

Facts, evidence, decisions, and context items must carry enough provenance to answer: **where did this come from, what scope does it apply to, and is it still valid?**

### Serialization

Serialized schemas are public contracts once released. Breaking changes require an explicit migration path or a versioned schema.

## Architecture boundaries

The intended dependency direction is:

```text
core/domain
    ^
requirements  epistemics  planning  verification
    ^              ^           ^
context       scheduler     supervisor
    ^              ^           ^
executors/adapters  runtime/application
    ^
api/cli/ui
```

Domain modules MUST NOT import concrete executor implementations, web frameworks, database clients, or UI code.

Adapters may depend on domain interfaces; domain code must never depend on adapters.

## Testing expectations

Every behavioral change requires tests appropriate to its risk.

At minimum:

- pure domain logic -> unit tests;
- state transitions -> positive and negative transition tests;
- persistence/event replay -> round-trip and replay tests;
- executor adapters -> contract tests using fakes plus integration tests when practical;
- concurrency/resource locks -> deterministic race/ownership tests;
- bug fixes -> regression test reproducing the original failure;
- security/authority boundary changes -> adversarial negative tests.

Tests must not depend on wall-clock timing, network availability, or nondeterministic model behavior unless explicitly marked as integration/e2e tests.

## Quality gate

Before submitting a pull request, run:

```bash
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
mypy src
pytest
```

`pre-commit run --all-files` is also recommended and mirrors local style checks.

## Pull request checklist

A PR is review-ready when:

- the change has a clear problem statement and scope;
- new behavior is covered by tests;
- invariants affected by the change are named explicitly;
- schema or state-machine changes document migration/compatibility impact;
- public APIs and configuration changes are documented;
- logs and fixtures contain no private or secret material;
- CI passes without suppressing errors through broad ignores.

## Documentation

Architecture decisions that alter durable project behavior should be documented under `docs/` and, when the trade-off is non-obvious, recorded as an ADR under `docs/adr/`.

Code comments should explain **why** a non-obvious constraint exists, not restate what the code already says.
