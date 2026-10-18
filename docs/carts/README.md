# Cost-Aware Research Tree Search (CARTS)

CARTS replaces fixed research catalog ordering with an explainable decision policy.
It reuses canonical `Hypothesis`, `Unknown`, `Task`, `PlanGraph`, `Evidence`, typed
project events, task transitions and `SqliteEventStore`. `ProjectState.research` stores
research metadata and accounting, never another copy of task/hypothesis/evidence status.
No new service, UI framework, dependency, trading connection or remote write is required.

## Decision contract

The heuristic is **not EVSI** and has no calibrated probability claim:

```
information = information_prior * uncertainty
contribution = goal_contribution_prior * (1 - .5 * uncertainty)
unlocking = sum(descendant_contribution * uncertainty * .5**distance), capped at 1
exploration = .15 * uncertainty
score = (information + contribution + unlocking + exploration - risk)
        / (sum(estimated_cost[unit] / budget[unit]) + 1e-9)
```

Every ranking includes the components, prior source, confidence, uncertainty, reasons
for exclusion and a stop reason. A one-step follow-through guard protects an affordable
F2+ experiment with verified prerequisites when another low-fidelity task would consume
its remaining budget. The guard is a declared heuristic intervention recorded in the
explanation; the score is then an ordering sentinel, not a cardinal EVSI estimate.

FIFO and the existing `SchedulingSignals.priority` product formula share the same
physical feasibility checks, proposals, latent feedback and budgets. Ablations omit
information, cost or descendant unlocking; the no-cost version also omits the budget
lookahead preference. Hard budget checks remain enabled in all variants.

AND prerequisites must retain valid support recursively. OR alternatives need one valid
path. Parent links represent genealogy, not mandatory success. Mutex declarations are
symmetric during feasibility checks even if declared on one node. Exclusive worktree
claims prevent concurrent writers; consumable resources need declared capacities.
Execution is serial. ResourceScheduler is a pure compatibility check, not an OS lock;
the durable per-project reservation/CAS prevents duplicate launch. Cross-project or
external processes need a shared resource coordinator before enabling parallel runners.

Only explicit comparable fidelity chains use `VerifiedFidelityPolicy`; inconclusive,
under-sampled, unreliable or failed observations cannot unlock them. Low-fidelity
support never proves the F4 outcome. A definitive negative can refute only its declared
hypothesis with adequate verified samples; a missing dataset blocks qualification,
not the economic alpha proposition. Repeated history remains development evidence.

Final goal acceptance needs independently verified, explicitly promotion-qualified F4
observations in the goal's required scope, all metric constraints and minimum samples.
A nonzero `minimum_improvement` additionally requires a verified `improvement` metric
relative to a preregistered baseline. The verifier owns its computation and provenance.
No unit-test success can satisfy a research goal.

## Commands

Run from EpiPilot with its environment installed (`pip install -e '.[dev]'`):

```bash
python -m epipilot.research --help
python -m epipilot.research benchmark --seeds 100 --start-seed 1000 --output .epipilot/carts/benchmark
python -m epipilot.research etf-replay --repository /path/to/quant_etf --output .epipilot/carts/replay
python -m epipilot.research etf-pilot --repository /path/to/quant_etf --db .epipilot/carts/test.sqlite3 --project etf-validation --output .epipilot/carts/pilot
```

Generic JSON import uses `ResearchDefinition` (`goal`, `nodes`, `proposals`), with the
strict schemas in `research/models.py`. IDs are stable UUIDs. Unknown IDs must already
exist in canonical state. The library supports adding new canonical hypotheses via
`engine.add_hypothesis(node, predictions, falsification_conditions)` and proposals via
`engine.propose(proposal, canonical_basis_ref)`, preserving earlier events.

```bash
python -m epipilot.research init --definition problem.json --db research.sqlite3 --project example
python -m epipilot.research show --db research.sqlite3 --project example
python -m epipilot.research rank --strategy carts --db research.sqlite3 --project example
python -m epipilot.research dry-run --db research.sqlite3 --project example
python -m epipilot.research propose --proposal experiment.json --basis-ref CANONICAL_ID --db research.sqlite3 --project example
python -m epipilot.research budget --db research.sqlite3 --project example
python -m epipilot.research report --db research.sqlite3 --project example
```

