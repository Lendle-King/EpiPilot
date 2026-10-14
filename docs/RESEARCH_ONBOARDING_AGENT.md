# Research Onboarding Agent — grounded semantic intake

The initial workspace UI extracted keyphrase-matched snippets. This extension adds a **model-assisted semantic research onboarding agent** while preserving EpiPilot's evidence-first architecture.

## Core execution

1. Adopt an **existing local Git repository** using the Research Workspace UI. Its selected commit is immutable.
2. Open **Research Onboarding Agent** in the project sidebar.
3. Choose one of two inference paths:
   - **Local model:** optional locally installed [Ollama](https://ollama.com) using its structured-output chat endpoint. Select an installed model (e.g. `qwen3:4b`) and a bounded 1–6 document batch. No direct GitHub/Notion APIs or ChatGPT scraping are used.
   - **Ordinary ChatGPT Chat:** Prepare a source-grounded document packet, copy into ordinary ChatGPT chat, and paste the generated JSON into the UI. The assistant is asked to produce JSON matching the typed schema. All GitHub and Notion remote actions continue through authorized ChatGPT tools.
4. The controller checks every model-reported `source_line` and `exact_quote` against the pinned Git blob; **fabricated citations reject the entire batch**.
5. Normalize exact duplicate propositions while collecting all supporting source locations.
6. Highlight **potential** contradictions for common `topic_key` with opposing `stance`. These are *not* conclusions: status differences may be explained by chronology, data coverage, horizon, metric or experiment conditions.
7. Human-review each claim as a useful research note or reject it. Neither model generation nor human note review automatically creates canonical EpiPilot Evidence, Hypothesis or Fact.

The resulting structured claims distinguish `execution_status` (completed, failed, unknown, not applicable) from `scientific_status` (supported, refuted, blocked, inconclusive, unknown, not assessed). Example: a GitHub workflow can be `completed` while the scientific qualification remains `blocked`.

## Local model usage

Install and run Ollama separately:

    ollama serve
    ollama pull qwen3:4b

Start the existing workspace API and frontend:

    python -m pip install -e '.[ui]'
    python -m epipilot.workspace.api
    cd frontend && npm install && npm run dev

Visit http://127.0.0.1:5173 and select an adopted project. The model endpoint is hardcoded to http://127.0.0.1:11434/api/chat. Remote model endpoints are not configurable through the UI.

The agent scans at most six tracked README/docs/reports/result-summary files per run (max 100 KiB/file); only a bounded opening excerpt is submitted to the model. Per-document models are instructed to extract at most twelve grounded items. This is **partial coverage**, not a full historical archive ingestion; a long report can contain relevant conclusions outside the excerpt. Later improvements should use bounded, section-aware chunking, explicit artifact indexes, and incremental processing.

## ChatGPT manual interoperability

The normal-chat path is intentionally not an unattended ChatGPT automation bridge. Select a document, click **Copy ChatGPT analysis request**, paste it into an ordinary ChatGPT conversation, then import the JSON response through **Validate and import JSON**. The controller rejects unexpected fields, unrecognized source paths, oversized payloads and unmatched citations. Subsequent document imports merge previous candidates, preserving existing human review decisions and their audit events.

Because locally saved source texts may be private, inspect the copied packet before transferring it into ChatGPT. Never supply credentials or protected records. The local model option keeps analysis requests on-device.

## Trust boundaries

- Model-extracted claims are **staged** and remain `independently_verified=false`.
- Citation verification means **the quote existed in a pinned source document**, not that the experiment happened or its conclusion is true.
- Run output is versioned in append-only `semantic_onboarding` rows and `semantic_onboarding_completed` audit events. Review decisions append `semantic_claim_review` events.
- The semantic report is **not the canonical EpiPilot event store**; there is no hidden scientific claim promotion, trading, automatic GitHub writes, or Notion writes.
- Potential contradictions are conservative flags. Before calling a scientific claim resolved, inspect experiment configs, datasets, timestamps, primary artifacts, outcome metrics, code revisions and matched baselines.

## Tests and limitations

    ruff format --check .
    ruff check .
    mypy src
    pytest -q tests/test_research_onboarding_agent.py
    cd frontend && npm run build

Tests use a deterministic fake semantic model: independent source checking and state transitions are tested, **not local model output quality**. A genuine offline-model end-to-end benchmark on quant_etf still needs a local Ollama service and model download.

Additional future work: experiment-level structured metric indexing and source artifact lineage, true semantic contradiction adjudication, user-approved canonical onboarding transactions, and research DAG planning.

References: Ollama structured output docs: https://docs.ollama.com/capabilities/structured-outputs and https://github.com/ollama/ollama/blob/main/docs/api.md
