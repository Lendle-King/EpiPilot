# quant_crypto CARTS Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a thin, optional host in `quant_crypto` that initializes a separate EpiPilot CARTS project and advances only hash-pinned BTC/ETH research through reviewed discovery, a registered runner, independent verifier, canonical evidence, and resumable settlement.

**Architecture:** Keep EpiPilot imports inside `quant_crypto.integrations.carts`; the core data and backtest package remains installable without EpiPilot. Use only documented `ResearchEngine`, `AgentService`, `apply_discovery`, `MeasurementContract`, `Computer`, `ExperimentRunner`, and `EvidenceVerifier` interfaces with static adapter bindings and the existing canonical workspace database.

**Tech Stack:** Python 3.11+, installed EpiPilot package/environment, existing CARTS SQLite event store, typed `quant_crypto` manifests and verification reports, pytest synthetic integration fixtures.

**Spec:** `docs/superpowers/specs/2026-10-20-quant-crypto-design.md`; standalone artifact interfaces are defined in `docs/superpowers/plans/2026-10-21-quant-crypto.md`.

## Global Constraints

- Use a distinct canonical project ID `quant-crypto-btc-eth-spot`; never read or copy ETF events, evidence, hypotheses, frontier, or budget.
- Use the existing `EPIPILOT_WORKSPACE_DB` database or its default `~/.local/share/epipilot/workspace.sqlite3`; never create a second research event store or reset a budget.
- Require an explicit positive `--wall-seconds` value when initializing CARTS; no default and no inherited ETF budget. Explain that this meters runner/verifier wall time, not semantic coding-agent/API usage.
- Use `AgentService.iterate()` for canonical packets, `apply_discovery()` for reviewed discovery batches, and `ResearchEngine.run_next(runner, verifier)` for registered BTC/ETH experiments.
- Provide a static adapter-name-to-`Computer` mapping; do not load adapter names from shell input, use dynamic imports, or execute arbitrary checks.
- Pin every measurement to a Git commit, input SHA-256 values, a strict manifest, role-tagged development/validation/walk-forward-test/final-holdout ranges, cost assumptions, and the exact strategy/config revision.
- Use the existing `MeasurementContract.inputs` to hash-pin the role-bearing manifest and every declared source file; the domain verifier validates the manifest's explicit roles rather than inferring them from file names/order.
- Only a verified F4 `DEVELOPMENT` result may set `qualified_for_promotion=True`, and only when both walk-forward and untouched holdout independently meet CAGR >= 30%, MDD <= 20%, >=730 daily observations, full data/cost lineage, and valid 2x/4x cost stress artifacts.
- Preserve reservation, observation, verification, settlement, replay, and resume semantics; never rerun an unrecorded active reservation or overwrite an artifact.
- Do not start a background daemon or exchange-account/order integration.

## Review Focus

- Reinitializing the same project must preserve its original goal, budget, and history; Task 1 tests idempotent reuse and mismatched-configuration refusal.
- A discovery batch with stale version, changed input, ambiguous role, unsupported adapter, or invalid source anchor must not partially mutate state; Task 2 tests atomic rejection.
- A runner crash after reservation must not silently relaunch or lose recorded cost; Task 3 tests recovery and resume through a reopened SQLite store.
- A syntactically passing but non-independent or target-failing artifact must not promote the goal; Task 3 tests verifier and goal constraints.
- A project using the canonical default database must not derive a second database or project identity; Task 1 tests path/ID selection.

---

### Task 1: Add CARTS runtime, explicit initialization, and typed goal setup

**Files:**
- Create: `src/quant_crypto/integrations/__init__.py`
- Create: `src/quant_crypto/integrations/carts.py`
- Modify: `src/quant_crypto/cli.py`
- Test: `tests/integration/test_carts_init.py`

**Interfaces:**
- Consumes standalone package interfaces from Tasks 1–5 of the quant_crypto plan.
- Produces `CartsRuntime.open(root: Path, db: Path | None, project_id: str) -> CartsRuntime`, `CartsRuntime.initialize(wall_seconds: float) -> None`, and `CartsRuntime.iterate() -> AgentPacket`.
- `open` uses an explicit `--db` argument, then `EPIPILOT_WORKSPACE_DB`, then `Path.home() / ".local/share/epipilot/workspace.sqlite3"`. Its artifact path follows EpiPilot's canonical convention: `db.parent / "carts" / str(stable_id(project_id, "artifacts"))`.
- `initialize` uses `SqliteEventStore` and `ResearchEngine.initialize(ResearchGoal(...), (), ())`, with `required_scope=DEVELOPMENT`, minimum 730 samples, constraints `walk_forward_cagr >= 0.30`, `walk_forward_mdd <= 0.20`, `holdout_cagr >= 0.30`, and `holdout_mdd <= 0.20`, then calls `enable_frontier`. It requires a positive explicit `wall_seconds` budget. On first initialization it creates the goal; on repeat it is a no-op only if goal, budget, project ID, and repository match exactly, otherwise it fails without mutation. A previously configured project with no frontier may have its frontier enabled without replacing canonical history, matching CARTS init semantics.
- Static computer registration uses the single reviewed adapter name `crypto_backtest_v1` mapped to `CryptoResearchComputer`; the project ID is fixed to `quant-crypto-btc-eth-spot` by the CLI unless a future separately authorized project is designed.

