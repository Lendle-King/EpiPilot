# Research Workspace UI (MVP)

EpiPilot has a local-first research dashboard for new projects and for adopting existing Git projects. It is a real interface backed by a Python API and SQLite staging data, not a finished unattended autonomous scientist.

## Install and run

Requires Python 3.11+, Git and Node 22+.

    python -m pip install -e '.[ui]'
    python -m epipilot.workspace.api

In a separate terminal:

    cd frontend
    npm install
    npm run dev

Visit http://127.0.0.1:5173. The frontend proxies API calls to http://127.0.0.1:8787. The API binds only to loopback.

Default private database: ~/.local/share/epipilot/workspace.sqlite3

Optionally set EPIPILOT_WORKSPACE_DB to an alternate SQLite path. EPIPILOT_ALLOWED_ROOTS is an OS-path-separated list of directories from which the API may adopt local Git repos (use colon on Linux/macOS and semicolon on Windows). The default root is the current user's home directory.

## New project

Use Add project > Start new. Provide a name and explicit research goal. The backend creates a staging project and records a project_created audit event. This does not silently initialize Git, write a remote repository or fabricate scientific evidence.

## Adopt existing research

Use Add project > Adopt existing. Supply the absolute path to a local Git repository. This operation is deliberately read-only and offline.

1. Validate that the resolved Git root is within allowed directories.
2. Pin its Git commit SHA and branch; never read uncommitted working tree files as historical evidence.
3. Scan up to 80 tracked README/docs source files from the immutable commit, and extract up to 220 heuristic candidate observations and lessons.
4. Record source file, line, Git SHA and source content hash for every candidate.
5. Review each extracted candidate. Accepted notes are only useful context; they are NOT qualified independent Evidence or canonical Fact. Rejections and acceptances are append-only workspace audit events.
6. Inspect the overview, evidence inbox, source lineage, Git status and research timeline.

The extraction is lexical/heuristic, not semantic LLM synthesis. It can miss research conclusions or return unimportant sentences. Real research quality requires later reasoning against full experiment logs and independent source verification. No private/untracked/secret data should be presented to a model without user review.

## Pages

- Overview: project goal, counts, recent observations.
- Observations & knowledge: reviewed versus unreviewed research findings with source references.
- Research graph: file-to-observation lineage, not a causal or hypothesis DAG.
- Local Git workspace: current HEAD/branch, tracked changed files and worktree count, read-only.
- Timeline: immutable onboarding, note review and user intention records.
- Research Copilot: record human-intent proposals and copy a source-grounded research brief for ordinary ChatGPT Chat. It is NOT a direct ChatGPT API nor an embedded LLM.

Github/Notion remote operations remain delegated to ChatGPT using its authorized tools. This application never calls their APIs or performs trades.

## Trust and roadmap

The separate WorkspaceStore SQLite database is a staging UI read model, NOT EpiPilot's canonical project event store. A reviewed note must never automatically create a passed task or verified fact. Integration into ProjectContract, Evidence, Hypothesis, Task DAG and replayable canonical events requires an explicit, tested promotion workflow.

The next milestones are scientific evidence reconciliation, experiment/result contracts, user-approved decision changes, and a bounded autonomous research supervisor. Treat Work or ChatGPT web automation as an independent supported-capability question.

## Security

The API currently has no authentication and must remain bound to 127.0.0.1. Do not expose ports 8787/5173 to a LAN or the Internet. Restrict EPIPILOT_ALLOWED_ROOTS to trusted repositories. The credential-line filter is not a complete secret scanner, so review copied content. SQLite and local Git are visible to the local OS user only to the extent permitted by filesystem permissions.

## Validation

    ruff format --check .
    ruff check .
    mypy src
    pytest -q tests/test_workspace_onboarding.py
    cd frontend && npm run build

All functionality remains user-initiated and does not scrape or automatically drive ordinary ChatGPT chat.
