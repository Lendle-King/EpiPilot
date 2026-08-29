---
name: epistemic-research
description: Use EpiPilot to turn an open-ended coding or systems problem into an auditable evidence-driven research loop. Use when the user wants Codex to clarify only necessary user-owned decisions, explore technical unknowns autonomously, preregister competing hypotheses, run discriminative experiments, update beliefs from independently verified evidence, replan, and finish with both a verified result and a clear epistemic map.
---

# EpiPilot Epistemic Research

Use EpiPilot as the canonical control layer. Do not treat this skill, conversation history, or your own self-report as project truth.

## Core loop

```text
Goal / Success Criteria / Constraints
  -> Decision Frontier
  -> Unknowns
  -> Falsifiable Hypotheses
  -> Minimum Discriminative Experiment
  -> Execute
  -> Observe
  -> Independent Verify
  -> Evidence
  -> Hypothesis Update / Unknown Resolution
  -> Replan
  -> Acceptance + Epistemic Report
```

## Non-negotiable behavior

1. Ask the user only for value, policy, budget, irreversible-risk, major-scope, or otherwise user-owned decisions that cannot be safely inferred or cheaply tested.
2. Convert technical uncertainty into canonical `Unknown` items rather than asking the user to diagnose it.
3. Before an experiment, preregister hypotheses, observable predictions, falsification conditions, measurements, controlled variables, a decision rule, and a bounded budget.
4. Prefer the cheapest experiment that can change the current decision. Do not enumerate or run every plausible branch.
5. An executor statement is not independently verified evidence. Use `observe` for executor-side findings and `verify-command` for decisive deterministic checks.
6. Never mark a hypothesis `SUPPORTED` or `REFUTED`, or resolve a technical unknown, from unverified executor evidence.
7. Never declare the project complete directly from this loop. When research is exhausted, synthesize the epistemic map and run the project's independent acceptance contract.
8. Do not retry the same failed experiment unchanged. Require new information, a changed strategy, or a materially changed environment.

## Durable bridge

From this skill directory, use:

```bash
python3 scripts/epipilot_bridge.py --help
```

The bridge defaults to the target workspace's:

```text
.epipilot/events.sqlite3
```

Run it from the target repository root so canonical research state stays with that workspace.

If the bridge cannot import EpiPilot dependencies, do not silently emulate durable state in chat. Explain that the source-tree plugin requires the EpiPilot Python dependencies and install them only when authorized.

## Project intake

Collect a meaningful goal and explicit success criteria before autonomous execution. Also capture hard constraints, budgets, and forbidden actions when supplied.

Initialize canonical state:

```bash
python3 <skill-root>/scripts/epipilot_bridge.py init --project-id <stable-id> --goal "<goal>" --success "<observable success criterion>" --constraint "<hard constraint>" --budget "<bounded budget>" --forbidden "<forbidden action>"
```

Use repeated flags when there are multiple items.

Do not keep asking questions merely because implementation details are unknown. Once remaining uncertainty is system-resolvable, proceed.

## User decisions and safe defaults

When an open unknown is user-owned, ask only that blocking question. Record the answer as a canonical user decision, then resolve the unknown using the returned decision id.

```bash
python3 <skill-root>/scripts/epipilot_bridge.py decision --project-id <stable-id> --question "<question>" --choice "<user answer>" --rationale "<why it matters>" --authority user
```

For a `use_safe_default` directive, use only an already-authorized reversible default. Record it as a reversible system decision, then resolve the unknown with `--decision-id`. Do not invent a default for a high-impact user-owned choice.

```bash
python3 <skill-root>/scripts/epipilot_bridge.py decision --project-id <stable-id> --question "<question>" --choice "<safe reversible default>" --rationale "<why this default is authorized>" --authority system
```

```bash
python3 <skill-root>/scripts/epipilot_bridge.py resolve --project-id <stable-id> --unknown-id <id> --decision-id <decision-id>
```

## Investigate

Check the next canonical action:

```bash
python3 <skill-root>/scripts/epipilot_bridge.py next --project-id <stable-id>
```

For an `investigate` directive, register the important unknown if it is not already present, then preregister one or more falsifiable hypotheses:

```bash
python3 <skill-root>/scripts/epipilot_bridge.py unknown --project-id <stable-id> --question "<decision-relevant unknown>" --impact high --mode experiment --voi 1.0 --decision-sensitivity 1.0
```

```bash
python3 <skill-root>/scripts/epipilot_bridge.py hypothesis --project-id <stable-id> --statement "<falsifiable statement>" --prediction "<observable prediction>" --falsification "<condition that would refute it>"
```

When several hypotheses compete, design the minimum discriminative experiment that makes their predictions diverge.

## Evidence and belief update

After execution, treat your own interpretation as non-authoritative. If useful, record it with `observe`; this path can never set `independently_verified=true`.

For decisive evidence, run a preregistered deterministic check through `verify-command`. The bridge derives pass/fail from the process exit status with `shell=False`, discards raw stdout/stderr, and is the only plugin CLI path that creates independently verified evidence.

```bash
python3 <skill-root>/scripts/epipilot_bridge.py verify-command --project-id <stable-id> --name "<preregistered check name>" --scope "<repository revision / experiment / task scope>" --cwd . python -m pytest <focused-verification-target>
```

Then update a hypothesis with the full cumulative evidence set. `SUPPORTED` and `REFUTED` transitions fail closed unless their decisive evidence is independently verified.

```bash
python3 <skill-root>/scripts/epipilot_bridge.py hypothesis-update --project-id <stable-id> --hypothesis-id <id> --status supported --confidence 0.95 --supporting-evidence <evidence-id>
```

Resolve a technical unknown only from independently verified evidence:

```bash
python3 <skill-root>/scripts/epipilot_bridge.py resolve --project-id <stable-id> --unknown-id <id> --evidence-id <evidence-id>
```

## Replanning and stopping

After each meaningful evidence update, request `next` again. Use the smallest justified replan scope; do not global-replan for a local implementation failure.

Stop exploration when additional information is unlikely to change the current decision enough to justify its cost. The `synthesize` directive is not automatic success. Produce an epistemic report containing:

- verified result and acceptance evidence;
- current causal explanation;
- supported hypotheses;
- refuted hypotheses and why;
- unresolved or explicitly accepted uncertainty;
- evidence provenance;
- experiments that changed the decision;
- remaining limitations and the conditions under which the conclusion should be revisited.

The final answer must distinguish verified facts from plausible interpretation.
