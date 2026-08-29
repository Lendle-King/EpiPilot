---
name: epistemic-research
description: Use EpiPilot for open-ended coding, systems, ML, or research work that benefits from durable unknowns, falsifiable hypotheses, preregistered experiments, independently verified evidence, belief updates, and evidence-driven replanning. Ask the user only for genuinely user-owned decisions.
---

# EpiPilot Epistemic Research

Use EpiPilot as the canonical research control layer. Conversation history and executor self-report are not project truth.

## Start or resume

Call `epipilot_info` first. It returns the durable event-store path and verifies that the MCP bridge is live.

Then call `epipilot_list_projects`:

- resume an existing project when it clearly matches the user's current research task;
- otherwise create one with `epipilot_start_project` after obtaining a meaningful goal and at least one observable success criterion.

Keep hard constraints, budgets, and forbidden actions explicit. Do not ask the user to answer technical questions that can be investigated safely.

## Core loop

Repeat:

```text
canonical state
  -> epipilot_next
  -> ask_user | investigate | run_experiment | execute | use_safe_default | synthesize
  -> action / experiment
  -> observation
  -> independent verification when decisive
  -> evidence / belief update / resolution
  -> epipilot_next
```

### `ask_user`

Ask only the returned blocking question. Record the answer with `epipilot_record_decision`, then resolve the matching user-owned unknown with `epipilot_resolve_unknown` using the decision id.

### `use_safe_default`

Use only a low-risk reversible default. Record it as a reversible `system` decision before resolving the unknown. Never silently default a high-impact user-owned choice.

### `investigate`

The technical unknown needs a new discriminative experiment. If necessary:

1. register the unknown with `epipilot_register_unknown`;
2. preregister competing falsifiable hypotheses with `epipilot_preregister_hypothesis`;
3. create exactly one bounded, decision-relevant experiment with `epipilot_preregister_experiment`.

The experiment must state controlled variables, measurements, a prediction and falsification condition for every hypothesis, a decision rule, and a budget. Prefer the cheapest experiment that could change the current decision.

After preregistration, call `epipilot_next` again. It should normally return `run_experiment` for that experiment rather than inventing another one.

### `run_experiment`

Read the experiment from `epipilot_get_state` and execute exactly its preregistered contract. Do not silently change the measurement, threshold, dataset split, evaluator, budget, or falsification rule after seeing results.

Record executor-side interpretation with `epipilot_record_observation` when useful. This is intentionally unverified and cannot by itself support/refute a hypothesis or close a technical unknown.

For decisive deterministic evidence, use the normal Codex shell so sandbox/approval rules still apply. First read `event_store_path` from `epipilot_info`, then run:

```bash
epipilot-codex --db "<event_store_path>" verify-command \
  --project-id "<project-id>" \
  --name "<preregistered-check-name>" \
  --scope "<revision/task/experiment scope>" \
  --cwd "<target workspace>" \
  <argv...>
```

Examples of suitable checks are a focused pytest target, benchmark acceptance script, deterministic metric checker, or reproducible probe command. `verify-command` uses argv execution rather than a shell and records only the derived result metadata, not raw stdout/stderr.

Use the returned verified evidence id to:

1. call `epipilot_conclude_experiment`;
2. update hypotheses with `epipilot_update_hypothesis`;
3. resolve the technical unknown with `epipilot_resolve_unknown` when the evidence is decision-sufficient.

`SUPPORTED`, `REFUTED`, experiment conclusion, and technical-unknown resolution fail closed when decisive evidence is not independently verified.

### `execute`

Execute the returned canonical task using its existing task/verification contract. Executor completion is never project acceptance.

### `synthesize`

`SYNTHESIZE` means the current action/unknown frontier is exhausted; it is not automatic success. Every `SYNTHESIZE` directive carries a `synthesis_contract`. Treat every required dimension in that contract as a completion gate.

Before claiming the goal is complete:

1. call `epipilot_get_state` again and reason from canonical requirements, hypotheses, experiments, evidence, decisions, and remaining unknowns;
2. assess the requested outcome against the canonical goal and success criteria;
3. characterize the **nature of the subject under this goal**, not as an encyclopedia entry: identify the problem/system/phenomenon class, dominant structural or causal properties, constraints, invariants, boundary conditions, and tradeoffs that matter to achieving the goal;
4. reconstruct the **hypothesis landscape**: competing explanations considered, supported/refuted/inconclusive/active hypotheses, confidence, decisive evidence, and plausible alternatives;
5. distinguish verified facts from causal inference and from post-hoc candidate hypotheses;
6. if synthesis exposes a new decision-relevant unknown that could change acceptance or the recommended action, register it and resume the research loop instead of declaring completion.

The final report must cover all eight goal-conditioned dimensions returned by `synthesis_contract`:

1. **Outcome against goal** — what was achieved, what criterion proves it, and what was not achieved.
2. **Subject nature** — what this subject is *for the current goal*: its relevant structure, mechanisms, constraints, invariants, tradeoffs, and boundary conditions.
3. **Hypothesis landscape** — material competing hypotheses and their current status/confidence; explicitly label untested post-hoc hypotheses rather than upgrading them to supported.
4. **Causal or structural explanation** — the best evidence-bounded mechanism explaining why the result occurred.
5. **Decisive evidence** — experiments/checks that changed belief or decision, with provenance.
6. **Uncertainty and alternatives** — unresolved uncertainty, accepted uncertainty, and still-plausible alternative explanations.
7. **Generalization boundaries** — where the current understanding should and should not be expected to hold.
8. **Reopen conditions** — observations, regressions, distribution shifts, or failed assumptions that should reopen investigation.

The report is explicitly **goal-conditioned**. Do not claim an absolute theory of the subject when the evidence only establishes properties relevant to the current objective.

Run the project's independent acceptance contract before claiming completion. Goal completion requires both an accepted outcome and the mandatory epistemic synthesis above.

## Invariants

- Never treat chat history as canonical state.
- Never turn executor self-report into verified evidence.
- Never support/refute a hypothesis from unverified evidence.
- Never resolve a technical unknown without independently verified evidence.
- Never retry an unchanged failed experiment; require new information, strategy, or environment.
- Never global-replan when a local correction is sufficient.
- Never use EpiPilot's MCP server as a hidden arbitrary-command executor; run experiments and verifiers through Codex's normal shell/tool boundary.
