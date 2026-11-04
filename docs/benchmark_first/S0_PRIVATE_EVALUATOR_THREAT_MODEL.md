# S0 evaluator boundary and remaining threats

## Implemented boundary

`PublicTask` contains only public synthetic development rows and their MSE target. The host
scorer accepts a bounded, strict numeric prediction schema, exact ordered row IDs and a candidate
SHA-256. It rechecks the code digest and independently recomputes MSE; it never imports or
executes candidate code. Missing/duplicate JSON keys, nonfinite values, extra fields, missing
rows, mismatched candidate identity and source changes fail closed. The evaluator source digest
is pinned in the offline configuration and checked before any container launch.

Candidate generation/execution takes place in a separate Docker container with no network,
credentials, host home directory, host event database or Docker socket. Only an explicit public
source checkout, harness source and synthetic cell work area are mounted. Source and harness are
read-only. Final code is copied outside that writable mount and hashed. The trusted host reads
numeric outputs after the container closes. A sealed scorer has no method in the agent protocol.

Actual boundary probe verified denial of a host-side synthetic sealed file, absent credential
environment variables/socket, denied network and read-only framework source. Cgroups reported
memory.max=2147483648, cpu.max=200000 100000, pids.max=64. A wall timeout exited 124 and cleanup
removed its named container. A separate 128 MiB memory test exited 137 after allocating 300 MiB.
See the sanitized process receipts and `S0_SECURITY_RESULTS.json`.

## Scope of the evidence

There is **no deployed private hidden test evaluator** in this PR and no hidden scientific
score. The synthetic host-side sealed file proves only this local mount boundary. No real
hidden labels were downloaded or committed. An independent scoring service with separate
runner identity, immutable scorer/data digests and authenticated run/candidate-bound attestations
is still required. Do not turn `sealed_scoring=true`, a local digest or this smoke into that proof.

Public dev feedback can guide search; hidden feedback must only be produced after every arm's
candidate/checkpoints are frozen and sessions closed. For tasks requiring inference on hidden
features, run the frozen candidate in a **second untrusted inference job** without labels or
credentials, then pass only schema-checked predictions to a separate trusted numeric scorer.
Never execute submitted Python/pickle/notebooks inside the trusted scoring process.

## Attacks and limits

- Symlink/oversized/malformed candidate and prediction artifacts are rejected at ingress.
- Source repository/ref/origin/dirty mismatches stop setup; they are not silently repaired.
- Output identity/digest binding detects accidental substitution, but does not authenticate an
  untrusted author's claimed training trajectory. Private runner attestation is pending.
- Process limits and global job locks bound local engineering work. Sampled host RAM/log-size
  guards are not absolute disk/whole-machine quotas. Container writable volume disk quotas,
  hostile multi-tenant denial of service and Docker VM escape resistance need server audit.
- Raw logs remain outside Git; published summaries contain synthetic metrics and hashes only.
- The S0 mock counter has no provider transport. It cannot meter a real external CLI's internal
  API requests. Paid execution requires an independently tested quota proxy/provider hard cap,
  per-attempt reservations (including retries), token caps and explicit operator approval.

Managed delivery EventStore authorizes engineering changes only. Neither integration nor
publication constitutes scientific acceptance.
