# Hermes frontend integration

EpiPilot can be loaded as a native Hermes plugin so Hermes provides the human-facing
conversation surface while EpiPilot retains canonical project state and control authority.

## Authority boundary

```text
Human
  |
  v
Hermes CLI / Gateway
  |
  |  /epipilot commands + bounded context projection
  v
EpiPilot Hermes frontend adapter
  |
  +--> profile-scoped pointer state (non-canonical)
  |
  v
EpiPilot SQLite EventStore
  |
  v
Replay -> ProjectState / ProjectContract
```

Hermes is an interface in this integration. It does not become the owner of requirements,
evidence, task state, verification, or completion. Conversation history remains non-canonical.
Explicit project-intake commands create typed EpiPilot events with user provenance.

The integration intentionally does **not** treat a Hermes response as evidence and does not
allow executor self-report to produce `PASSED`.

## Install into Hermes

Install EpiPilot into the same Python environment that runs Hermes:

```bash
python -m pip install -e .
hermes plugins list
```

EpiPilot publishes the `hermes_agent.plugins` entry point named `epipilot`. Restart Hermes
after installing or upgrading the package, then use:

```text
/epipilot start Implement the repository feature
/epipilot success pytest passes
/epipilot constrain do not modify generated files
/epipilot status
/epipilot exit
/epipilot resume <project-id>
```

`start`, `success`, and `constrain` are explicit operator actions and therefore may create
canonical `RequirementAdded` events. Ordinary chat messages do not silently mutate canonical
project state.

## Persistence

The Hermes state bridge stores only frontend bookkeeping such as the active and last project
IDs. Canonical events are stored separately in:

```text
<HERMES_HOME>/plugin-data/<epipilot-plugin-namespace>/epipilot-events.sqlite3
```

The database uses EpiPilot's existing `SqliteEventStore`. Project status is reconstructed by
`replay_project()` on every integration read rather than trusted from conversational memory or
a cached assistant summary.

The plugin resolves `ctx.state.data_dir` at operation time so Hermes profile isolation remains
the host responsibility instead of being frozen at plugin-registration time.

## Current execution gate

This first integration slice is deliberately interface-only.

While an EpiPilot project is active, the plugin's `pre_tool_call` hook blocks ordinary Hermes
tool execution. This is a fail-closed boundary, not a missing feature hidden behind prompting.
It prevents a frontend Hermes session from editing files or running commands outside EpiPilot's
task, scope, supervision, and verification contracts.

The next integration slice must add a `HermesExecutor` implementation of
`CodingAgentExecutor` and route execution through:

```text
ProjectRuntime
  -> TaskRuntime
  -> HermesExecutor
  -> executor observation
  -> independent VerifierPipeline
  -> PASSED | FAILED
```

Only after that adapter exists should the frontend gate permit execution for an authenticated
EpiPilot task. At that point `TaskContract` path/resource boundaries should be enforced before
Hermes tool execution, and the context supplied to the executor should come from EpiPilot's
Context Compiler.

## Failure behavior

The integration fails closed when:

- the profile-scoped active-project pointer is malformed;
- an active project has no event stream;
- replay rejects the event stream;
- the reconstructed project does not form a valid `ProjectContract`;
- an unknown project ID is requested for resume.

A canonical project stream is never deleted by `/epipilot exit`; that command changes only the
frontend pointer.
