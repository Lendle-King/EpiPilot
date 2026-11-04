# S0 budget and recovery verification

The affected contracts are: no executor self-certified evidence; durable reservation before
attempt; failures retain spent quota; duplicate code/data/seed experiments are not rerun;
recovery requires explicit settlement; no paid transport exists in S0.

New automated tests cover four attempted **mock** calls, three mock dev trials and the 1800 s
cell ceiling, intentional pending-call interruption, explicit reconciliation, failed calls/trials,
exhaustion, duplicate operation/fingerprint rejection, immutable cell/budget identity, clock
rollback/timeout rejection and corrupted/truncated JSONL. All reservations remain spent after
restart. Reusing a completed ID fails, and a rejected reservation consumes no call allowance.

Additional tests cover exact source origin/SHA, tracked/untracked/ignored dirt, strict numeric
prediction ingress, code/prediction substitution, source changes, unexpected evaluator SHA,
nonfinite/duplicate/extra JSON fields, paid transport rejection and incomplete-run reconciliation.
`reconcile_s0` retains every missing/interrupted cell and never invokes a subprocess or refills it.
Existing benchmark tests cover both metric directions, normalized progress, evaluator mismatch,
missing/duplicate cells, per-arm ceilings, failures/timeouts and unknown vs zero dollar billing.

The clock-based 1800 s case is deterministic; it does not waste 30 minutes sleeping. Native
normal/crash smokes separately execute actual upstream Python with scripted provider responses.
AIDE uses native serialization/deserialization; AIRA uses native checkpoint save/load. Current
EpiPilot uses its original append-only event store and independently executes its frozen numeric
verifier, with inconclusive scientific verdicts; its crash arm uses the original abort transition.

Actual Docker negative checks: blocked source write/network/host sealed-file access; wall timeout
124; cgroup OOM exit 137. Raw command argv, elapsed time, log hashes and all framework failures
are retained in the run receipts. Container runtime ceilings are 90 s/process, two processes per
cell, two CPUs, 2 GiB and 64 PIDs. Synthetic task inputs and baseline commits are identical across
arms; there is no inter-arm memory sharing. Limits for paid training/GPU usage remain unapproved.

These checks do not establish provider request metering, complete ML dependency parity or
independent scientific efficacy. The 4/3/1800 ledger is mock-only, not an S1 spending permit.
