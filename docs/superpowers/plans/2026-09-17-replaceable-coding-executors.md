# Replaceable Coding Executors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configuration-driven, replaceable Pi/Codex/DSH coding-agent executors without coupling EpiPilot project logic to any backend.

**Architecture:** Keep `CodingAgentExecutor` as the stable port. Add a registry/config layer and two one-shot JSONL adapters (`codex exec --json --ephemeral`, `dsh --profile headless --json`) sharing subprocess lifecycle code. Executor terminal reports remain non-authoritative and flow through the existing independent verifier.

**Tech Stack:** Python 3.11+, dataclasses, asyncio subprocesses, JSONL, pytest/pytest-asyncio, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-17-replaceable-coding-executors-design.md`

## Global Constraints

- Do not change `TaskRuntime`'s dependency on the `CodingAgentExecutor` protocol.
- Executor self-report must never directly produce `TaskStatus.PASSED`.
- Built-in backend names are exactly `pi`, `codex`, and `dsh`.
- Unknown executors must fail closed; never silently fall back to another agent.
- External processes must be launched without a shell and must be cleaned up on `terminate`.
- Pi's existing RPC behavior and tests must remain compatible.
- No mandatory new runtime Python dependency is introduced for Codex or DSH.

---

### Task 1: Executor selection registry

**Files:**
- Create: `tests/test_executor_registry.py`
- Create: `src/epipilot/executors/registry.py`

**Interfaces:**
- Produces: `ExecutorConfig`, `ExecutorRegistry`, `default_executor_registry()`, `create_executor()`.
- Consumes: `CodingAgentExecutor`, `PiRpcExecutor`; later tasks provide `CodexExecExecutor` and `DshHeadlessExecutor`.

- [ ] **Step 1: Write failing registry tests**

Cover validation of empty backend names, default names `("codex", "dsh", "pi")`, duplicate registration rejection, custom registration, and concrete built-in type selection.

- [ ] **Step 2: Verify RED in CI**

Commit tests only and confirm GitHub Actions fails because `epipilot.executors.registry` does not yet exist.

- [ ] **Step 3: Implement minimal registry/config**

Use a frozen/slotted dataclass for `ExecutorConfig`; normalize backend names with `strip().lower()` at lookup time but reject empty values. `ExecutorRegistry` stores factories by explicit name and returns names sorted for deterministic errors/tests.

- [ ] **Step 4: Verify targeted tests and type/lint checks in CI**

Expected: registry tests pass after backend modules from Tasks 2-3 are present; until then imports may remain intentionally red.

---

### Task 2: Shared one-shot JSONL subprocess lifecycle and Codex executor

**Files:**
- Create: `tests/test_codex_exec_executor.py`
- Create: `src/epipilot/executors/jsonl_process.py`
- Create: `src/epipilot/executors/codex_exec.py`

**Interfaces:**
- `JsonlProcessExecutor` implements `CodingAgentExecutor` lifecycle and calls backend hooks for record/EOF interpretation.
- `CodexExecExecutor(command=("codex", "exec", "--json", "--ephemeral"), cwd=None, shutdown_timeout_seconds=5.0)`.

- [ ] **Step 1: Write failing Codex tests**

Use `sys.executable -u -c <script>` as command override. One fixture emits `turn.completed`; one emits `turn.failed`; one emits malformed JSON. Assert only the completed stream maps to `REPORTED_DONE` and summaries state that independent verification is still required.

- [ ] **Step 2: Verify RED**

Run/observe CI failure due to missing `CodexExecExecutor`.

- [ ] **Step 3: Implement shared process lifecycle**

The helper validates command/context, builds the prompt as `<context>\n\n[Current task]\n<objective>`, starts subprocess with `stdin=DEVNULL`, drains stderr, parses one JSON object per stdout line, and performs bounded terminate-then-kill cleanup.

- [ ] **Step 4: Implement Codex event interpretation**

`turn.completed` => `REPORTED_DONE`; `turn.failed` or `error` => `FAILED`; malformed JSON/non-object/EOF without terminal success => `FAILED`. Unknown events only update a non-authoritative progress summary.

- [ ] **Step 5: Verify GREEN**

Targeted Codex tests must pass; existing Pi tests must still pass.

---

### Task 3: DSH headless executor

**Files:**
- Create: `tests/test_dsh_headless_executor.py`
- Create: `src/epipilot/executors/dsh_headless.py`
- Modify: `src/epipilot/executors/registry.py`

**Interfaces:**
- `DshHeadlessExecutor(command=("dsh", "--profile", "headless", "--json"), cwd=None, shutdown_timeout_seconds=5.0)`.
- Reuses `JsonlProcessExecutor`.

- [ ] **Step 1: Write failing DSH tests**

Fixtures: `final` then exit 0; `final` then exit 1; `error`; EOF without `final`. Assert only `final` + zero exit maps to `REPORTED_DONE`.

- [ ] **Step 2: Verify RED**

Confirm missing DSH adapter causes expected failure.

- [ ] **Step 3: Implement DSH event/exit semantics**

Remember whether a `final` event was observed. Do not mark done until stdout closes and subprocess exit status is known. Exit 0 + final => `REPORTED_DONE`; all other terminal combinations => `FAILED`.

- [ ] **Step 4: Wire built-ins into the default registry**

Factories must preserve `cwd`, optional command override, and shutdown timeout. Default command overrides are owned by each adapter.

- [ ] **Step 5: Verify GREEN**

Run all executor tests and full CI.

---

### Task 4: Documentation and backend-selection examples

**Files:**
- Modify: `README.md`
- Modify: `docs/FRAMEWORK.md`

**Interfaces:**
- Document construction through `ExecutorConfig` + `create_executor`.

- [ ] **Step 1: Add README example**

```python
from epipilot.executors.registry import ExecutorConfig, create_executor

executor = create_executor(ExecutorConfig(backend="codex", cwd=workspace))
```

Show `backend="pi"` and `backend="dsh"` as interchangeable choices.

- [ ] **Step 2: Clarify executor boundary**

State explicitly that Codex, Pi, and DSH are replaceable execution runtimes and never own canonical hypothesis/evidence/project truth.

- [ ] **Step 3: Verify docs do not claim external executables are bundled**

State prerequisites clearly: the selected agent executable must be installed/configured; command overrides support custom launch paths.

---

### Task 5: Final verification

**Files:** no new production files.

- [ ] **Step 1: Run GitHub CI on the final branch commit**

Required checks: formatting, lint, mypy, pytest on all configured Python versions/jobs.

- [ ] **Step 2: Inspect failures rather than trusting the implementation summary**

If CI fails, use job logs to fix the actual failure and rerun.

- [ ] **Step 3: Review branch diff against spec**

Confirm: no main-branch writes, no executor fallback, no verifier bypass, no new required runtime dependency, and no regression to Pi behavior.

- [ ] **Step 4: Open a pull request**

PR should summarize the stable selection interface, backend semantics, test evidence, and remaining limitation: first Codex/DSH adapters are one-shot headless sessions; richer steering/resume can be added behind the same port later.
