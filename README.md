# EpiPilot

**Evidence-grounded, cost-aware research iteration for coding agents.**

EpiPilot is a lightweight research plugin for **Codex, Kimi Code, and other coding agents**. Keep your existing coding agent as the executor: it reads code, reasons about mechanisms, implements changes, and runs experiments. EpiPilot supplies a **durable claim/unknown/evidence memory**, a **budget-aware research-action selector**, and an **independent verification gate**, so a long-running optimization project can learn from both successful and failed experiments instead of repeatedly starting over.

> **Where is the plugin?** The current cross-agent research skill and CLI live on [`feat/research-plugin-cost-aware-tree-search`](https://github.com/Lendle-King/EpiPilot/tree/feat/research-plugin-cost-aware-tree-search). The default `main` branch currently contains the earlier EpiPilot V0 framework, **not** this CLI. Follow the branch-specific installation below. An **independent Codex-native Agent Plugin** is also available on [`feat/codex-epistemic-research-plugin`](https://github.com/Lendle-King/EpiPilot/tree/feat/codex-epistemic-research-plugin).

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
- **Evidence gates:** experimental observations remain unverified until a separate argv-based check passes; checked evidence still requires scientific interpretation before updating a claim.
- **Agent-native operation:** no second autonomous agent or separate LLM backend is required. Your coding agent executes the research work using its normal tools and approval boundaries.

**Maturity:** This is an MVP. The ranking policy is **not** a trained MCTS or a calibrated probabilistic value-of-information model. Automatic historical scientific synthesis, autonomous experiment execution, and demonstrated performance gains over baseline research policies are **not** claimed. See the [research plugin design and limitations](https://github.com/Lendle-King/EpiPilot/blob/feat/research-plugin-cost-aware-tree-search/docs/RESEARCH_PLUGIN.md).

## Install on Codex or Kimi Code (recommended research skill)

The **same lightweight research CLI + skill** works with either coding agent; this is a skill/instructions integration, **not** a claim that Kimi Code supports the Codex-specific plugin marketplace.

**Requirements:** Python 3.11+, Git, and a coding agent able to read project instructions and run commands in the research workspace.

### 1. Install the EpiPilot research CLI

Clone the branch containing the current research plugin (not `main`):

~~~bash
git clone --branch feat/research-plugin-cost-aware-tree-search --single-branch https://github.com/Lendle-King/EpiPilot.git
cd EpiPilot
python -m pip install -e .
epi --help
~~~

Keep this clone if you use an editable install. If `epi` is not on the agent's `PATH`, use `python -m epipilot.research` with the same Python environment instead. Install the Python package in the environment that your coding agent's terminal actually uses.

### 2. Make the research skill available in your target project

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
~~~

`--budget` uses **experiment cost units defined by your project**; it is not an automatic dollar/token meter. `onboard` inventories files, but your coding agent must inspect actual code, metrics, and artifacts before creating trustworthy research claims.

Then tell Codex or Kimi Code:

> Read `.agents/skills/epipilot-research/SKILL.md`. Use EpiPilot to analyze this repository and its past experiments. Register the important unknowns and falsifiable claims, propose controlled experiments with explicit costs and acceptance checks, select the next cost-effective action with `epi iterate`, execute it yourself, verify the evidence independently, update the claim memory, and repeat within the approved budget. Do not invent results or bypass the project's existing safety and validation requirements.

The CLI supports `epi unknown add`, `epi claim`, `epi propose`, `epi next`, `epi plan`, `epi iterate`, `epi record`, `epi verify`, `epi reflect`, and `epi review`. Use `epi --root /path/to/research/repo <command>` when outside the target repository.

**Research state:** written to `.research/events.jsonl` in the **target repository**. This file is intentionally Git-visible and single-writer; review its contents for credentials, personal information, or private traces before committing it. Do not run concurrent EpiPilot writers against the same research state.

Detailed contracts and examples: [Research plugin documentation](https://github.com/Lendle-King/EpiPilot/blob/feat/research-plugin-cost-aware-tree-search/docs/RESEARCH_PLUGIN.md) · [Research skill source](https://github.com/Lendle-King/EpiPilot/blob/feat/research-plugin-cost-aware-tree-search/skills/epipilot-research/SKILL.md).

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

The default `main` branch currently houses V0 contracts for requirements, decisions, event storage/replay, task DAGs, evidence-gated verification, scheduler policy, memory, context compilation, and a Pi executor. The broader orchestration/UI/runtime roadmap is **not a prerequisite** for using the research skill branch.

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

Select the branch you intend to develop/test before running these commands.

## License

EpiPilot is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution information.
