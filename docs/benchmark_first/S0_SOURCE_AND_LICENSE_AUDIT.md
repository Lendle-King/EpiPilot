# S0 source and license audit — 2026-11-04

Engineering-only. No dataset download, paid provider call or scientific comparison was run.
The requested branch base is `d20c891a00e4904ff9a262b1eeadc3328cd7f776`.
The research skill and both Benchmark-First specifications were read before changes.

| Framework | Exact source revision | License inspected | Dependency/runtime findings |
|---|---|---|---|
| Agent-Only seam | `d20c891a00e4904ff9a262b1eeadc3328cd7f776` | Apache-2.0, repository LICENSE/NOTICE | Scripted offline host executor; no claim that a real Codex session/model transport was qualified |
| AIDE ML | `ad73d227af481bea3de06f884f41b2993dd6453a` | MIT, upstream LICENSE | requirements.txt/setup.py reviewed; Python >=3.10; native code/feedback backends differ; optional broad ML dependencies are not all installed |
| AIRA-dojo Greedy | `c795d8649d30b77fa9b8c011b978bba7127507ad` | CC BY-NC 4.0 plus THIRD_PARTY_LICENSES.md | environment.yaml requests Python 3.12; package permits >=3.11; requirements.txt reviewed; MLE-bench and native execution infrastructure absent |
| Current EpiPilot | `d20c891a00e4904ff9a262b1eeadc3328cd7f776` | Apache-2.0 | pydantic; existing native planner, append-only research store, preregistration, record/verify and abort exercised |
| autoresearch | `228791fb499afffb54b46200aca536f79142f117` | No LICENSE in pinned tracked tree; permission unresolved | pyproject.toml/uv.lock reviewed; original CUDA torch 2.9.1 workload; no NVIDIA GPU on this Mac, excluded |

Sources: [AIDE](https://github.com/WecoAI/aideml/tree/ad73d227af481bea3de06f884f41b2993dd6453a),
[AIRA](https://github.com/facebookresearch/aira-dojo/tree/c795d8649d30b77fa9b8c011b978bba7127507ad),
[autoresearch](https://github.com/karpathy/autoresearch/tree/228791fb499afffb54b46200aca536f79142f117).
No third-party implementation or license text was relicensed/copied into EpiPilot.
AIRA is restricted to eligible noncommercial research; commercial permission is **not** established.
Its pyproject MIT classifier conflicts with LICENSE; LICENSE/README restrictions take precedence in
this audit. Deployment/redistribution requires the operator to confirm permitted use.

## Environment and source verification

Observed host: Darwin 24.6.0 ARM64, 14 logical CPUs, 24 GiB physical RAM; host test Python 3.12.7.
Docker 29.9.2: Linux aarch64 VM, 14 CPUs, 8,318,976,000 bytes configured RAM.
Container Python 3.11; no NVIDIA CUDA/GPU. This is the available Mac, not an assumed research server.
`/srv` is absent, so public checkouts and private raw engineering logs use a separate local
`epipilot-benchmark-workspace` directory. No privileged host installation was performed.

Clones used `git clone --no-checkout --filter=blob:none URL CHECKOUT` followed by
`git checkout --detach EXACT_SHA`. EpiPilot uses a shared local clone with its public origin URL.
Every native run checks checkout root, origin URL, exact HEAD and tracked/untracked/**ignored** dirt.
Each cell gets a separate detached framework worktree and a separate synthetic task Git worktree.
The task baseline commit has fixed author/time; original user checkout was not staged/stashed/reset.

The old example's EpiPilot SHA `b7633a0d4b785fa8989cc34afdeeb752c1af0268` intentionally differs
from the requested branch. Its preflight reported the mismatch, not success. The new offline
config explicitly pins d20c891; `git diff b7633a0..d20c891 -- src/epipilot/research` is empty.
The example task hashes remain placeholders and were never used to authorize execution.

## Installation and risky paths

Inspected the frameworks' provider clients, native step/search functions, interpreter/subprocess
paths, config loading, preprocessing and setup scripts. Risks include generated-code execution,
archive extraction, network dataset/model downloads, provider retries and AIRA shell-based hardware
probes. Runtime never invokes a download, Slurm job, Apptainer image or real provider backend.
AIDE's original code/feedback query function and AIRA's client constructor boundary are mocked;
search policies, prompt operators and journals remain native. Unsupported paths are not emulated.

The first image build timed out downloading public dependencies. A second attempted combined
image failed because AIDE jsonschema 4.19.2 conflicts with AIRA LiteLLM's >=4.22 requirement.
The final Dockerfile has separate `aide` and `aira` targets, with explicit divergent native pins.
No dependency constraint was silently unified. Image IDs and full resolved Python packages are
recorded beside this report. This is a minimal import/synthetic-smoke installation, not proof
that every ML workload or the complete upstream requirements set is runnable. Debian build
repositories and transitive wheel availability may change; recorded local image IDs identify
what actually ran, while the Dockerfile alone is not a bit-for-bit rebuild guarantee.

Runtime: non-root UID, network none, no credentials or Docker socket, read-only root/framework/
harness, bounded writable synthetic work area; 2 CPUs, 2 GiB RAM/swap limit, 64 PIDs, 90 s per
process and a stricter-than-1800 s combined two-process smoke. Whole-host memory pressure is
sampled at 1 s and stops at 95%; that is not an absolute host-wide OS reservation.

## Backend eligibility

AIDE declares OpenAI, Anthropic, OpenRouter and Gemini-compatible paths. AIRA contains OpenAI,
LiteLLM/Azure and Google clients with internal retries. These are source capabilities only.
No credential was printed, forwarded or authenticated to a provider. Exact live model IDs,
reasoning settings, billing and cross-framework parity remain unverified. Subscription access
is not API spending authorization. All matched-track eligibility remains false.
