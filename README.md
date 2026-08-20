# EpiPilot

**EpiPilot** is an evidence-driven epistemic orchestration framework for long-horizon coding agents.

EpiPilot sits above coding agents such as Pi, Codex, Claude Code, and other executors. It manages project requirements, unknowns, hypotheses, evidence, dynamic task graphs, context compilation, verification, and replanning so that long-running work remains auditable and evidence-driven.

## Core loop

```text
Goal -> Requirements -> Unknowns/Hypotheses -> Plan -> Execute
     -> Observe -> Verify -> Evidence -> Belief Update -> Replan
```

## Core invariants

1. No completion without evidence.
2. Executor self-report alone can never create a canonical fact.
3. Every plan mutation must trace to a requirement, decision, or evidence item.
4. Canonical project state is separate from the LLM context compiled for a task.
5. The same failure may not be retried without new information or a changed strategy.

## Project status

EpiPilot is in early design and bootstrap development. The initial repository work focuses on architecture contracts, contribution standards, automated quality gates, and a minimal typed runtime skeleton.

## Documentation

The detailed architecture, code standards, contribution workflow, and agent instructions will live in this repository as version-controlled project contracts.

## License

No open-source license has been selected yet. A license will be added only after an explicit project decision.
