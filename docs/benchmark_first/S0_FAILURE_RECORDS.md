# S0 retained failures and corrections

All are engineering observations, not framework-performance comparisons. No failed cell was
refilled inside an existing run. New probe directories identify changed configurations/strategies.
The complete 21-cell sanitized ledger includes all six failed cells (28.57%); intentional crash
trials are reported separately. Raw logs remain in the local benchmark workspace.

| Phase | Failure/observation | Changed strategy and outcome |
|---|---|---|
| Pre-change sample preflight | EpiPilot example expects b7633a0, checkout is requested d20c891 | Retained mismatch; new explicit synthetic config pins d20c891; sample not used as authority |
| Image build 1 | Public dependency download read timeout, exit 2 | Increased timeout; raw result was captured by tool output, no local full-log SHA retained |
| Image build 2 | Resolver conflict: AIDE jsonschema 4.19.2 vs AIRA LiteLLM >=4.22 | Separate native dependency images; retained build-2.log digest in artifact index |
| probe-1 / AIRA | CLI and core require LOGGING_DIR | Set only nonsecret sandbox directories; no .env/keys forwarded |
| probe-2 / AIRA | CLI lacks mlebench; direct Greedy import causes circular registry import | Initialize upstream solver registry first; no upstream edits or substitute solver |
| s0-run-1 / AIRA | Core smoke harness used wrong logger import attribute | Corrected upstream logger initialization; core then ran unchanged |
| s0-run-2 / AIRA | Top-level CLI still lacks mlebench | Core succeeded; CLI failure retained; no fake CLI wrapper or hidden fallback |
| final normal / AIRA | Same unresolved required CLI dependency | NOT_QUALIFIED; independent explicit final validation, not an automatic retry |
| final crash / AIRA | Same CLI blocker; core's first synthetic trial intentionally crashes | Native debug and checkpoint recovery succeeded; CLI blocker remains NOT_QUALIFIED |
| final crash / all four arms | First generated candidate raises intentional RuntimeError | Failure remains in per-trial history; changed scripted candidate succeeds; EpiPilot uses native abort |
| security timeout / OOM | Exit 124 / 137 expected | Named-container cleanup and cgroup ceiling verified; these are passing negative checks |
| New test development | Initial missing-module red test; first implementation had three failing assertions | Corrected spent-ID handling/tuple finite-value validation; final complete suite passes |

No unchanged failure was retried indefinitely. Installation logs 3–6, all framework receipts,
native diagnostics, zero/unknown billing and missing hidden scores remain inspectable. Earlier
exploratory probes did not record an adapter-bundle digest; that field is not retroactively
invented. Final normal/crash manifests and receipts do pin the exact code bundle and source SHAs.

AIRA's official task runner also requires a reviewed MLE-bench installation and its execution
stack. Installing all task dependencies or downloading data simply to make `--help` pass was
not treated as evidence of real task readiness. Full dependency qualification remains open.
Autoresearch was not executed: pinned tree has no LICENSE file and its original NVIDIA workload
is unavailable. No alternative Mac fork was silently substituted.
