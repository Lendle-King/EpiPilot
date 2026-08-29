# EpiPilot Codex Plugin

EpiPilot can be installed as a Codex Agent Plugin that keeps research state outside the conversation and exposes a bounded MCP control surface.

## What the initial usable plugin provides

```text
User <-> Codex
          |
          +-- epistemic-research skill
          |
          +-- EpiPilot MCP tools
                  |
                  v
          append-only SQLite event store
                  |
        Requirements / Decisions
        Unknowns / Hypotheses
        Preregistered Experiments
        Evidence / Task state
```

The current plugin can:

- create and resume durable research projects;
- keep explicit user-owned decisions separate from system-resolvable technical unknowns;
- preregister falsifiable hypotheses and bounded discriminative experiments;
- return a deterministic next action: `ask_user`, `investigate`, `run_experiment`, `execute`, `use_safe_default`, or `synthesize`;
- record executor observations without promoting them to verified truth;
- admit deterministic independent evidence through the shell-safe `epipilot-codex verify-command` adapter;
- update hypotheses, conclude experiments, and resolve unknowns only when authority/evidence requirements pass;
- survive new Codex threads because canonical state is reconstructed from the plugin event store.

It deliberately does **not** provide automatic project acceptance or an arbitrary-command MCP tool.

## Requirements

- Python 3.11+
- a recent Codex build with `codex plugin` support
- `epipilot-mcp` available on the PATH visible to Codex

## Install from the current development branch

### 1. Install the EpiPilot runtime

Preferred with `uv`:

```bash
uv tool install "git+https://github.com/Lendle-King/EpiPilot.git@feat/codex-epistemic-research-plugin"
```

If the `uv` tool bin directory is not already on PATH:

```bash
uv tool update-shell
```

Alternatively:

```bash
python -m pip install "git+https://github.com/Lendle-King/EpiPilot.git@feat/codex-epistemic-research-plugin"
```

Verify that the executable Codex will launch is available:

```bash
epipilot-mcp --self-check
```

### 2. Add this repository as a Codex plugin marketplace

```bash
codex plugin marketplace add Lendle-King/EpiPilot --ref feat/codex-epistemic-research-plugin
```

Confirm it is visible:

```bash
codex plugin marketplace list
```

### 3. Install the plugin

```bash
codex plugin add epipilot@epipilot
```

Confirm installation:

```bash
codex plugin list
```

Start a **new Codex thread** after installation so the plugin manifest, skill catalog, and MCP tools are loaded from a clean session.

## Install after this branch is merged to `main`

The commands simplify to:

```bash
uv tool install "git+https://github.com/Lendle-King/EpiPilot.git"
codex plugin marketplace add Lendle-King/EpiPilot
codex plugin add epipilot@epipilot
```

Then start a new Codex thread.

## First use

A good first prompt is:

```text
Use EpiPilot to investigate why this project is failing.
First make the goal, observable success criteria, hard constraints and budget explicit.
Ask me only for genuinely user-owned decisions. Treat technical uncertainty as unknowns,
form competing falsifiable hypotheses, preregister the cheapest discriminative experiment,
verify decisive results independently, and keep going until the remaining uncertainty is
not decision-relevant. Finish with the verified result and an epistemic map.
```

Codex should first call `epipilot_info`, then `epipilot_list_projects`, and either resume a clearly matching project or create a new one.

## MCP tools

The plugin exposes these bounded tools:

```text
epipilot_info
epipilot_list_projects
epipilot_start_project
epipilot_get_state
epipilot_next
epipilot_register_unknown
epipilot_preregister_hypothesis
epipilot_preregister_experiment
epipilot_record_decision
epipilot_record_observation
epipilot_conclude_experiment
epipilot_update_hypothesis
epipilot_resolve_unknown
```

The MCP server does not expose `run_command`, shell access, or a way to set `independently_verified=true` directly.

## Independent verification

Experiments themselves run through Codex's ordinary tools/shell, so normal Codex sandbox and approval rules continue to apply.

For a decisive deterministic check:

1. call `epipilot_info` and read `event_store_path`;
2. execute the preregistered verifier through the normal Codex shell:

```bash
epipilot-codex --db "<event_store_path>" verify-command \
  --project-id "<project-id>" \
  --name "<check-name>" \
  --scope "<revision/task/experiment scope>" \
  --cwd "<target workspace>" \
  python -m pytest <focused-target>
```

3. use the returned evidence id with `epipilot_conclude_experiment`, `epipilot_update_hypothesis`, and—when decision-sufficient—`epipilot_resolve_unknown`.

`verify-command` uses argv execution with `shell=False`, derives evidence from the process result, and does not persist raw stdout/stderr.

## Persistence

When launched as a Codex Agent Plugin, `mcp.json` sets the server working directory to `${PLUGIN_DATA}`. EpiPilot therefore defaults to:

```text
${PLUGIN_DATA}/events.sqlite3
```

Codex allocates plugin data separately from the cached plugin source. Reinstalling/upgrading the plugin does not make conversation history canonical state; projects are recovered from the append-only event stream.

For manual development outside the plugin, set:

```bash
EPIPILOT_DB=/absolute/path/to/events.sqlite3 epipilot-mcp
```

or use the CLI `--db` option.

## Research frontier

The deterministic policy uses this precedence:

1. missing explicit success criterion -> `ask_user`;
2. blocking user-owned decision -> `ask_user`;
3. runnable canonical task -> `execute`;
4. open user-owned unknown -> `ask_user`;
5. reversible safe-default unknown -> `use_safe_default`;
6. highest-priority technical unknown with a pending preregistered experiment -> `run_experiment`;
7. technical unknown without a pending experiment -> `investigate`;
8. otherwise -> `synthesize` and run project acceptance separately.

This prevents repeated redesign of the same experiment and prevents research-frontier exhaustion from becoming automatic success.

## Update the plugin

For a Git marketplace, refresh its snapshot, reinstall the plugin, and start a new thread:

```bash
codex plugin marketplace upgrade epipilot
codex plugin add epipilot@epipilot
```

Update the Python runtime separately when EpiPilot code changes:

```bash
uv tool install --force "git+https://github.com/Lendle-King/EpiPilot.git@feat/codex-epistemic-research-plugin"
```

Then run `epipilot-mcp --self-check` again.

## Troubleshooting

### `epipilot-mcp` not found

The Python package executable is not on the PATH inherited by Codex. Run:

```bash
epipilot-mcp --self-check
```

from the same environment that starts Codex. With `uv`, `uv tool update-shell` usually fixes the PATH.

### `codex plugin` is unavailable

Use a recent Codex build with Plugins support. Older builds cannot consume the marketplace/Agent Plugin layout.

### MCP tools are missing after install/update

Start a new Codex thread. Plugin changes are not guaranteed to refresh an already-running thread.

### Need to inspect durable state

Ask Codex to call `epipilot_info`, `epipilot_list_projects`, and `epipilot_get_state`. Do not recover state from an old transcript.

## Current limitations

This is an initial usable research plugin, not EpiPilot V1.0. Remaining gaps include:

- project-level acceptance is not yet wired into the MCP research frontier;
- evidence-driven `LOCAL_PATCH / SUBGRAPH_REPLAN / GLOBAL_REPLAN` is not yet automatic;
- experiment artifacts are referenced through evidence summaries rather than a complete first-class artifact store;
- SQLite is a single-machine persistence backend, not a multi-process server store;
- task/worktree enforcement and crash reconciliation remain broader EpiPilot roadmap items.

These limitations are explicit so the plugin can be useful without pretending that open-ended autonomous research is already solved.
