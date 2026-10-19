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
- `docs/research_understanding/external_sources_<review-count>_<timestamp>.json`

The external-source ledger records the actual search and reading performed for this version.
Link it from the report, coverage and latest pointer, with its content hash.

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

## Search outside the project at every synthesis

Alongside cumulative local evidence, perform a fresh, bounded web search driven by the
subject, acceptance goal and decision-relevant unknowns. Search for relevant research,
papers, author research blogs and news; source types are useful when relevant, not quotas.
Include competing mechanisms, negative findings, method limitations and developments that
could change the next route. Do not search only for confirmation of the current winner.
Reuse foundational sources with their inspected versions, and search for newer results,
corrections or changing conditions. Record the actual query strings, UTC search interval,
scope, stopping reason and coverage limits. Use public generic queries, not private prompts,
credentials, unpublished results or confidential claim text. Respect source access controls.
Bound the search by decision value and available operator resources. Record measured search
costs separately from experiment accounting, with unknown costs explicit rather than zero;
do not create or reset canonical budgets to accommodate literature review.

Read the original source before relying on it. Prefer original papers, author/institution
copies, official statistics and first-party announcements for technical claims. Trace a
blog or news lead to the underlying study/data when available. Distinguish peer-reviewed
work, preprints, practitioner experiments, commentary and event reporting. Evaluate method
and relevance rather than treating prestige, recency or publication as verification.

Each retained source entry requires:

- stable source ID, title, author/publisher, URL and DOI/version when available;
- publication/update date and event/data window separately, plus UTC access time;
- actual access level and inspected section/page locator: full text inspected, selected
  sections, abstract only, search lead only, or failed access; unknown metadata stays unknown;
- a short attributed finding in the agent's own words, study market/universe, method,
  costs/timing and evidence scope, with missing details and limitations explicit;
- links to local claim/question IDs and a relation: supports, challenges, narrows,
  suggests an alternative, or contextual only; say whether that relation is an inference;
- independent-study identity, related versions/mirrors, conflicts of interest when evident,
  disposition and implications for a falsifiable next question.

Retain failed or irrelevant leads with a reason. A snippet, title, inaccessible page or
unread paper cannot be described as a completed review. When access is blocked, use a
legitimate author copy or mark the gap; never invent contents. A bounded search is not an
exhaustive literature review. If search is unavailable, label the external component
incomplete and record the blocker; do not silently claim the expanded synthesis is complete.
Save concise notes and allowed excerpts, not wholesale copyrighted pages or private traces.
News must distinguish when an event happened from when it was reported and first observed;
current reporting must not be backfilled into historical strategy inputs.

The report includes a local/external reconciliation table and an explicit understanding
delta: strengthened, weakened, narrowed, newly hypothesized, unchanged or unresolved.
Explain transfer limits across markets, eras, universes, leverage, trading rules and costs.
One study cited by many pages is one external study, not independent replication. External
evidence may motivate a hypothesis or change route priority; it does not establish the
project's measured effect, close a PIT gap, or satisfy prospective goal acceptance.
Maintain external source observations separately from locally verified measurements and
canonical facts. A Git anchor to the saved source note proves that note's wording only;
the source URL, access record and methodology are still required for its interpretation.

Use the combined model to propose bounded tests with predictions, alternative explanations,
data/availability prerequisites, costs and failure criteria. Keep exploratory alternatives
alongside improvements to the current candidate. Register any hypothesis or state change
through existing evidence-gated contracts; reading a paper supplies no execution authority,
budget reset or automatic scientific promotion.

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