- [ ] **Step 1: Write failing tests** named `test_init_requires_positive_explicit_wall_budget`, `test_init_sets_development_goal_and_four_thresholds`, `test_init_uses_canonical_database_and_artifact_path`, `test_repeat_init_preserves_goal_budget_and_history`, `test_repeat_init_rejects_mismatched_budget_without_mutation`, and `test_iterate_returns_canonical_discovery_packet`.
- [ ] **Step 2: Run `pytest tests/integration/test_carts_init.py -q` in an environment with EpiPilot installed and confirm the missing host behavior.**
- [ ] **Step 3: Implement the optional integration runtime, canonical path resolution, goal initialization, frontier setup, and `iterate` command.** Keep imports from `epipilot` inside this optional module so `import quant_crypto` still succeeds without EpiPilot installed.
- [ ] **Step 4: Run the focused tests with a temporary SQLite database and confirm that no user workspace or other project's state is touched.**
- [ ] **Step 5: Commit as `feat: initialize separate crypto CARTS project`.**

### Task 2: Bind reviewed discovery to role-pinned experiment manifests

**Files:**
- Modify: `src/quant_crypto/integrations/carts.py`
- Create: `src/quant_crypto/integrations/manifest.py`
- Modify: `src/quant_crypto/cli.py`
- Test: `tests/integration/test_carts_discovery.py`

**Interfaces:**
- Consumes `CartsRuntime` from Task 1, EpiPilot `DiscoveryBatch`, and the standalone `RunManifest` from the quant_crypto plan.
- Produces `validate_crypto_contract(root: Path, proposal: ExperimentProposal, contract: MeasurementContract) -> RunManifest`, `CartsRuntime.apply_batch(batch: DiscoveryBatch) -> None`, and a CLI `discover --file PATH` command.
- The contract must use the registered adapter `crypto_backtest_v1`, have `proposal.input_revision == contract.fingerprint()`, pin every file through `InputFile(path, sha256)`, and include the serialized role-bearing run manifest as one of those inputs. Manifest roles explicitly cover development, validation, each walk-forward test fold, and final holdout with chronological non-overlapping dates and hashes; the holdout is never treated as selection data.
- `apply_batch` delegates to canonical `apply_discovery`; all source anchors, canonical basis references, stale versions, repository path safety, and atomic event validation remain enforced by EpiPilot.

- [ ] **Step 1: Write failing tests** named `test_contract_binds_manifest_and_every_input_digest`, `test_contract_rejects_filename_derived_or_missing_roles`, `test_contract_rejects_role_overlap_and_holdout_in_selection`, `test_contract_rejects_unknown_adapter`, `test_discovery_uses_canonical_apply_discovery`, and `test_stale_or_invalid_discovery_has_no_partial_state_change`.
- [ ] **Step 2: Run `pytest tests/integration/test_carts_discovery.py -q` and confirm role, digest, and canonical state failures.**
- [ ] **Step 3: Implement strict manifest parsing/validation and route submitted batches through `apply_discovery` without rewriting canonical state or input files.**
- [ ] **Step 4: Run the focused tests against a temporary Git repository and SQLite event store; malformed contracts and batches must leave the event version unchanged.**
- [ ] **Step 5: Commit as `feat: bind CARTS discovery to crypto data roles`.**

### Task 3: Run experiments through typed ports and independently verify promotion

**Files:**
- Modify: `src/quant_crypto/integrations/carts.py`
- Create: `src/quant_crypto/integrations/runner.py`
- Create: `src/quant_crypto/integrations/verifier.py`
- Test: `tests/integration/test_carts_execution.py`
- Test: `tests/integration/test_carts_recovery.py`

