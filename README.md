# EpiPilot

**Evidence-grounded, cost-aware research iteration for coding agents.**

EpiPilot is a lightweight research plugin for **Codex, Kimi Code, and other coding agents**. Keep your existing coding agent as the executor: it reads code, reasons about mechanisms, implements changes, and runs experiments. EpiPilot supplies a **durable claim/unknown/evidence memory**, a **budget-aware research-action selector**, and an **independent verification gate**, so a long-running optimization project can learn from both successful and failed experiments instead of repeatedly starting over.

> **Default branch:** `main` now includes the lightweight research CLI, agent skill, and the underlying EpiPilot framework. The previous V0-only main snapshot is preserved at [`legacy/main-before-research-plugin`](https://github.com/Lendle-King/EpiPilot/tree/legacy/main-before-research-plugin). A **separate Codex-native Agent Plugin** remains available on [`feat/codex-epistemic-research-plugin`](https://github.com/Lendle-King/EpiPilot/tree/feat/codex-epistemic-research-plugin).

## Why use EpiPilot?

Research is not just a sequence of coding tasks. It involves uncertain explanations, expensive experiments, conflicting results, and decisions about *what is worth trying next*.

~~~text
Research goal + budget + constraints
          |
          v
  Inspect repository and past experiments
          |
          v
  Unknowns <-> falsifiable, scoped claims
          |
          v
  Candidate experiments and improvements
          |
          v
  Cost-aware selection (explore / optimize / verify)
          |
          v
  Codex / Kimi Code / other agent executes
          |
          v
  Record real measurements and artifacts
          |
          v
  Independent check -> review evidence -> update claims
          |
          +-----------------------> next research action
~~~

The research skill/CLI currently provides:

- **Research onboarding:** a bounded, read-only repository inventory, followed by *agent-led* inspection of historical results. Filenames and summaries are not treated as verified findings.
- **Persistent research memory:** goals, constraints, unknowns, scoped claims, proposals, experiments, and linked evidence in a replay-validated append-only log.
- **Cost-aware prioritization:** explicit information-gain, expected-improvement, cost, risk, novelty, and dependency signals; a deterministic two-step heuristic with a discounted follow-on value.
- **Exploration-to-optimization balance:** heuristic phase selection based on remaining uncertainty and independently checked evidence, not a fixed number of iterations.
- **Frozen experimental protocols:** preregister metrics, thresholds, controls, Git revision, config/data fingerprints, cost ceiling and verifier argv before execution. Reserve budget at start; record actual costs even after overruns.
- **Layered evidence gates:** confirm reproducible artifacts, independently parse measured metrics and run the frozen checker; the linked, conclusive result may then support a provisional claim. Syntax checks alone are not scientific proof.
- **Failure recovery:** surface blocked, running and awaiting-verification studies, require a recorded corrective strategy for retries, and limit repeated failures.
- **Agent-native operation:** no second autonomous agent or separate LLM backend is required. Your coding agent executes the research work using its normal tools and approval boundaries.

**Maturity:** This is an MVP. Metric-threshold checks do **not** establish causal or statistical validity; historical v1 verification flags do not become v2-qualified measurements. The ordinary `epi iterate` planner remains a deterministic two-step heuristic. An optional Scout-inspired **decision table / conditional information-value** analysis now supports frozen forecasts and calibration from qualified v2 results, but is **not** trained MCTS. Source-grounded onboarding produces *reviewable candidate notes*, not verified claims. No unattended scientific discovery or measured research-efficiency improvement is claimed. See [Research plugin limitations](docs/RESEARCH_PLUGIN.md) and [integration status](docs/INTEGRATION_STATUS.md).

## Install on Codex or Kimi Code (recommended research skill)

The **same lightweight research CLI + skill** works with either coding agent; this is a skill/instructions integration, **not** a claim that Kimi Code supports the Codex-specific plugin marketplace.

**Requirements:** Python 3.11+, Git, and a coding agent able to read project instructions and run commands in the research workspace.

### 1. Install the EpiPilot research CLI

Clone the default `main` branch, which contains the research plugin:

~~~bash
git clone https://github.com/Lendle-King/EpiPilot.git
cd EpiPilot
python -m pip install -e .
epi --help
~~~

Keep this clone if you use an editable install. If `epi` is not on the agent's `PATH`, use `python -m epipilot.research` with the same Python environment instead. Install the Python package in the environment that your coding agent's terminal actually uses.

### 2. Make the research skill available in your target project

**Recommended single-command setup** (run after installing the Python runtime):

~~~bash
epipilot-install-agent --project /path/to/research/project --agent both
~~~

For Codex only, use `--agent codex`; for Kimi Code only, use `--agent kimi`.
Use `--dry-run` first to inspect changes, or `--json` for machine-readable output.
The installer copies a versioned project skill without overwriting user-modified
skills unless `--force` is explicitly passed, and conservatively adds a bounded
EpiPilot section to `AGENTS.md` for Kimi. It does **not** install or claim to
enable a Codex-native marketplace plugin. Start a **new agent session** afterward.

**Manual fallback**:

Run the following **inside the repository you want EpiPilot to research**, replacing `/path/to/EpiPilot` with the clone from step 1:

~~~bash
mkdir -p .agents/skills/epipilot-research
cp /path/to/EpiPilot/skills/epipilot-research/SKILL.md .agents/skills/epipilot-research/SKILL.md
~~~

For Windows PowerShell, the equivalent is:

~~~powershell
New-Item -ItemType Directory -Force .agents/skills/epipilot-research
Copy-Item C:\path\to\EpiPilot\skills\epipilot-research\SKILL.md .agents/skills/epipilot-research/SKILL.md
~~~

**Codex:** `.agents/skills/epipilot-research/SKILL.md` is a project-local agent skill. Start a new Codex session in the target repository and ask it to use **epipilot-research**. The CLI does the stateful work; the skill tells Codex when to call it.

**Kimi Code:** ask Kimi Code to **read `.agents/skills/epipilot-research/SKILL.md` explicitly** before research work, or copy the skill to the Skills directory recognized by your installed Kimi Code version. For persistent project instructions, add the following to the target project's existing `AGENTS.md` (create it if needed; do not overwrite other instructions):

~~~markdown
## EpiPilot research workflow

For research or algorithm optimization tasks, first read
`.agents/skills/epipilot-research/SKILL.md` and follow its operating loop.
Use the installed `epi` CLI to manage research goals, unknowns, claims,
proposals, experiments, evidence and budgets. Execute experiments with this
coding agent; never invent measurements or treat self-report as verified evidence.
~~~

If the agent does not automatically load `AGENTS.md` or the skill, include an explicit instruction to read the skill at the beginning of the conversation.

### 3. Initialize a research project

From the **target research repository** (not the EpiPilot source clone):

~~~bash
epi init --goal "Identify the bottlenecks and improve this algorithm" --budget 20 \
  --metric "held-out task success and experiment cost" \
  --constraint "do not use held-out results for model selection"
epi onboard
epi status
# Then register a scoped Unknown, Claim, and experiment Proposal.
# After preparing and committing the actual research code/config:
# epi preregister --proposal ACT-ID ... --metric-rule score:at_least:0.8 \
#   --artifact results/metrics.json --max-cost 2 \
#   --verifier python -m json.tool {artifact}
# epi start --contract PRE-ID
# The coding agent runs the experiment and writes the frozen metrics JSON.
# epi record --contract PRE-ID --summary ... --outcome improved --cost 1
# epi verify --experiment EXP-ID
~~~

`--budget` uses **experiment cost units defined by your project**; it is not an automatic dollar/token meter. `onboard` inventories files, but your coding agent must inspect actual code, metrics, and artifacts before creating trustworthy research claims.

Then tell Codex or Kimi Code:

> Read `.agents/skills/epipilot-research/SKILL.md`. Use EpiPilot to analyze this repository and its past experiments. Register the important unknowns and falsifiable claims, propose controlled experiments with explicit costs and acceptance checks, select the next cost-effective action with `epi iterate`, execute it yourself, verify the evidence independently, update the claim memory, and repeat within the approved budget. Do not invent results or bypass the project's existing safety and validation requirements.

The CLI supports `epi unknown add`, `epi claim`, `epi propose`, `epi preregister`, `epi start`, `epi record`, `epi verify`, `epi retry`, `epi abort`, `epi reflect`, `epi unknown resolve`, `epi review`, `epi doctor`, `epi next`, `epi plan`, and `epi iterate`. Research actions that spend resources must first have a frozen experiment contract and budget reservation. Use `epi --root /path/to/research/repo <command>` when outside the target repository.

**Research state:** written to `.research/events.jsonl` in the **target repository**. This file is intentionally Git-visible and single-writer; review its contents for credentials, personal information, or private traces before committing it. Do not run concurrent EpiPilot writers against the same research state.

Detailed contracts and examples: [Research plugin documentation](https://github.com/Lendle-King/EpiPilot/blob/main/docs/RESEARCH_PLUGIN.md) · [Research skill source](https://github.com/Lendle-King/EpiPilot/blob/main/skills/epipilot-research/SKILL.md).

## Optional Scout-inspired decision analysis

EpiPilot now also supports **source-grounded, cost-reserved Scout discovery**,
driven by the **same coding agent**, with bounded committed-document anchors:

~~~bash
epi onboard
epi scout prepare
epi scout start --operation SCOUT-1 --reserve 0.5
# The coding agent studies real committed reports and writes a JSON submission.
epi scout submit --file scout-submission.json
epi scout review --candidate CANDIDATE-ID --decision accepted \
  --reason "Novel, falsifiable and within the approved experiment budget"
epi iterate
~~~

A review merely creates an unverified Unknown/Claim/Proposal; it cannot
bypass frozen experiment contracts or independent measurement validation.
The same frontier cannot be scouted again merely by renaming a failed/no-op
operation. See [Scout v2 protocol](docs/SCOUT_V2.md).


After registering a `Proposal`, a coding agent can prepare a **four-outcome
decision table** as local JSON (`improved / no_gain / inconclusive /
infrastructure_failure`) containing normalized conditional utilities,
probabilities, a non-executing stop option and bounded follow-on actions.
EpiPilot validates and records this table before a preregistered experiment:

~~~bash
epi decision register --file decision-table.json
epi decision rank
epi decision status
~~~

These scores are advisory: `epi iterate` still uses the evidence-gated
budget-aware planner. A decision estimate is frozen with its experiment
contract; outcome calibration counts only validated v2 measurements from the
matching predictor/utility version/group. This is **not** evidence that
model-predicted probabilities are correct or that the research is causal.
See [Scout-inspired decision code](src/epipilot/research/decision_value.py).

## Optional problem exploration mode

Before optimizing an unfamiliar research problem, explicitly enable an
evidence-gated **Problem Exploration Mode**. It reuses the same Scout, Claim,
Proposal, frozen experiment and budget system; no second agent is launched:

~~~bash
epi explore enable --budget-share 0.20
epi explore status
epi explore probes
epi iterate
# After exploratory experiments are independently verified and reviewed:
epi explore profile
epi explore advance --reason "Important unknowns resolved by reviewed evidence"
~~~

During exploration, only exploratory proposals may be registered and started;
the cap covers both diagnostic experiments and Scout. The \`epi explore profile\`
projection links scoped experimental Claims to advisory algorithm
recommendations. Unverified historical reports cannot become validated
knowledge. If the cap is exhausted, the CLI reports insufficient evidence
rather than treating budget exhaustion as readiness. Advancing early requires
a recorded \`--override\`. New important counterevidence can reopen discovery.

See [Problem Exploration Mode](docs/PROBLEM_EXPLORATION.md) for lifecycle,
probe types, scientific limits and commands.

## Optional local Research Workspace

A separate **React + FastAPI** interface can create/adopt local research
projects, stage observations from pinned Git history, show a source-lineage
graph and offer human review. Its **Semantic onboarding** tab supports
manually imported source-grounded JSON or an opt-in third-party
OpenAI-compatible model. Any remote transmission of source excerpts
requires explicit consent; the API key is server-only.

~~~bash
python -m pip install -e '.[ui]'
python -m epipilot.workspace.api
# in another terminal
cd frontend && npm install && npm run dev
~~~

Open `http://127.0.0.1:5173`. Workspace candidates are saved in a separate
staging SQLite store and **cannot set the CLI's canonical v2 Evidence/Claim
status**. The UI does not run unattended research, connect to trading APIs or
change GitHub/Notion on its own. See [workspace guide](docs/RESEARCH_WORKSPACE.md)
and [semantic onboarding boundaries](docs/SEMANTIC_ONBOARDING_INTEGRATION.md).

## Alternative: native Codex Agent Plugin (separate integration)

For Codex versions that support `codex plugin`, an **earlier, separate Codex-native integration** provides a plugin manifest, a bounded MCP tool surface, and a durable SQLite-backed epistemic workflow. **It is not the same implementation as the cost-aware research CLI above** and should not be represented as shipping that CLI's research-action ranking.

Requirements: Python 3.11+, [uv/uvx](https://github.com/astral-sh/uv), and a Codex build with Plugins support. Install directly from its own development branch:

~~~bash
uvx --from "git+https://github.com/Lendle-King/EpiPilot.git@feat/codex-epistemic-research-plugin" epipilot-install-codex --ref feat/codex-epistemic-research-plugin --json
~~~

The bootstrap installs the runtime, registers the Codex marketplace/plugin, and checks whether Codex reports it as installed and enabled. Start a **new Codex thread** afterwards. This native Codex marketplace flow is **not** an installation method for Kimi Code.

See [Codex installation guide](https://github.com/Lendle-King/EpiPilot/blob/feat/codex-epistemic-research-plugin/CODEX_INSTALL.md) and [Codex plugin reference](https://github.com/Lendle-King/EpiPilot/blob/feat/codex-epistemic-research-plugin/docs/CODEX_PLUGIN.md).

## Underlying EpiPilot framework

The research plugin builds on the broader EpiPilot goal of **evidence-driven epistemic orchestration** for long-horizon coding agents:

~~~text
Goal -> Requirements -> Unknowns/Hypotheses -> Plan -> Execute
     -> Observe -> Verify -> Evidence -> Belief Update -> Replan
~~~

Its core principles are:

1. **No completion without evidence.** An executor's self-report alone is not a canonical fact.
2. **Traceability.** Plans, hypotheses and decisions must have explicit provenance.
3. **Separation of concerns.** Canonical state, durable memory and the agent's working context are different.
4. **No blind retries.** A failed experiment must be informed by new evidence or a changed strategy before repetition.
5. **Independent acceptance.** A passing process check proves only what that check actually validates; it is not by itself a general scientific conclusion.

The `main` branch combines the lightweight research plugin with the earlier V0 framework contracts for requirements, decisions, event storage/replay, task DAGs, evidence-gated verification, scheduler policy, memory, context compilation, and a Pi executor. The pre-plugin main snapshot remains available on [`legacy/main-before-research-plugin`](https://github.com/Lendle-King/EpiPilot/tree/legacy/main-before-research-plugin). The broader orchestration/UI/runtime roadmap is **not a prerequisite** for using the research skill.

Architecture and contributor references:

- [Framework](docs/FRAMEWORK.md) · [Architecture](docs/ARCHITECTURE.md) · [Memory](docs/MEMORY.md) · [Roadmap](docs/ROADMAP.md)
- [Contributing](CONTRIBUTING.md) · [Agent instructions](AGENTS.md) · [Security](SECURITY.md)

### Development

~~~bash
python -m pip install -e '.[dev]'
ruff format --check .
ruff check .
mypy src
pytest
~~~

Run these commands from `main` (or from the feature branch you are actively developing).

## License

EpiPilot is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution information.
