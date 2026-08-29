# Codex Plugin Alpha

EpiPilot can be used as a Codex plugin that turns an interactive coding agent into an evidence-driven research frontend while keeping canonical project truth in EpiPilot.

## Why this is a plugin, not a prompt

The plugin is intentionally thin:

```text
User <-> Codex conversation / execution
              |
              v
      EpiPilot plugin skill
              |
              v
      CodexResearchBridge
              |
              v
 append-only canonical event state
```

Codex may propose observations, hypotheses, experiments, code changes, and completion claims. EpiPilot owns canonical requirements, decisions, unknowns, hypothesis state, evidence, and the next research frontier.

This preserves the core invariant:

```text
Canonical State != Conversation != Compiled Context
```

## Alpha vertical slice

This branch provides a first complete epistemic transition:

```text
ProjectContract
  -> UnknownRegistered
  -> HypothesisCreated(ACTIVE)
  -> experiment / measurement
  -> EvidenceRecorded
  -> HypothesisUpdated(SUPPORTED | REFUTED | ...)
  -> UnknownResolved
  -> next research directive
```

`HypothesisUpdated` and `UnknownResolved` fail closed for decisive transitions unless the referenced evidence is independently verified and is not merely `EXECUTOR_REPORT`. Codex cannot set that authority bit directly: the plugin admits verified evidence only through the shell-free `verify-command` adapter, which derives the result from a preregistered argv command.

The research-frontier policy returns only:

```text
ASK_USER
INVESTIGATE
USE_SAFE_DEFAULT
EXECUTE
SYNTHESIZE
```

It deliberately has no automatic `ACCEPT` transition. `SYNTHESIZE` means the open research frontier is exhausted; the project acceptance contract must still pass independently.

## Codex plugin layout

The repository root is the plugin root:

```text
.codex-plugin/plugin.json
skills/
  epistemic-research/
    SKILL.md
    scripts/
      epipilot_bridge.py
src/epipilot/
  integrations/codex/
    bridge.py
    cli.py
  research/
    contracts.py
    policy.py
```

The manifest follows Codex's plugin scaffold conventions and exposes the `epistemic-research` skill.

## Development use

Install EpiPilot's Python dependencies in the development environment:

```bash
python -m pip install -e '.[dev]'
```

Then from the target research repository:

```bash
python /path/to/EpiPilot/skills/epistemic-research/scripts/epipilot_bridge.py --help
```

The default durable state is:

```text
<target-repo>/.epipilot/events.sqlite3
```

Initialize a project:

```bash
python /path/to/EpiPilot/skills/epistemic-research/scripts/epipilot_bridge.py init --project-id query-collapse --goal "Explain and repair query-cloud policy collapse" --success "Fresh held-out evaluation verifies the repair" --constraint "Do not change evaluator semantics" --budget "1 GPU; bounded training episodes"
```

The bridge prints machine-readable JSON so Codex can use it without parsing conversational prose.

## Research policy

The deterministic policy applies the following precedence:

1. missing explicit success criterion -> ask the user;
2. blocking user-owned decision -> ask the user;
3. runnable canonical task -> execute it;
4. open user-owned unknown -> ask the user and resolve it from the recorded user decision;
5. reversible safe-default unknown -> record the system decision and resolve it;
6. open technical unknown -> investigate the highest-impact, highest decision-weighted information-value item;
7. otherwise -> synthesize and run acceptance.

Open technical unknowns are ranked by:

```text
impact class
then value_of_information * decision_sensitivity
then stable unknown id
```

The policy decides *what kind of work is needed*, not the scientific answer. Experiment design remains constrained by preregistered predictions, falsification criteria, budget, and independent evidence.

## Security and truth boundary

The plugin must not:

- promote Codex self-report to verified evidence;
- resolve unknowns from unverified observations;
- support or refute hypotheses using executor-report evidence;
- use conversation history as the canonical project ledger;
- silently retry identical failed experiments;
- automatically declare project acceptance.

`observe` always records unverified executor-side evidence. `verify-command` runs an argv command with `shell=False`, derives the result from its exit status, suppresses raw stdout/stderr, and is the only alpha CLI path that records independently verified evidence.

The bridge validates every event by reducing it against current canonical state before persistence, so an illegal epistemic transition cannot poison the durable event stream.

## Current limitations

This is an alpha vertical slice, not yet the full autonomous-research runtime.

Still to add:

- first-class persisted `ExperimentContract` events and artifact references;
- Codex MCP transport so the plugin does not depend on a local Python bridge command;
- automatic task creation from selected experiments;
- evidence-driven `LOCAL_PATCH / SUBGRAPH_REPLAN / GLOBAL_REPLAN`;
- project-level acceptance integration;
- crash-safe resume/reconciliation for interactive Codex sessions;
- context manifests for every Codex research turn.

These should build on the same canonical state rather than introducing a parallel plugin-specific truth store.