**Interfaces:**
- Consumes contract validation from Task 2, standalone `run_walk_forward` and `verify_run`, and EpiPilot `ExperimentRunner`, `EvidenceVerifier`, and `AgentService` interfaces.
- Produces `CryptoResearchComputer.measure(root: Path, contract: MeasurementContract) -> Measured` at `Quality.MEASUREMENT`, `CryptoExperimentRunner.run(proposal: ExperimentProposal, output: Path) -> Observation`, and `CryptoEvidenceVerifier.verify(proposal: ExperimentProposal, observation: Observation) -> Assessment`.
- `CryptoResearchComputer.measure` validates pinned roles and inputs, runs the deterministic protocol, and returns computed metrics/sample counts; it has no persistence or promotion authority. The typed `ExperimentRunner` persists the run artifact and `EvidenceVerifier` independently recomputes it.
- `CryptoExperimentRunner` resolves the canonical contract for the proposal, verifies its Git revision and all pinned hashes before running, writes a fresh strict JSON artifact at EpiPilot's reserved output path, and reports actual sample count and wall cost. It never writes exchange orders or credentials.
- `CryptoEvidenceVerifier` calls the standalone independent verifier against the just-written artifact and frozen inputs; it does not trust `Observation.metrics`, runner prose, or report fields. It returns verified development-scope evidence; `qualified_for_promotion` is true only for a complete F4 result passing all four target constraints, 730 observations in both windows, role/hash checks, base costs, and recorded 2x/4x sensitivities. Every other valid measurement leaves promotion false.
- `CartsRuntime.run_next() -> Observation | None` first inspects `AgentService.iterate()`, executes only `ready_to_execute` via `ResearchEngine.run_next(CryptoExperimentRunner, CryptoEvidenceVerifier)`, and reconciles questions only after settlement. `resume()` uses `ResearchEngine.resume(CryptoEvidenceVerifier)` and reconciles after successful settlement; recovery-required states return without relaunch.

- [ ] **Step 1: Write failing tests** named `test_computer_rechecks_hashes_and_role_manifest`, `test_runner_writes_proposal_bound_artifact`, `test_verifier_recomputes_instead_of_trusting_observation_metrics`, `test_target_failure_never_qualifies_for_promotion`, `test_only_passing_f4_development_scope_can_qualify`, `test_run_next_uses_agent_packet_and_typed_engine_ports`, `test_restarted_engine_resumes_persisted_observation_without_rerunning`, and `test_unrecorded_active_reservation_requires_explicit_recovery`.
- [ ] **Step 2: Run `pytest tests/integration/test_carts_execution.py tests/integration/test_carts_recovery.py -q` and confirm the host has no registered runner or verifier.**
- [ ] **Step 3: Implement the static Computer mapping, proposal-bound typed runner, independent evidence verifier, settlement/reconciliation cycle, and recovery-safe `run`/`resume` commands.**
- [ ] **Step 4: Run the integration tests using only synthetic BTC/ETH data, a temporary Git repo, and a temporary SQLite database; no web request or user workspace mutation is allowed.**
- [ ] **Step 5: Commit as `feat: run and verify crypto experiments through CARTS`.**

### Task 4: Document plugin operation and verify full host lifecycle

**Files:**
- Create: `docs/carts.md`
- Modify: `README.md`
- Test: `tests/integration/test_carts_lifecycle.py`

**Interfaces:**
- Consumes all host commands from Tasks 1–3.
- Documents optional installation into the existing EpiPilot Python environment, how to select the canonical database, how to initialize the new project with an explicit wall-seconds budget, how to iterate/discover/run/resume, what wall-time accounting includes, and why historical research remains development evidence.
- Documents the installed research skill path `epipilot-carts:epipilot-research` and requires the coding agent to use the current skill for semantic discovery and ten-check cumulative synthesis; the host does not spawn another agent or run as a daemon.

- [ ] **Step 1: Write `test_crypto_carts_project_completes_discovery_run_settlement_and_replay` using a synthetic discovery batch and temporary SQLite store.** Assert a separate project ID, target metrics, immutable manifest, verified settlement, replay-equivalent state, and no second event database.
- [ ] **Step 2: Run `pytest tests/integration/test_carts_lifecycle.py -q` and confirm an end-to-end host flow is incomplete.**
- [ ] **Step 3: Document only commands executed by the lifecycle test, including the mandatory `--wall-seconds` and resume/recovery paths.**
- [ ] **Step 4: Run all standalone tests and all integration tests in the EpiPilot environment; run `ruff format --check`, `ruff check`, and `mypy` on the new integration modules.**
- [ ] **Step 5: Commit as `docs: document quant crypto CARTS workflow`.**
