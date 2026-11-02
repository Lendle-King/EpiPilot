# Evidence-linked Scientific Discovery Core (first refactor slice)

## What this implements

The research plugin keeps a **single authoritative v2 event stream** for goals,
Unknowns, scoped Claims, experiments and independently checked Evidence.
This change introduces a deliberately **read-only** discovery interface on top
of that canonical research state:

- A projected research frontier, showing open unknowns, claim scopes, hypothesis
  status and qualified or unqualified evidence links.
- An explicit *hypothesis comparison* draft: multiple registered Claims must
  predict different, observable outcomes of a controlled proposed intervention.
- A qualitative separation ranker: it counts hypothesis pairs whose predicted
  categorical results differ; it flags nondiscriminating or over-budget designs.
- A reusable numeric-stable candidate fingerprint primitive for deduplication;
  it only merges structural JSON equivalents such as 1 and 1.0.

**These are design tools, not autonomous experiment execution, causal
identification, calibrated uncertainty or actual expected information value.**

## Workflow for an existing coding agent

Initialize a research project and register the competing hypotheses under the
*same deliberately scoped evaluation setting* (the identifiers below are only
examples; adjust to actual project evidence and data):

~~~bash
epi init --goal "Explain why our baseline saturates" --budget 10
epi unknown add --id U1 --question "Why is the benchmark saturated?"
epi claim --id H1 --statement "There is no recoverable method gap" --scope dev-v1
epi claim --id H2 --statement "Method advantage appears on hard cases" --scope dev-v1
epi explore frontier
~~~

Create a local JSON draft under the **target research repository**, e.g.
~diagnostic-matrix.json~:

~~~json
{
  "unknown_id": "U1",
  "scope": "dev-v1",
  "hypothesis_ids": ["H1", "H2"],
  "probes": [
    {
      "id": "difficulty_probe",
      "title": "Difficulty-stratified matched comparison",
      "protocol": "Evaluate preselected hard and easy cases without changing models",
      "control": "Use shared case IDs, initialization and compute budget",
      "measurement": "Measure paired AUROC and failure rate in both strata",
      "decision_rule": "A reliable hard-only gap favors H2; otherwise reconsider H1",
      "estimated_cost": 0.8,
      "risk": 0.1,
      "predictions": {
        "H1": "no_gap",
        "H2": "hard_only_gap"
      }
    }
  ]
}
~~~

Then preview, before creating any experiment, charging any budget or opening a
held-out test:

~~~bash
epi explore discriminate --file diagnostic-matrix.json
~~~

The result ranks proposals by:

1. Feasibility within the currently available project/exploration budget.
2. Number of qualitatively different predicted outcomes across hypothesis pairs.
3. Decision importance of the linked open Unknown.
4. Lower estimated cost, then lower risk, then stable ID.

The ranking **does not** use asserted outcome probabilities or a pretend
information-gain formula. The predicted categories may overlap under noisy
measurement, so pair separation is only a preliminary experimental design
check; it is *not* a claim of statistical power or successful identification.

The proposed design remains a draft. The coding agent should critique its
predictions, ensure measurable contrasts and a suitable sample size, register
an actual exploratory Proposal with the existing CLI, freeze data/control/
baseline/verifier details in a preregistration, reserve resources, run the
study, independently verify measurements, then explicitly update Claim and
Unknown state. Failed and inconclusive studies are retained.

## Integrity boundaries

- The comparison JSON is loaded from a bounded, local path inside the
  research root. Duplicate JSON keys, nonfinite numbers, extra model fields,
  unregistered or inactive Claims, cross-scope references and mismatched
  prediction keys are rejected.
- Existing evidence status is never changed by a preview.
- A comparison may suggest hypotheses that are compatible rather than
  mutually exclusive; use the labels and controls to check whether the
  experiment really separates mechanisms.
- Model-authored descriptions are provisional. Claims can be supported only
  by independently checked, properly scoped experiments.
- The default heuristic policy and event replay schema are unchanged. No
  model calls, code execution, hidden-test reads, provider spending or
  background work are introduced.
- The current ResearchBench-T1 controller is in separate draft PR #25. Its
  experiment receipts and their limitations are not silently rewritten by
  this main-branch refactor.

## Next refactor acceptance gates

1. **Prediction calibration**: record forecast versus measured outcome on
   multiple held-out diagnostic tasks; only then consider an EVSI or learned
   meta-controller.
2. **Task-specific candidate canonicalization**: version equivalence rules
   (e.g. identical numeric feature modes on a numeric-only dataset) rather
   than assume all schema fields or metrics imply identity.
3. **Paired research-efficiency benchmark**: Agent-Only, basic memory,
   current EpiPilot, plus/minus Problem Model, plus/minus active design under
   equal tool permissions, strong baselines, independent tasks and
   fully accounted actual model/compute costs.
4. **Pivot and research branching**: require verified evidence for changing
   scientific direction, preserve superseded hypotheses and resume branches.
5. **Autonomous host integration**: a coding agent can construct and register
   its own draft, but the CLI never assumes permissions it was not granted.

## Verification

~~~bash
ruff format --check .
ruff check .
mypy src
pytest -q tests/research/test_discovery.py
pytest
~~~
