# Algorithm-result iteration contract

The quant_etf loop uses the committed research adapter
`scripts/run_epipilot_algorithm_round.py` when present. Configure the local
interpreter using `EPIPILOT_ALGORITHM_PYTHON`. Other projects retain the existing
review-only flow; historical checkpoint records are never rewritten.

An algorithm round selects exactly one supported, not-yet-completed algorithm
from source-grounded claims and the preceding experiment result. The adapter
executes the fixed public-history V3 protocol on the host; the browser model
evaluates the actual new artifacts, explains the algorithm result, and drafts
new claims. Missing host access in the browser is not evidence of missing data.

Completion requires all of the following:

1. A receipt bound to the current route ID, round number and selected algorithm.
2. SHA-256 validation of inputs and artifacts, ordered UTC execution timestamps,
   daily equity/return reconciliation, and independently recomputed CAGR,
   maximum drawdown and annual turnover.
3. A current-route review containing `execution_run_id`, `candidate_conclusion`
   (`accept`, `reject`, or `inconclusive`), nonblank `new_claims_zh`, and an
   algorithm report. Acceptance means retaining a research hypothesis, never
   authorizing deployment or promoting a canonical fact.
4. An archived report and receipt in `docs/auto_iteration/`, followed by a
   source-grounded claim refresh that includes that report.

Only then is a completion checkpoint appended. Negative algorithm results are
valid completed experiments. Preflight-only reports and blocked/malformed
reviews do not complete a round. Up to three changed-information review
attempts can repair a response inside the same round; each rejection appends
a `loop_attempt` event. Exhaustion stops fail-closed at the original checkpoint.
Host execution faults also leave the round incomplete for investigation and
repair; unchanged failures are not automatically retried.

If a reviewed experiment failed during archival or claim refresh, restarting
the loop resumes that route automatically. A `loop_archive` record binds the
report path, Git blob ID and byte SHA-256; recovery checks them before reusing
the report. The verified execution receipt and review are reused without a
new browser submission. A successful byte-grounded extraction explicitly
supersedes a prior active rejection while preserving its audit record.
Host attempt directories are versioned and never overwritten. Retrying an
unfinished host attempt requires changed source/input hashes.

The fixed adapter is launched without a shell or inherited model credentials.
It must be tracked and clean along with research source/configuration. Outputs
are isolated under `results/v3/`. Route text cannot supply executable commands.
The adapter checks the V2.5 SHA-256 lock before execution and verifies input and
frozen-file hashes afterwards. Forward journals are never experiment inputs or
outputs.

Historical public data remains exploratory: post-acquisition bars and current
membership cannot establish historical point-in-time qualification or
independent forward performance. Consequently a historical `goal_met` response
is recorded as an observation and normalized to `continue`; no data-audit phase
can satisfy the independent-forward research goal. Algorithm catalogs are
bounded and completed algorithms are not rerun unchanged. Exhaustion requires
a new evidence-grounded experimental design, not arbitrary parameter search.
