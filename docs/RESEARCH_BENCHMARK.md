# Equal-budget research policy benchmark

This is a conservative COMPARISON harness, not a claim that EpiPilot improves science. CI fixtures are synthetic and do not measure Agent quality.

Use it to compare bare Coding Agent, memory-only, greedy, EpiPilot heuristic, and conditional decision policy on the same frozen tasks. Supply external evaluator-produced result files. Fix the coding model, tools, task/seed set, evaluator, dataset, and per-case budget before starting.

Create a manifest.json inside the evaluation directory, with schema_version=1, name, dataset, evaluator, metric, direction (maximize or minimize), budget_unit, per_case_budget, baseline, policies, cases, and runs. Each case is {case_id, seed}. Every run includes policy, case_id, seed, artifact_ref, artifact_sha256, actual_cost, human_interventions, repeated_experiments and unsupported_claims.

The evaluation artifact is a small JSON object with exactly case_id, seed, dataset, evaluator, metric, value. Produce it with an INDEPENDENT frozen evaluator, never based solely on an executor's claim. For a binary success task the evaluator may record a failed attempt as value=0. All cases must be recorded, including failures; missing evaluations cause the harness to refuse comparison rather than drop hard tasks.

Run: epi --root /evaluation/directory benchmark --manifest manifest.json --min-cases 8

The tool checks complete matched coverage, correct identity, stable metric file SHA, numeric values, and equal per-case budget ceilings. It calculates mean score, mean and total cost, paired improvement and wins/losses/ties against the baseline, intervention counts, duplicate runs and unsupported claims.

Important: a hash only checks a file against a stated hash. It cannot prove an independent evaluator created it or that a reported actual_cost is genuine. Do not claim a true efficiency gain without validating provenance, final holdout, cost records, calibration, repeated tests, and statistical uncertainty. Running the provided synthetic CI fixtures is NOT an empirical benchmark.