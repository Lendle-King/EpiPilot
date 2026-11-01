# ResearchBench T1 release index — 2026-11-01

This index accompanies the `experiment/researchbench-t1-cost-capped` branch. The
branch contains the T1 controller/evaluator code, tests, protocol, final reports
and the three sanitized pilot receipts. The receipts exclude raw model prompts
and outputs; provider model IDs and token usage are reported only where the
source receipt actually supplied them.

## Local run sidecars reviewed

Eleven sidecar directories in the task workspace were inventoried. Their
per-call traces and CSV/JSON artifacts remain local; they were not force-added
because they have not been audited for raw provider content, private machine
paths, or row-level data. The repository receipts and reports are the published
aggregate result record.

| Local sidecar | Files | Bytes |
|---|---:|---:|
| `T1_Pilot_Results` | 7 | 113,298 |
| `T1_Pilot_Recovery_1` | 8 | 108,377 |
| `T1_Pilot_Recovery_2` | 8 | 110,954 |
| `T1_Pilot_Recovery_3` | 8 | 113,865 |
| `T1_Pilot_Recovery_4` | 8 | 114,128 |
| `T1_Pilot_Recovery_5` | 12 | 111,993 |
| `T1_Pilot_Recovery_6` | 19 | 124,119 |
| `T1_Pilot_Recovery_7` | 29 | 149,918 |
| `T1_Pilot_V2_Results` | 73 | 192,545 |
| `T1_Pilot_V2_Continuation_Results` | 39 | 175,425 |
| `T1_Pilot_V2_Continuation_Results_031a410` | 96 | 299,719 |
| **Total** | **307** | **1,614,341** |

No raw dataset, credential, environment dump, full model conversation, or
row-level candidate trace is included in this branch. The public receipts retain
the experiment identifiers, frozen protocol/data hashes, per-cell outcomes,
call/attempt counts, failure states and result limitations. They do not provide
the complete local per-call trace or candidate source for every attempted
proposal; those remain in the local sidecars.

The reported T1 runs are pilot evidence only. They do not establish a general
performance advantage for EpiPilot over AgentOnly.
