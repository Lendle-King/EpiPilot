# P0 Mac engineering follow-up — NOT QUALIFIED

This report extends, and does not overwrite, the S0 reports. It records an engineering
calibration, not an S1 comparison. No framework winner or improvement is claimed.

## Status and remaining gates

| Component | Evidence | Qualification |
| --- | --- | --- |
| Agent-Only | Real Codex CLI 0.166.0 is installed in the pinned Linux ARM image; `exec --help` passed. A real command adapter and event parser exist. | No subscription model call or full agent development run has occurred. The subscription transport and request hard cap are not implemented. |
| AIDE ML | Original pinned AIDE entry point, search, interpreter, and backend are used; a no-step startup passed. The real-data adapter is implemented. | No real-model AIDE run has occurred. Its native backend currently needs an API-key-compatible provider; subscription interoperability is unverified. |
| AIRA-dojo Greedy | Pinned source and documented MLE-bench dependency install; `pip check`, official help and config pass. The zero-step official task reaches the missing prepared dataset. | No completed official task or native Jupyter/container parity; dataset access and private scoring integration remain open. |
| EpiPilot | Historical S0 native/mock evidence remains in the S0 reports. | No real-model comparison in this follow-up. |
| autoresearch | Requires NVIDIA/CUDA; no license found in the audited source snapshot. | Excluded on this Mac. |
| Private numeric scorer | CSV-only scorer validates frozen prediction hash, shape, order, finiteness and a keyed attestation. Docker boundary probes passed with synthetic credentials. | Deployment under a distinct trusted identity and end-to-end hidden-test isolation for every framework are not established. |
| Request accounting | API-key Responses gateway reserves per request and tracks retries, input/output usage, wall and estimated token charges. A zero-quota network probe blocked generation. | Subscription authentication/metering, provider billing reconciliation, hard training-trial limits and a shared global live-run lock remain unverified. The API-key path has not made real requests. |

The real runner is an **engineering prototype**. It accepts only `calibration` as a track.
Do not use it to produce formal Native or Matched scores before the above gates are independently
verified. AIDE's native review feedback is not an independent scorer; only the frozen final
candidate is rerun and evaluated independently. AURP is not implemented for real runs.
The runner prompt specifies a trial limit but does not technically enforce it; resource
isolation is not a complete security boundary against a hostile container/kernel escape.

## Machine and calibration

Host: macOS ARM64, 14 logical CPUs, 24 GiB RAM. Docker Linux ARM VM: about 7.75 GiB,
14 CPUs, cgroups v2; no NVIDIA GPU or CUDA. Cells used 2 CPUs and 4 GiB; GPU allocation 0.
The container file-size ulimit had been accidentally set to 16 KiB in earlier probes and is
now 16 MiB; previous S0 artifacts are retained and their original claim is superseded by this
finding. A regression test covers the corrected limit. Calibration runs were sequential.

The UCI Bike Sharing hourly dataset (CC BY 4.0, DOI 10.24432/C5W894), downloaded SHA-256
`b70182d0d0508e9abbb79306ce5c0cec34869000f8220175ac83d11dbe845401`, uses a chronological
13,003/2,208/2,168 train/dev/test split. Test labels are kept outside agent containers and
were not scored. The independent dev scorer measured RMSE 195.997785 (Ridge) and 60.723881
(histogram gradient boosting). These are human-specified references, not agent results;
task saturation and suitability for S1 have not been established.

Final AIRA CPU image: `sha256:63683046bbefd2bcbc57ebe80e6b2b46b49aaf75b2a04e60e91d60eda28b10dc`.
The official zero-step entry failed because `random-acts-of-pizza/prepared/public/description.md`
was absent; no competition dataset or credentials were fetched. The Python interpreter is
a CPU variant, not the native Jupyter environment.

Pinned agent image: `sha256:17b0ce48ff39023cbefd880572f29a0412c4dc95be59df3ea4830bf6c75d6ead`.
AIDE source: `ad73d227af481bea3de06f884f41b2993dd6453a` (MIT). AIRA source:
`c795d8649d30b77fa9b8c011b978bba7127507ad` (CC BY-NC 4.0; non-commercial research
use only); MLE-bench compatibility source: `d0f60ad0d3b2287469ac3c8ac9767330c928c980`
(MIT code; datasets have separate terms). Source checkouts and private runtime artifacts are
outside Git. The source and image manifests must be reverified on a different machine.

## Cost and model access

Real provider requests: **0**; paid API expenditure from this work: **0**, with no API key
used. The user authorized the existing logged-in subscription for later work, not an API-key
billing change. Subscription usage and dollar cost are unknown until a supported, isolated,
request-metered integration is verified. A token estimate is not a provider invoice. No hidden
test score, agent token total, AURP or agent ranking exists.

The earlier [frozen S1 proposal](../S1_FROZEN_PROPOSAL.md) remains a design only. It requires
source/data licenses, strong references and non-saturation tests, actual model/tool parity,
request and compute enforcement, cost information, independent scoring, and explicit S1
approval before launch. Mac CPU feasibility is not yet demonstrated for its three task families.
