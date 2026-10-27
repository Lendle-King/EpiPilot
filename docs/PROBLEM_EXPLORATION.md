# Optional Problem Exploration Mode

EpiPilot's exploratory mode is a **strictly opt-in** gate around the existing
coding-agent-driven research loop. The default CLI remains unchanged. When
enabled, the agent must characterize decision-relevant properties of a problem
before registering algorithm-optimization experiments. This is not another
agent, another experiment runner, or another canonical claim database.

## Enable and operate

In the *target* research repository (after an explicitly approved `epi init`):

~~~bash
epi explore enable --budget-share 0.20
epi onboard
epi explore status
epi explore probes
epi explore probes --dimension conditional_dependence
epi scout prepare
# Reserve Scout cost before source-grounded discovery; review submitted leads.
epi iterate
# Register discriminative Unknown/Claim/Proposal and use normal prereg/start/record/verify.
epi explore profile
~~~

The catalog has eight kinds of suggested probes: evaluator repeatability,
bottleneck ablations, solution-space perturbation, matched conditionals,
interaction tests, oracle/upper bounds, robustness and cost-response frontiers.
Templates are **not** registered or run automatically. Every new study must
use the existing frozen experiment contract, resource approvals, substantive
verifier and independent measurement checks. An accepted Scout citation is not
scientific evidence.

An exploration stage selects only `--kind explore` Proposals. This
restriction is checked at planning **and** pre-registration **and** start. It
does not silently abort or interrupt an existing experiment. Stage switches
are rejected until pending Scout operations and registered/running/awaiting-
verification contracts have been handled.

## Exploration readiness versus budget exhaustion

~~~bash
epi explore status
epi explore advance --reason "Critical unknowns resolved with reviewed evidence"
~~~

`advance` is allowed without override only if:

- There is at least one **independently measured, conclusive exploratory**
  experiment whose evidence was explicitly reviewed in a scoped Claim.
- No unresolved Unknown has importance × decision_sensitivity ≥ 0.25.
- No frozen research operation is pending.

This threshold is a conservative *operational gate*, not a statistical claim
of complete understanding. Unknowns must be intentionally marked as resolved
through EpiPilot's existing reviewed evidence protocol.

If exploratory budget is exhausted, `epi iterate` returns
`exploration_budget_limited`, **not** `exploration_ready`. With explicit
authorization, a human operator may elect to optimize with known uncertainty:

~~~bash
epi explore advance --override \
  --reason "Authorized optimization despite the recorded unresolved questions"
~~~

`advance --override` also allows an informed phase transition where the
readiness heuristic is inappropriate. Overrides are recorded in the append-only
event log; they do not fabricate evidence or close Unknowns.

## Budget and resume

The exploration cap is `research_goal.budget × budget_share`, accounting for
actual Scout costs and `explore` experiment costs and their reservations or
aborted run costs, including exploratory work that predates mode enablement.
The cap shares the *existing* project budget and cannot mint new resources.
A run that exceeds its reservation still records actual cost and may overrun
the exploration cap; subsequent reservations are blocked.

`epi iterate` prioritizes an in-flight run, verifier or Scout operation before
any proposed action. If the same scientific question remains open but there
is no affordable exploratory Proposal, it returns
`exploration_design_required` with suggested templates.

The user may return to original behavior through:

~~~bash
epi explore disable --reason "Return to the existing EpiPilot selection policy"
~~~

This keeps all events and validated scientific evidence, but stops gating
optimizer proposals. Disabling does not refund expenses.

## Scoped Problem Profile and algorithm guidance

Every scientific property continues to live as a normal Unknown/Claim/
qualified-Evidence link. Exploration adds optional **advisory algorithm
guidance**, anchored to the current conclusive, independently reviewed Claim
status and its evidence; it is *not* a second scientific truth system:

~~~bash
epi explore guide \
  --title "Conditional complexity favors a light router" \
  --dimension conditional_dependence \
  --scope "matched dev tasks" \
  --claim CLM-ID \
  --recommend "Prefer conditional low-dimensional routing" \
  --avoid "Train an unconstrained large router without a reproducible boundary" \
  --reason "Matched validated experiments identify a scoped decision boundary"
epi explore profile
~~~

`guide` cannot be registered from an unverified or inconclusive Claim.
Guidance becomes `stale` automatically when new counterevidence changes
the linked Claim status, while retaining all original traceability.
The agent/human must interpret the result; the CLI does not silently promote
advice into a universal scientific fact.

In optimization, a new critical Unknown reopening from contradictory qualified
evidence causes `epi iterate` to return
`exploration_reconsideration_required`. To investigate:

~~~bash
epi explore reopen \
  --reason "Matched counterevidence invalidated the route recommendation"
~~~

Alternatively the operator can explicitly acknowledge the new evidence and
continue through `epi explore advance --override --reason ...`. This acknowledgment
does not delete the contradictory evidence. The mode therefore supports
**diagnose → optimize → reconsider → diagnose**, with observable decisions.

## Design and integrity boundaries

- **Authoritative**: `.research/events.jsonl`, the versioned v2 event
  replay, validated experiments and scoped Claims.
- **Read models**: readiness, `explore profile` and the probe catalog. None
  writes claims or performs experiments on read.
- **Executor**: the existing Codex/Kimi coding agent with its normal permissions
  and existing Git/pre-registration budget controls.
- **Advisory**: readiness, decision forecasts and algorithm guidance are not
  causal/statistical proofs.
- **Legacy compatibility**: missing exploration state means disabled; existing
  v1/v2 events and default `epi iterate` behavior retain their semantics.

Do not use the small template catalog to justify an automatically selected
scientific result. In particular, experiment completion/frozen metric thresholds
must not be conflated with externally valid causal interpretation.

## Validation

~~~bash
ruff format --check .
ruff check .
mypy src
pytest -q tests/research/test_problem_exploration.py
pytest
~~~

For an **efficiency** claim, run a matched equal-budget research policy
benchmark, not just unit tests: baseline EpiPilot, exploration enabled, and a
diagnostic-cost-control ablation that does not consume the learned Problem
Profile. Fix held-out evaluator, task seeds, model, tools, intervention budget
and reporting of every failure in advance.
