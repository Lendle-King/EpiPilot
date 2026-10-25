# EpiPilot: Agent-native Research Plugin

**Current branch:** `main` (research plugin). Supports Codex, Kimi Code and
other coding agents that can read instructions and invoke a local CLI.

## Scope

The plugin is a **durable, evidence-aware research decision layer** above an
existing coding agent. The agent remains responsible for interpretation,
coding, experiment execution and scientific criticism. There is no nested
autonomous LLM scientist.

The actual components are `research/models.py` (contracts),
`research/store.py` (append-only event memory), `research/planner.py`
(two-step heuristic), `research/onboarding.py` (bounded inventory),
`research/validation.py` (local git/artifact/verifier checks) and
`research/cli.py` (agent interface).

It supports:

1. Research goals, unknowns and scoped defeasible claims.
2. Project-local event replay, including reading historical v1 streams.
3. Cost-aware candidate ranking with dependencies, feasible lookahead and
   cautious same-kind cost calibration from validated measurements.
4. Pre-registered experimental baselines, controls, metric rules, frozen Git
   revision/config/data references, metric artifact and verifier command.
5. Budget reservation **before** execution and actual-cost accounting even
   if a run overruns its reservation.
6. Explicit running/awaiting-verification/blocked/completed/aborted states;
   corrective retries are recorded, bounded and auditable.
7. Independently re-reading artifact metrics, SHA-256 fingerprints and a frozen
   local argv verifier; checking the recorded metrics against frozen thresholds.
8. Linked, conclusive and explicitly reasoned Claim reviews and Unknown
   resolutions; inconclusive experiments cannot close an Unknown.

## Install

Requires Python 3.11+, Git and an existing coding agent:

```bash
git clone https://github.com/Lendle-King/EpiPilot.git
cd EpiPilot
python -m pip install -e .
epi --help
```

For Codex, copy `skills/epipilot-research/SKILL.md` into the target project's
`.agents/skills/epipilot-research/SKILL.md`. For Kimi Code, ask it to read
the same file or reference it in the project's `AGENTS.md`. The native Codex
marketplace plugin on `feat/codex-epistemic-research-plugin` is a distinct
older integration, **not** a packaging of this research CLI.

## Example end-to-end session

Execute in a **target Git research repository**:

```bash
epi init --goal "Improve task success on a fixed evaluation split" \
  --budget 10 --metric "success rate" --constraint "no evaluation leakage"
epi onboard
epi unknown add --id UNK-1 --question "Does ranking cause failures?"
epi claim --id CLM-1 --statement "Better ranking improves success" --scope "split-v1"
epi propose --id ACT-1 --title "Matched ranking ablation" --kind explore \
  --rationale "Isolate ranking from action policy" \
  --acceptance "Frozen controlled success comparison" \
  --unknown UNK-1 --claim CLM-1 --ig 0.8 --gain 0.3 --cost 1
epi iterate
```

Implement/commit code and experiment configs **before** freezing the study.
Use project-specific baseline and result files:

```bash
epi preregister --proposal ACT-1 --baseline "baseline-v1" \
  --config configs/rank.yaml --dataset data/split-v1.json \
  --control "matched seeds" --control "same compute" \
  --decision-rule "gain >= 0.02 supports the scoped prediction" \
  --falsification "gain < 0.02 refutes the scoped prediction" \
  --metric-rule gain:at_least:0.02 --artifact runs/rank/metrics.json \
  --max-cost 1.5 \
  --verifier python -m json.tool {artifact}
epi start --contract PRE-ID
```

Your coding agent executes the real experiment, which produces an artifact like:

```json
{"metrics": {"gain": 0.032}}
```

Then record real measurements/cost and validate:

```bash
epi record --contract PRE-ID --summary "Controlled ranking comparison" \
  --outcome improved --cost 1.2 --seed 42 --metric gain=0.032
epi verify --experiment EXP-ID
epi reflect --claim CLM-1 --verdict supported --evidence EV-ID \
  --rationale "Metric passed the frozen rule under the matched protocol"
epi unknown resolve --id UNK-1 --evidence EV-ID \
  --rationale "The scoped ranking decision has sufficient checked evidence"
epi review
epi iterate
```

Replace PRE-ID/EXP-ID/EV-ID with actual CLI-returned IDs. Commands such as
`python -m json.tool` verify only syntactic JSON and **must be replaced**
with domain-appropriate checks for a defensible scientific claim.

## Optional controlled-run receipt

For research steps that should bind their metric file to a real EpiPilot-invoked
process, commit a bounded JSON runner specification alongside your code:

```json
{"argv": ["python", "scripts/train.py", "--config", "configs/rank.yaml"], "timeout_seconds": 3600}
```

Pass `--runner-spec runner.json` **before** `--verifier` in
`epi preregister` (the verifier consumes the remaining argv), then:

```bash
epi start --contract PRE-ID
epi execute --contract PRE-ID
epi record --contract PRE-ID --summary "controlled run" --outcome improved --cost 1
epi verify --experiment EXP-ID
```