`run-next`, `etf-pilot`, `verify` and `resume` use the built-in read-only ETF adapter in
the CLI. Other domains embed `ResearchEngine` with their own runner and verifier:

```python
engine.initialize(goal, nodes, proposals)
engine.run_next(my_runner, my_independent_verifier)
engine.resume(my_independent_verifier)
```

Six typed ports are in `research/ports.py`: `ExperimentRunner`, `EvidenceVerifier`,
`CostEstimator`, `ResearchObjective`, `FidelityPolicy`, `ResultInterpreter`. BudgetManager
implements estimation; pure `MetricObjective`, `ThresholdInterpreter` and
`VerifiedFidelityPolicy` provide reusable defaults. Core policy imports no ETF modules.
Threshold interpretation is an observation-level suggestion, not verification.

## Persistence and recovery

Canonical events remain append-only in the existing `project_events` table. Reservations
precede runner calls, observations precede verification, and settlement releases budget
and resources. SQL expected-version checks reject stale workers. Stable task/proposal/
evidence IDs prevent repeated submission and billing. Actual overruns are recorded
rather than clipped; negative remaining budget prevents another launch. Cost calibration
uses median actual/estimated ratios by kind/fidelity/unit, clipped to [.5,4]. Unknown
estimates or uncovered cost units cause abstention.

`resume` finishes a persisted observation without rerunning the experiment. An uncertain
run with no persisted observation fails closed. Recover its authentic typed Observation:

```bash
python -m epipilot.research observe --observation recovered.json --db research.sqlite3 --project example
python -m epipilot.research resume --db research.sqlite3 --project example
```

Or cancel with explicit measured cost (JSON `{"costs":[{"unit":"wall_seconds","amount":1.2}]}`):

```bash
python -m epipilot.research cancel --proposal-id ID --actual-cost measured.json --reason 'operator measured aborted work' --db research.sqlite3 --project example
```

Cancellation refunds only unspent reservations; it never erases incurred cost or retries
unchanged inputs. Artifact paths use stable UUID filenames and existing outputs are not
overwritten. Verifier exceptions preserve in-flight state for explicit recovery.
The wall meter covers runner work and the completed verifier call; orchestration overhead
is reported separately in the benchmark. Work lost before a measurement is persisted
cannot be reconstructed as an exact actual cost; recover such costs explicitly. The
current implementation is not a process-level spend meter or distributed coordinator.

## Workspace API cutover

The adopted project and canonical events share `EPIPILOT_WORKSPACE_DB`. Explicit import
is the cutover; it requires the legacy loop to be idle:

```
POST /api/projects/{id}/research-tree/import
GET  /api/projects/{id}/research-tree
POST /api/projects/{id}/research-tree/run      {"max_rounds":10}
POST /api/projects/{id}/research-tree/resume
```

Once configured, existing `GET/POST .../research-agent/loop` dispatch to CARTS. Old route
runs/checkpoints/workspace events remain untouched; legacy browser resume fields are
rejected for a CARTS project. Raw source reports are imported as attributed observations,
not promoted investment facts. The explicit import is not an additional human approval
requirement; the user authorized it in the implementation request.

## ETF adapter limits

Read pinned source objects from V8, V5, V7 B2 and execution-stress branches. Required
local inputs are identified in `research/etf.py`. Saved stress report checks prove only
its grid/arithmetic consistency, not independent cash/holdings or real opening liquidity.
Local panel checks prove manifest integrity, not historical PIT membership/publication
or frozen V25 reproduction. Original journal capture provenance and mature paired
returns remain mandatory; date columns and newly downloaded history are insufficient.
No financial model is trained from unqualified B1/B2 snapshots. No future logs, orders,
frozen strategy changes or GitHub/Notion actions are created.

Historical replay uses actual Git committer availability, never the date in a report
filename. At each frontier only revealed routes enter the real policy. Historical costs
are unknown, so measured-cost rankings abstain. Two labeled relative-cost assumptions
show sensitivity; they do not establish measured historical savings or ETF returns.
