# Research Scout Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; apply TDD to each task.

**Goal:** Deliver the approved bounded Scout -> decision score -> verified calibration loop.
**Architecture:** Extend CARTS and its canonical EventStore; pure score/calibration and thin
host-facing Scout services; existing independent measurement and managed Git delivery.
**Tech Stack:** Python 3.11+, existing Pydantic/SQLite/pytest, no new dependency.
**Spec:** docs/superpowers/specs/2026-10-22-research-scout-design.md

## Global Constraints

- Edit only the acquired research-scout-v1 lease, leave canonical root and user index intact.
- Typed immutable models, UTC time, no I/O in reducers or scoring.
- No scientific acceptance from quotes, forecasts, engineering gates or executor self-report.
- One budget ledger, explicit costs, no silent resets, no publication this session.
- Five typed event families, old event replay remains compatible.

## Review Focus

- Stale or invalid scout submission still accounts genuine overhead without partial science.
- Forecast and experiment reservation cannot separate across an interrupted writer.
- Downstream choices cannot invent affordable or supported prerequisites.
- Calibration must not learn from a future result or treat model gain as actual value.
- Independent gate must import candidate sources, never the user's editable installation.

## Task 1: Pure contracts, decision scoring and calibration

Files: research/intelligence_models.py, research/decision_value.py;
tests/research/test_decision_value.py.
Interfaces: ResearchPolicy, DecisionEstimate, DecisionForecast, DecisionOutcome and pure
score_estimate/calibration. Decision tables have <=4 outcomes and <=3 options.
- [x] Write tests: irrelevant signal gives zero; changing optimum gives positive gain;
  incoherent/duplicate/nonfinite tables reject; small samples preserve priors; correction
  excludes incompatible predictors; costs and risk use fixed scales.
- [x] Run targeted pytest; expected missing interface failure before implementation.
- [x] Implement smallest frozen contracts and deterministic arithmetic.
- [x] Run targeted tests, formatting, lint and type checks.

## Task 2: Canonical lifecycle, overhead budget and replay

Files: core/events.py, events/registry.py, state/project.py, state/reducer.py,
research/models.py, research/intelligence_reducer.py, research/budget.py;
tests/research/test_intelligence_events.py.
Interfaces: five payload families and IntelligenceState, overhead reservation/settlement
on existing ResearchState; canonical policy references and stable operation IDs.
- [x] Test policy authority, codec/replay, overhead quota, duplicates and forecast-before-run.
- [x] Run tests and confirm missing behavior.
- [x] Add reducers and budget projection; no database schema fork.
- [x] Run targeted and legacy replay/budget tests.

## Task 3: Scout service, source reader and CLI

Files: research/scout.py, research/discovery.py, research/agent_cli.py;
tests/research/test_scout.py.
Interfaces: prepare(operation_id)->ScoutRequest; submit(ScoutSubmission)->ScoutReceipt;
validated discovery entries reusable in atomic combined completion.
- [x] Test bounds/idempotency, real costs on stale/invalid results, source provenance,
  Git-only revision paths absent in user checkout, no-op epoch suppression and CLI schema.
- [x] Run tests and observe missing behavior.
- [x] Implement host protocol and pinned reads; preserve scientific validation gates.
- [x] Run Scout, discovery and CLI regression tests.

## Task 4: Connect selection, frozen forecast and outcome reconciliation

Files: research/policy.py, research/agent_policy.py, research/agent_service.py,
research/engine.py, research/reporting.py; docs/carts/SCOUT.md and AGENT_PROTOCOL.md;
tests/research/test_scout_cycle.py.
Interfaces: shared feasibility independent of legacy score; opt-in Ranking; atomic
forecast/reservation; idempotent review/settlement-driven outcome reconciliation.
- [x] Test full synthetic loop, legacy cutoff bypass, failures/cancellation, replay/resume,
  read-only iterate and unconfigured-project compatibility.
- [x] Run tests and confirm missing behavior.
- [x] Implement integration and operator docs; preserve explicit exploration selection reason.
- [x] Run complete ruff format, ruff check, mypy src and pytest.
- [x] Review complete diff, correct significant findings with regression tests.
Delivery condition: use work complete for exact-candidate engineering gates and local
integration; no push. Its canonical operation receipt records completion.

## Verification and review record

Baseline: 432 tests, Ruff formatting/lint and strict mypy passed on the original checkout.
Pre-review implementation: 455 tests passed. One fresh read-only reviewer found six material
boundary defects; synthetic regression tests reproduced all six before the fix pass:

- Unknown downstream proposal: reject at Scout admission/replay and tolerate unavailable
  references during reconciliation (`test_unknown_downstream_reference_rejects_submission_before_execution`).
- Mutex/graph unlocking: evaluate the shared hard gates under scoring-only assumptions;
  canonical task/hypothesis objects are never changed (`test_unaffordable_unlock_cannot_inflate_decision_value`).
- Detached/duplicate forecast: validate atomic application/replay boundaries and one forecast
  per reservation (`test_standalone_forecast_cannot_persist_or_double_count_an_experiment`).
- Forged/stale revision and policy estimates: bind accepted epoch/revision and replace estimate
  sets (`test_forged_forecast_revision_is_rejected`, `test_new_policy_does_not_reuse_old_epoch_estimates`).
- Settlement crash recovery: reconcile even when engine resume has no active reservation
  (`test_public_engine_resume_recovers_outcome_after_settlement_crash`).
- Skipped agent gates: share cost-unit, scientific-question and baseline eligibility across
  both ranking policies (`test_optimize_requires_supported_baseline_with_new_score`).

All six reproductions were RED before the fix and GREEN after it; 41 targeted tests passed.
The author's final pass will run all gates again before managed exact-candidate acceptance.

Ruling: per-project AgentPacket instructions and AGENT_PROTOCOL route the host through Scout;
the existing portable generic skill remains compatible and unchanged. If a host ignores its
runtime packet it may need updated instructions, but cannot bypass scientific acceptance.

Review limitations: synthetic tests do not establish universal policy superiority or live
external-source authenticity. External captures remain document observations. Managed gate
execution and candidate imports are verified by the final delivery, not by reviewer claims.

Deferred minor: malformed submission JSON/schema cannot settle a known cost until a corrected
submission is provided. The reservation remains visible and blocks further spending; this
recovery is documented rather than adding another overlapping command.

Final post-fix regression: 462 tests passed (one existing Starlette/httpx deprecation warning).
The final managed operation runs formatting, lint, strict types and all tests again against
its exact candidate before integration; its receipt is the authority for delivery status.
