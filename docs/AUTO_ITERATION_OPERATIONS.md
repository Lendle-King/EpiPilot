# Auto-iteration operations

The local loop stops on a terminal executor review (`blocked` or `goal_met`),
the requested round budget, or an adapter/validation failure. A review is an
observation, not independent verification of the research goal.

Every terminal round follows the same archival sequence as a continuing round:
validate the review, commit its report under `docs/auto_iteration/` in the
research repository, repin the source snapshot, refresh grounded candidate
claims, then append the checkpoint. A failure during archival or extraction
fails closed before a new checkpoint is recorded. Existing audit rows are
never replaced.

After a repin, the semantic refresh revalidates every prior citation against
the new Git blob before carrying the claim forward. A changed or missing
source cannot be inherited; its claims are discarded from the carry-forward
view and the source is extracted again. This keeps route context continuous
without treating an old commit's citation as current evidence.

A repository path and a nonblank report are required to complete a round.
The newly archived report must itself contribute quote-grounded claims and
must not appear in `rejected_documents`; successful extraction from older
documents cannot satisfy this gate. Missing/skipped/rejected report extraction
leaves the review and any committed report available for audit, while leaving
the completed-round count and checkpoint unchanged.

The Chrome extension must return the `assistantText` field, including an empty
string when no assistant response exists. The backend rejects an older loaded
extension before typing or submitting a prompt. Reload EpiPilot at
`chrome://extensions` after extension JavaScript changes, then inspect the live
DOM through `/api/browser-bridge/inspect` before retrying. A connected heartbeat
alone does not establish protocol compatibility. Screenshot permission is
optional for DOM inspection and must not be broadened merely to diagnose a reply.

The loop checkpoint counts archived rounds; starting with `max_rounds` permits
that many additional rounds after the latest checkpoint. Operators must account
for the remaining overall budget when resuming after a failure.

For quant_etf, preserve the V2.5 SHA-256 lock and its forward journal. Reports
and quote-grounded claims do not independently establish CAGR or drawdown.
Do not backfill forward evidence, enable broker execution, or enable remote
GitHub/Notion actions as part of recovery.

Exact quotes are matched with their original whitespace. Trimming a quote
before matching can admit bytes that never occurred in the source line; such
a mismatch rejects the whole document batch. Line numbers follow the source
compiler's Unicode `str.splitlines()` convention, including form-feed breaks.

## Resuming a pending browser reply

The extension reply timeout is 7,200 seconds. A timed-out request that is still
running in the existing Chrome tab can be recovered by posting to the loop
endpoint with `resume_route_run_id` and `resume_tab_id` together. Both must
identify the pending latest route; the tab's observed transcript must contain
that route ID in the latest user turn. Recovery retains the conversation URL,
user-turn count and route ID, and checks all three on every browser read. It
accepts assistant text only after the latest user speaker boundary. Recovery
only reads that tab and waits for a stable, completed
reply. It does not type, click, create a new route, or resubmit the first prompt.

The resumed round is the next round after the latest completed checkpoint and
counts toward `max_rounds`. Later rounds use the normal submit path. Recovery
appends a `loop_resume` audit record; the previous stop summary remains intact.
The original request's two-hour window is measured conservatively from its
persisted route creation time, so restarting recovery does not reset the clock.
Missing/mismatched bindings, an already reviewed route, or an expired window
fail closed. Browser reads are bounded by the remaining window and checked
again after I/O against the original UTC deadline. A returned review must still pass schema, archival and grounding
gates before a checkpoint can advance.

Outgoing iteration packs explicitly forbid GitHub/Notion remote actions,
workflow triggering, pushes, trades, V2.5 changes and backfilled forward data.
Executor progress text is not proof that an external action occurred or that
those instructions were followed.
