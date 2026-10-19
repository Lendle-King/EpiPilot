# Cumulative research understanding

The coding agent maintains a versioned working model of the research subject and goal.
It is a derived projection of canonical evidence and source observations, not a second
truth store, scientific verifier, or replacement for individual claims.

## When to update

After every ten settled research checks, update the cumulative understanding before
planning the next batch. Use the project's actual counter and state its meaning; do not
mix legacy route numbers with the canonical review count. Failed or invalid settled checks
still count operationally, but do not become scientific evidence.

At takeover, create a current baseline if a due synthesis is missing. Mark its real writing
time and knowledge cutoff; do not fabricate historical versions or backdate understanding.
A material contradiction may justify an earlier revision. This is an operator requirement;
it does not add an automatic backend action, scheduler, or source of experiment authority.

## Required artifacts

Save an immutable report and a machine-readable coverage index in the research repository:

- `docs/research_understanding/understanding_<review-count>_<timestamp>.md`
- `docs/research_understanding/coverage_<review-count>_<timestamp>.json`

Use a small index to point to the latest version; retain every older report and index.
Record project ID, canonical event version, UTC creation time, completed-review count,
source revisions, previous synthesis version/hash, and the goal's actual acceptance rules.
The index may be replaced as a derived pointer; reports, claims and audit events may not.

## Synthesize the whole subject

Read the cumulative claim inventory, not just the last ten rounds, the context packet's
bounded evidence list, or the current winner. Include older verified results, negative
findings, active/proposed hypotheses, unresolved questions, and legacy document claims.
Keep current and historical-only entries distinguishable. Preserve each entry's namespace
and stable ID; a report claim and a hypothesis referencing the same measurement are not
independent replication.

Organize a small set of explanatory themes around the goal: proposed mechanisms, system
or market properties, data and execution constraints, method boundaries, and acceptance.
For each theme state:

1. the current scoped understanding and confidence/qualification;
2. supporting, refuting, and limiting claim/evidence IDs;
3. the required conditions: inputs/universe, time window, costs, timing and evidence scope;
4. unresolved conflicts, alternative explanations and missing prerequisites;
5. implications for the next experiments, including expected discriminating information.

Distinguish a measured association, a causal interpretation, a source document's wording,
and an independently accepted goal. A higher-level explanation is tentative unless its
own validation contract has passed. Never promote document observations through synthesis.

## Reconcile without forcing agreement

When claims appear inconsistent, compare their predicates and contracts first. Different
windows, universes, engines, transaction costs, execution times, or acceptance rules may
explain the difference. Preserve context-qualified coexistence. If comparable evidence
actually conflicts, mark the conflict and retain both sources; use canonical evidence-gated
reopening/supersession when justified. Do not average incompatible returns, discard a
negative result, reopen a question from prose alone, or rename an unchanged failed task.

Give every inventoried entry one explicit coverage disposition: integrated into a named
theme; contextual background; duplicate/view of specified source or evidence; superseded
with a reference; quarantined for failed provenance; or deferred with a concrete reason.
No silent omissions. Source hashes and verbatim Git anchors must validate before an entry
can support the report. Failed historical references remain auditable and quarantined;
partial coverage is reported honestly. Many paraphrases of one experiment count once as
scientific support, regardless of how many claim IDs they generated.

## Show change and guide action

Compare to the previous understanding: what was strengthened, weakened, overturned,
newly scoped, or left unknown. The first baseline says that earlier versions did not exist.
Include a direct goal-gap assessment and a short list of falsifiable next questions chosen
from the unified model. Rank by decision relevance, discriminating value, feasibility and
real remaining budget; keep exploration of unknown mechanisms alongside optimization.
A best historical candidate is not automatically a prospective success or deployment choice.

Validate coverage completeness, reference integrity, scope boundaries and the absence of
unsupported promotions. Report limitations. Persist the user's periodic-synthesis decision
and grounded artifact references through existing append-only events; keep canonical facts,
hypothesis states, budget, measurements and frozen baselines unchanged unless their own
contracts authorize a transition. Synthesis does not authorize extra experimental rounds.

Then use this version and the current task contract to compile the next bounded research
context. Do not dump the complete inventory into every executor prompt. Each ten-round
user update includes both the per-round results and this cumulative understanding.
