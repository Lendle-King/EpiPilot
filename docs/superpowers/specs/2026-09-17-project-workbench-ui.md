# Project cognition workbench — approved design slice

## Goal and scope

Implement the project-first UI approved in the conversation: Overview, Understanding,
Delivery, Opportunities, and Evidence. EpiPilot remains the canonical project controller;
the browser is a read model, not a second source of truth or a coding-agent terminal.

The first deliverable is a working local web application over a real Git repository and,
optionally, an existing EpiPilot SQLite event stream. Without a stream, the UI explicitly
shows missing project goals/execution data instead of manufacturing progress. Repository
inspection is static and revision-pinned; it never runs project code. All generated
suggestions and exported task handoffs are proposals, not facts or authorized execution.

## Architecture

Python 3.11+; optional FastAPI/Uvicorn web extra; packaged HTML, CSS, and ES modules,
without a Node build requirement or external CDN. Domain modules remain unchanged.
`RepositoryInspector` reads committed source/doc metadata. `load_stream` reads SQLite
with mode=ro, validates contiguous versions, and delegates to the existing replay kernel.
`build_project_view` is a typed, deterministic projection. `WorkbenchService` joins the
sources, builds grounded search results and Markdown reports. HTTP exposes GET only.

## UI and semantics

- Five keyboard-accessible tabs, responsive project sidebar, current-version header,
  project summary, acceptance gaps, next-step rationale, and evidence detail dialog.
- Distinguish documented intent, static inspection, unverified hypotheses, independent
  evidence, and potentially stale conclusions. Do not display made-up probabilities.
- Count verified tasks separately; never turn task ratios into project completion.
  Requirement-level acceptance remains NOT ASSESSED until explicit requirement proofs
  exist. Historical evidence never silently proves the currently inspected Git HEAD.
- Keep unknowns/hypotheses as investigation candidates, not an endless mandatory backlog.
- Search is local, source-linked retrieval, explicitly not an LLM assistant.
- Codex/Pi/DSH selection changes the exported proposal only. Browser execution, live
  steering/resume, deployment, merging, and approval are unavailable in this slice.
- Real source mode is the default. No synthetic demo activity in normal operation.

## Safety and operation

Bind only 127.0.0.1/localhost; validate Host and Origin/Fetch Metadata, disable CORS,
serve no user-supplied HTML, use CSP and textContent, reject mutating HTTP methods.
Do not expose arbitrary files, credentials, raw executor transcripts, or artifact paths
as download endpoints. Git paths and blob reads are bounded, tracked, and pinned to a
validated commit. Symlinks, submodules, and credential-like paths are excluded.
This is a local single-user reader, not a production authorization or sandbox boundary.
No API keys are needed or embedded. No model invocation, package installation, or
repository write is triggered by opening the UI or generating a task handoff.

## Verification and limits

Tests cover repository pinning, dirty trees, exclusions, source limits,
status/verification separation, unknown data, SQLite gaps and unsupported schemas, API safety,
proposal semantics, and actual Chromium navigation/search/export/mobile layout.
Existing suite must remain green in CI. Browser inspection and local focused tests are
separate from live coding-agent/model integration, which is not claimed.

## Primary references checked 2026-09-17

- https://linear.app/docs/project-overview — project summary and progressive disclosure.
- https://www.w3.org/WAI/ARIA/apg/patterns/tabs/ — tab semantics and keyboard handling.
- https://fastapi.tiangolo.com/tutorial/static-files/ — packaged static mounting.
- https://fastapi.tiangolo.com/tutorial/testing/ — HTTP contract tests.
- https://www.starlette.io/middleware/ — trusted hosts and middleware boundaries.
- https://playwright.dev/python/docs/test-assertions — browser verification.
