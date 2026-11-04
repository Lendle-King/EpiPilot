# S1 frozen design proposal v1 — NOT AUTHORIZED

The proposal is frozen in `experiments/benchmark_first/s1_design_frozen.json`; its SHA-256 is
listed in the S0 artifact index. This is a **design freeze**, not a runnable manifest. Missing
licenses/data hashes, measured baselines, model access, prices and private evaluator attestations
remain null; the strict executable schemas must not be bypassed by filling in fictitious hashes.
S0 currently fails, so no approval request to launch paid work is appropriate yet.

Recommend screening three independent tasks across text, tabular and graph regression, four
arms and one fresh session per cell: **12 cells per track**. Matched and Native each have their
own planned table, gate and budget. Running both would require approval for 24 cells; approval
for one never authorizes the other. No architecture ranking may mix them.

- MLE-bench `507f92e1138bb6e40dac5c6ee7a6758e6424bf97`: Spooky Author Identification (log loss),
  Tabular Playground December 2021 (accuracy). Code is MIT; competition data rights and splits
  are separate and not yet cleared/downloaded. Candidates, not final task selections.
- AIRS-Bench `18e4f1d501069cf7d7e2740d81c2ca748c56a6a1`: GraphRegressionZincMae (MAE). Source
  repository located via [paper v3](https://arxiv.org/abs/2602.06855v3); data license, hardware,
  evaluation implementation and reference reproduction remain review gates.
- [RE-Bench](https://github.com/METR/RE-Bench) `a0cc6d4a91b20f84ebef505d5c72358f88b3b389`:
  official repository is archived/deprecated. MIT code is distinct from its requests to avoid
  contaminating evaluation material. No protected solutions or test data were opened. Its GPU
  tasks are not selected for this Mac screen.

Task difficulty is **unmeasured locally**. Before selection, use a separate calibration set to
reproduce strong references and simple heuristic search under each proposed compute cap. Reject
ceiling tasks or infeasible workloads by an outcome-blind registered rule; preserve rejection
records and revise the protocol. Do not use the synthetic slope task, R1/WDBC or paper headline
scores to establish framework superiority. No official hidden feedback is used for calibration.

Provisional ceiling per cell: 4 attempted requests including all retries, 3 dev trials, 1800 s
wall, 600 s training, 8192 input and 2048 output/reasoning tokens per attempt, two CPUs/4 GiB,
zero GPU. Native per-operator clients may require different limits; that requires a revised
frozen proposal, pricing and approval. These limits are not promises that a strong reference
fits this machine. Use sequential randomized cell order and identical baseline Git commits,
editable files, public task information and developer feedback within each track.

Per track maximum: 48 requests, 36 dev trials, 393216 input tokens, 98304 output/reasoning tokens,
2 training wall-hours and 6 aggregate cell wall-hours. Prices are unknown; estimated USD is null,
**approved exposure is $0 and approved paid requests are zero**. The token-only formula is
`0.393216*P_input_per_million + 0.098304*P_output_per_million`; heterogeneous native models,
provider/tool fees and compute require separate measured line items. No dollar saving is claimed.

Before approval: audit a quota proxy/provider hard cap, verify exact backbone parity for Matched,
freeze independent scorer/data/code digests, review data terms, reproduce references, quote
actual prices and submit total dollar exposure. A subprocess count cannot enforce provider cost.

Independent scoring closes sessions first, freezes historical checkpoints, runs untrusted
inference separately from trusted numeric scoring and returns source-bound attestations. Report
held-out quality, AURP against the same full wall budget, tokens, all request attempts, training
and wall/CPU/GPU time, peak memory, failures, duplicates and repair costs; null remains null.
No failed/empty cell is omitted or refilled. S1 supports feasibility selection only. Later B0,
B1 memory, B2 problem/competing hypotheses and B3 adaptive control require individual ablations,
equal-overhead controls and final confirmation on unseen task families. None is implemented or
claimed superior in this S0 change.
