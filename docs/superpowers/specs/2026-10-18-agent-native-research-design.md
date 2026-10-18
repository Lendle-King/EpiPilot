# Agent-native research integration

Authority: user's request to integrate/optimize the research plugin for automatic,
efficient coding-agent research, retaining the earlier instruction to choose reasonable
implementation defaults without repeated approval handoffs.

Use current CARTS as the single canonical backend. Adapt the agent-facing workflow from
feat/research-plugin-cost-aware-tree-search@78c6266; do not overwrite its same-named
models or create a competing .research/events.jsonl. Native execution in this session.

The coding agent is the semantic investigator/executor. EpiPilot validates and persists
its structured discoveries, compiles bounded task packets, allocates resources, invokes
registered local adapters, independently recomputes measurements, and reopens invalidated
questions. No nested scientist agent or hidden LLM call is needed.

A discovery batch must identify its canonical state version and basis, supply exact Git
source anchors, falsifiable hypotheses and competing explanations, connect important
unknowns to discriminative experiments, and label property/variable/boundary/bottleneck/
method facets. A justified no-new-finding result is permitted. Validate the whole batch
before atomically adding existing canonical Hypothesis/Unknown/Task/Plan events plus
frontier metadata. Reject stale batches, bad quotes, duplicate semantic keys, invalid
links, cyclic dependencies and unchanged experiments.

Frontier metadata stores references and resolution history, never duplicate canonical
claim/evidence/task status. A question closes only from a relevant preregistered,
adequately sampled successful experiment with independently recomputed decisive results.
Inconclusive, infrastructure failure, source-document quotes or arbitrary command success
cannot close a scientific question. New contradiction can reopen it. Historical support
invalidated by current contrary evidence cannot boost proposals.

Phase depends on weighted resolved critical unknowns, not verification count. New critical
unknowns revert exploration. A default 20% exploration share is an explicit policy prior,
not a scientifically optimal ratio; protect feasible exploration against optimization
budget starvation. Physical feasibility, vector budgets, resource claims and calibrated
cost ratios reuse CARTS. Measured overruns persist and prevent new launches.

An agent loop alternates discovery and execution: iterate -> discover -> start/run ->
record -> verify -> resolve/reopen -> iterate. A new verified result refreshes the discovery
requirement. Persisted observations resume verification; unrecorded runs require authentic
recovery or measured cancellation. Atomic discovery writes and versioned packets survive
restart. CLI `epi` is a thin canonical facade; existing CARTS CLI/API remain compatible.

Verification quality is explicit. Built-in file integrity checks do not establish measured
scientific results. Registered deterministic CSV missingness and numeric-series adapters
recompute input-derived observations; domain plugins can inject runner/verifier ports.
No general-purpose arbitrary --check can promote scientific claims. ETF validation uses
actual local files, preserves V2.5 and all journals, and does not promote historical audits
to forward returns. Automation stops honestly for missing qualified inputs or exhausted
resources.

Acceptance: regression tests for all reviewed plugin failures; atomicity/replay/resume;
phase/continued exploration; complete synthetic agent trajectories; a new authentic ETF
missingness/boundary diagnosis; full ruff/mypy/pytest gates and independent final review.
Do not claim universal efficiency or newly attained investment targets.
