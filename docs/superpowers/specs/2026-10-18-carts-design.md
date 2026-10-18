# CARTS design (2026-10-18)

Authority: the user's CARTS implementation specification. Implementation details are
explicitly delegated; no further approval gate applies.

CARTS is a deterministic decision policy over canonical EpiPilot hypotheses, unknowns,
tasks, evidence and PlanGraph. Research metadata is an immutable projection on
ProjectState, rebuilt by typed events in the existing EventStore. It never duplicates
canonical task/evidence/hypothesis status. Scientific AND/OR dependencies differ from
technical task dependencies. Execution success, scientific outcome and qualification
for promotion remain separate.

Costs are vectors in named budget units. Reservations include uncertainty; settlement
records actual expenditure even on failure. Normalization uses budget shares, never
raw addition of dollars and compute hours. Unknown cost means abstention. Exclusive
ResourceClaims protect workspaces and other resources; initial execution is serial.

The first policy is explicitly a heuristic decision-value estimator, not EVSI. It
combines uncertainty-weighted information, goal contribution, discounted descendant
unlocking and exploration, minus risk, divided by normalized cost. Baselines reuse the
existing SchedulingSignals formula and FIFO. All policies share feasibility rules,
feedback, experiment pool and budgets. Low fidelity never automatically validates
high fidelity. Only preregistered, independently verified, adequately sampled results
can change hypothesis status; only qualifying F4 evidence can meet the final goal.

The serial application loop persists reservation before execution, raw observation
before verification and settlement before releasing locks. On restart it completes
recorded observations; unrecorded in-flight work requires explicit recovery or
cancellation with actual costs, never blind resubmission. Stable proposal IDs prevent
unchanged retries. New inputs require a new proposal and provenance.

ETF adapter reads pinned Git objects and local manifests only. It imports V8 routes,
performs report-consistency and data-integrity audits, and records missing journals
and original PIT vintages as blocked. No strategy/journal writes or remote operations.
Git committer timestamps bound replay knowledge; historical compute costs are unknown.
Repeated 2018–2026 results remain development evidence. No new ETF return claim follows
from scheduler benchmarks or artifact integrity.

Validation: adversarial unit/replay/recovery tests, seeded paired synthetic benchmark
including misleading and delayed signals, chronological ETF replay, authentic offline
pilot with independently recomputed checks. Reports separate measurements, estimates,
source observations and scientific conclusions.
