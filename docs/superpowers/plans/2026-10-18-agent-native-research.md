# Agent-native Research Integration Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline, with one fresh final reviewer.

**Goal:** Coding agents can discover, execute, verify, revise and resume research through
one canonical CARTS state, with credible evidence and bounded resource use.

**Architecture:** Thin agent CLI and structured discovery/measurement ports over current
ProjectState/EventStore. Frontier metadata references canonical entities. Atomic discovery
batches, relevance/quality-gated resolution, outcome-aware phases and exploration budget.

**Tech Stack:** Python 3.11+, existing Pydantic/SQLite/pytest; no new dependencies.

**Spec:** ../specs/2026-10-18-agent-native-research-design.md

## Global Constraints

- Canonical evidence/events and audits append-only; no parallel project truth.
- No self-report or successful arbitrary command can promote scientific knowledge.
- No frozen ETF changes, forward backfills, remote actions or real orders.
- Test behavioral contracts first; ruff format/check, mypy src and pytest before completion.

## Review Focus

- A stale or partially invalid discovery batch must leave no canonical additions.
- Unrelated/inconclusive/failed evidence cannot resolve a critical unknown or trigger optimization.
- New counterevidence invalidates dependent knowledge and reopens relevant questions.
- Actual overruns and recovery must retain real cost, not silently discard measurements.
- Expensive optimization must not consume all budget for feasible unknown exploration.

### Task 1: Atomic canonical discovery and frontier contracts

Files: runtime event stores; research frontier models/events/reducer/discovery service;
canonical event registry. Interfaces: AtomicEventStore.append_many; DiscoveryBatch;
ResearchFrontier on ResearchState; apply_discovery(engine, root, batch).

- [x] Write/run failing atomicity, exact-source, stale version, duplicate/link and replay tests.
- [x] Implement strict immutable frontier values and atomic canonical batch composition.
- [x] Run targeted tests and commit coherent discovery slice.

### Task 2: Relevant measurement and revisable knowledge

Files: research measurement adapter, frontier resolver, engine reservation seam.
Interfaces: MeasurementContract, registered runner/verifier; resolve/reopen services.

- [x] Test missing artifacts, arbitrary success, failed/inconclusive/irrelevant evidence,
      sample limits, contradiction, budget overrun and restart.
- [x] Implement input-bound deterministic measurements and scoped resolution events.
- [x] Run targeted tests, preserve legacy engine behavior and commit.

### Task 3: Agent packets, progressive selection and CLI

Files: agent policy/service/CLI; pyproject entry point; portable research skill/docs;
minimal workspace API dispatch integration.
Interfaces: iterate packet; discover/start/run/observe/verify/resolve/budget/resume commands.

- [x] Test phase depends on critical resolution, feasible exploration share, no-cost
      abstention, no unchanged repeats, CLI roundtrip and legacy compatibility.
- [x] Implement bounded packets, purpose-aware ranking, explicit discovery refresh and
      thin CLI, keeping existing CARTS entry points intact.
- [x] Run targeted checks and commit.

### Task 4: Actual trajectories, integration and delivery

- [x] Run synthetic agent trajectories and comparative exploration checks; distinguish
      scripted semantic fixtures from real agent discoveries and avoid universal claims.
- [x] Use actual ETF panel files for a preregistered missingness-boundary experiment,
      independent recomputation, new scoped claims and next-frontier update.
- [x] Full gates; independent review; fix findings with tests; local integration; record
      measured results, source anchors, limitations and reproducible commands.

Validation outcome: docs/carts/AGENT_RESULTS_20261018.md. Synthetic semantic paths were
regression fixtures; no new universal efficiency benchmark is claimed. All implementation
slices were committed together after the full gate and independent review.