The host Codex/Kimi session invokes the CLI; EpiPilot does not launch another
research agent. `execute` runs the **frozen** argv with `shell=False` and
records a run-start event before launching, a run receipt afterward, wall
time, exit code, combined output hash and metric-artifact hash. The metrics
path must not exist before launch, and its bytes must match the receipt
during record and verify. An interrupted start with no completed receipt is reported by
`epi doctor` / `epi iterate` and cannot be replayed blindly. A CLI-controlled
run writes its finished receipt first to `.research/receipts/RUN-ID.json` and
then to the event log. If the CLI crashes in that narrow window, restart the
coding agent and run `epi reconcile --contract PRE-ID`. It checks the sidecar's
frozen identity, command, and artifact fingerprint before recording the receipt
event; repeated reconciliation does not relaunch a process. If no complete
sidecar exists, inspect the actual process/job and record an explicit abort
with incurred cost rather than claiming recovery. This is opt-in: existing agent-managed executions continue
to work without a runner specification.

The receipt establishes what this CLI process attempted and which output it
observed; it is **not** tamperproof attestation, an isolated sandbox,
statistical validity, or a guarantee about external remote jobs. Keep the
existing coding-agent approvals and use a domain-specific verifier.

## Evidence and authority limitations

These are three distinct layers:

- **Execution check:** a frozen verifier exits successfully. The command may
  still be a weak or irrelevant check.
- **Measurement check:** EpiPilot verifies artifact existence/fingerprint and
  independently parses numeric metrics against pre-registered thresholds and
  agent-reported values. This detects protocol mismatch, but cannot prove data
  wasn't fabricated or leaked.
- **Scientific interpretation:** the decision rule is explicit and the linked
  provisional Claim can be reviewed. Real validity still requires appropriate
  baselines, variance, causal assumptions, data provenance and independent
  scientific reasoning.

The metric threshold only evaluates the **predeclared bounded prediction**.
Even a supported Claim is *not* a canonical universal Fact. Avoid interpreting
single-run differences as scientific conclusions when statistical power is
inadequate or distribution shift is likely.

Git status checks require a committed, clean tracked worktree at
pre-registration, recording and verification. Local file references are
SHA-256 pinned when files exist; external version IDs remain opaque and
must be verified separately. Metric artifacts are required inside the
repository, relative JSON files with finite numeric metric values. We do not
store raw verifier output, only its hash; execution uses `shell=False` and
cannot bypass the coding agent's ordinary safety approvals.

Legacy v1 `.research/events.jsonl` files replay without silent mutation.
Old "independently verified" evidence is **not** automatically treated as
measurement-validated v2 evidence; re-run and validate key experiments when
scientific conclusions depend on them. Do not edit historical event lines.

## Recovery, budgets and security

- `epi doctor` replays the event log, lists in-flight contracts and reports
  legacy evidence. No background agent runs automatically.
- `epi iterate` surfaces running, preregistered, blocked and
  awaiting-verification items before choosing a new action.
- Budget is reserved with `epi start`. Running experiments consume a
  reservation. `epi record` books the **actual** cost even if it exceeds the
  approved estimate; overspend is visible and blocks new starts.
- `epi abort --contract PRE-ID --reason "..." --cost N` books interrupted
  cost and releases the remaining reservation.
- Invalid verification marks the contract BLOCKED. Use
  `epi retry --contract PRE-ID --reason "specific corrective change"`
  only after making a real correction. Three failed attempts require a
  new proposal and pre-registered experiment.
- The event store is single-writer. A crashed process may leave a
  `.research/.write.lock`; check for live writers before manual recovery.
- A frozen Git revision pins research source, not the evolving research event
  stream. Tracked modifications or commits limited to `.research/**` are
  permitted during execution; changes to any other tracked file remain blocked.
  Config/data files are additionally checked by their frozen fingerprints.
  Do not keep executable research code under `.research/`.
- If independently validated counterevidence makes a previously supported
  Claim inconclusive, v2 replay reopens Unknowns whose resolution relied on
  that Claim. Original resolution evidence is retained and reopening evidence
  is recorded; downstream proposal reevaluation remains a separate step.
- The event file is intentionally visible to Git. Do not save private
  transcripts, secrets, passwords, cookies, private screenshots or raw
  proprietary results. Review before committing it.

## Research-policy limitations

For source-grounded discovery, see [Bounded Research Scout v2](SCOUT_V2.md).
It reserves the same budget as experiments, validates exact Git source lines,
stages provisional hypotheses for explicit review and prevents repeated
no-op searches. It does **not** classify quoted historical reports as
scientifically verified evidence.


The current policy is a deterministic two-step heuristic. It is **not** MCTS,
a trained policy, a calibrated causal inference model or a generic autonomous
science system. Information gain, improvement, novelty and risk remain
agent-supplied ordinal estimates. After at least two validated runs of the
same proposal kind, the policy softly calibrates estimated costs toward the
median measured costs. Outcome likelihoods, deeper conditional VoI, causal
knowledge extraction and cross-project semantic retrieval are future research.

At equal total experimental cost, compare against Agent-only, memory-only,
random proposal selection and greedy optimization. Evaluate held-out outcomes,
repeated experiments, inaccurate scientific conclusions and recovery quality.
No improvement benchmark has been established solely by unit-test passage.
