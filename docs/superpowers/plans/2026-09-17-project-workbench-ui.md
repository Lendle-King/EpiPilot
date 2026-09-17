# Project Workbench UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Deliver a real-data, project-first local workbench with grounded explanations.

**Architecture:** Repository and read-only event adapters feed a typed projection;
FastAPI serves same-origin static UI and read-only endpoints. The UI cannot mutate
canonical state or invoke an executor.

**Tech Stack:** Python 3.11+, Pydantic, optional FastAPI/Uvicorn, vanilla ES modules,
pytest/HTTPX, Chromium/Playwright.

**Spec:** `docs/superpowers/specs/2026-09-17-project-workbench-ui.md`

## Global Constraints

- Keep canonical core, verifier, and executor contracts unchanged.
- No task or project passes from executor self-report.
- All evidence is scoped; missing revision links remain unknown.
- Expose only GET operations. Bind to loopback. Never use shell=True.
- Do not depend on a frontend build or remote CDN.

## Task 1 — revision-pinned repository reader

Files: `src/epipilot/web/repository.py`, `src/epipilot/web/models.py`,
`tests/test_web_repository.py`.
Inputs: local Git root. Output: `RepositoryView` with revision, dirty state, documents,
modules, inventory limits, and static-analysis caveats.

- [x] Add temporary Git repository tests; modify README after commit and assert
  `inspect().documents[0].text` still contains committed content.
- [x] Observe RED from absent web modules before implementation.
- [x] Implement `RepositoryInspector.inspect()` with bounded subprocess argv calls,
  commit pinning, mode filtering, and cached immutable metadata.
- [x] Verify committed/dirty content distinction, symlink/credential exclusion, no-commit
  failure, and visible scan count limits in local focused tests.

## Task 2 — typed project projection and real event reader

Files: `src/epipilot/web/projection.py`, `src/epipilot/web/events.py`,
`src/epipilot/web/service.py`, `tests/test_web_projection.py`, `tests/test_web_events.py`.
Inputs: `ProjectState | None`, `RepositoryView`, event envelope metadata.
Output: `ProjectView`; evidence-linked search, Markdown report, proposal-only handoff.

- [x] Add tests for reported-done vs passed, missing independent evidence, and
  acceptance=not_assessed even when tasks pass.
- [x] Implement `build_project_view`; expose scope and time, not raw logs.
- [x] Add real SQLite/replay tests, including wrong aggregate, version gaps and schema.
- [x] Implement `load_stream(path, project_id)` using SQLite URI mode=ro and replay.
- [x] Verify selecting codex/pi/dsh changes only an exported proposal, not canonical tasks.
- [ ] Verify state integration against the complete upstream repository in GitHub CI.

## Task 3 — read-only application and accessible UI

Files: `src/epipilot/web/app.py`, `src/epipilot/web/__main__.py`,
`src/epipilot/web/static/{index.html,app.js,styles.css}`, `tests/test_web_api.py`,
`tests/test_web_browser.py`, `tests/test_web_cli.py`, `docs/UI.md`, `pyproject.toml`.

- [x] Verify GET project/report/proposal/search and rejection of writes/foreign origins.
- [x] Implement app factory and module CLI with loopback binding and optional dependencies.
- [x] Build five tabs, empty/error states, detail dialog, search, proposal selection/export.
- [x] Run 18 focused repository/API/CLI tests locally, plus JS syntax and offline
  Chromium DOM/rendering checks using synthetic repository fixtures.
- [ ] Run live HTTP browser tests and complete formatting/lint/type/test CI.
- [ ] Publish a focused PR stacked on executor PR #7 with exact verification results.

Local browser HTTP navigation is blocked by the environment's administrator policy;
offline DOM checks are not represented as live end-to-end verification. GitHub Actions
runs the live HTTP browser tests without weakening existing quality gates.
