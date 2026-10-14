# Research Onboarding Agent — third-party model API

Research onboarding uses an **OpenAI-compatible third-party Chat Completions API**, not local Ollama. This is distinct from the ordinary ChatGPT Chat session used for authorized GitHub/Notion remote actions.

## Quick start

1. Adopt an existing **local Git** repository in the Research Workspace UI; EpiPilot pins its current commit for provenance.
2. Configure the research-analysis provider **on the Python backend**. Never put keys in frontend settings, browser code, Git, Notion, test fixtures, or logs.
3. Open **Research Onboarding Agent → Third-party API**, confirm that the provider is configured, choose an optional model ID and at most six source documents, explicitly consent to sending their excerpts, and click **Analyze project history**.
4. Review structured extracted claims, potential contradictions, exact source quotes, and human acceptance/rejection decisions.
5. Optionally use **Ordinary ChatGPT** to manually analyze and import one source at a time without invoking the configured third-party API.

Python backend and frontend (two terminals):

    pip install -e '.[ui]'
    python -m epipilot.workspace.api

    cd frontend
    npm install
    npm run dev

Open http://127.0.0.1:5173. API listens only on 127.0.0.1:8787.

## Third-party API configuration

Required backend environment variables:

| Variable | Description |
|---|---|
| EPIPILOT_RESEARCH_API_BASE_URL | HTTPS OpenAI-compatible API **base URL**, without /chat/completions |
| EPIPILOT_RESEARCH_API_KEY | Bearer key; server-only secret |
| EPIPILOT_RESEARCH_API_MODEL | Default remote model identifier |
| EPIPILOT_RESEARCH_API_FORMAT | Optional: json_schema (default) or json_object |
| EPIPILOT_RESEARCH_API_TIMEOUT | Optional request timeout, 1–180 seconds (default 120) |
| EPIPILOT_RESEARCH_API_MAX_TOKENS | Optional max_tokens, 1–16000; omit if provider does not accept this parameter |

Supported examples (provider model and format availability must be verified for your account):

### OpenRouter

    export EPIPILOT_RESEARCH_API_BASE_URL=https://openrouter.ai/api/v1
    export EPIPILOT_RESEARCH_API_MODEL=openai/gpt-4o
    export EPIPILOT_RESEARCH_API_FORMAT=json_schema

The chosen OpenRouter provider endpoint must support strict JSON Schema. OpenRouter recommends choosing a model/provider with structured outputs support; not every hosted model has it.

### DeepSeek

    export EPIPILOT_RESEARCH_API_BASE_URL=https://api.deepseek.com
    export EPIPILOT_RESEARCH_API_MODEL=deepseek-chat
    export EPIPILOT_RESEARCH_API_FORMAT=json_object

DeepSeek documents OpenAI-compatible JSON object mode. The local controller still validates the entire returned JSON against the Pydantic claim schema.

### SiliconFlow

    export EPIPILOT_RESEARCH_API_BASE_URL=https://api.siliconflow.cn/v1
    export EPIPILOT_RESEARCH_API_MODEL=YOUR_PROVIDER_MODEL_ID
    export EPIPILOT_RESEARCH_API_FORMAT=json_object

SiliconFlow documents both JSON object and JSON schema modes for supported models; use json_schema only for models that support that mode.

**Set your API key securely**, for example in Bash/Zsh without echoing it:

    read -rs EPIPILOT_RESEARCH_API_KEY
    export EPIPILOT_RESEARCH_API_KEY

For Windows PowerShell, set the URL/model/format with $env:VARIABLE_NAME and obtain the API key from your secret manager, avoiding literal keys in shell history. Environment variables are inherited by the Python process; restarting the backend is necessary after changing its environment.

**No user-supplied API key or arbitrary URL is accepted through the UI.** The server requires an externally configured public HTTPS endpoint; URL credentials, redirects and non-public IP literals are rejected. A backend-only GET /api/research-model/config endpoint exposes the provider hostname, model ID, output format and whether the key is configured — never the key value.

## Structured model output and scientific trust

A provider request uses POST /chat/completions with an Authorization bearer header, a research-history system prompt, one bounded source excerpt and response_format:

- json_schema: strict JSON Schema where supported. EpiPilot removes schema keywords unsupported by many strict-output dialects, retaining all local Pydantic constraints.
- json_object: broader provider compatibility. It guarantees only a JSON object (per-provider behavior), **not** the scientific claim schema; EpiPilot validates the response strictly before persistence.

No unsupported mode is silently retried or downgraded. Configuration/auth/rate-limit errors are surfaced without logging the bearer secret or raw provider response. Each request can incur usage fees. Providers may retain the transmitted research data under their own policies; only send content you are authorized to disclose.

The selected snapshot has at most six tracked documents, 100 KiB/file, limited opening excerpts, and at most twelve structured claims/document. The source quote and line must appear **exactly** in the pinned Git commit; invalid citations reject a whole batch. Identical propositions are merged with all original citations preserved, and potentially opposite stances on a common research topic are flagged for human adjudication.

**A citation match only proves that the *document says it*.** It does not verify execution artifacts, portfolio metrics, as-of market-data availability, statistical significance, or reproducibility. The onboarding stage writes versioned *candidate* reports and append-only note review events, not canonical EpiPilot Evidence, Fact, Hypothesis, Task or live-trading actions.

## Ordinary ChatGPT Chat

The manual option is unchanged: prepare a pinned source+schema packet, paste it into ordinary ChatGPT chat, then paste back the structured JSON. The same original-source checks and audit logic apply. ChatGPT-connected GitHub/Notion capabilities remain separate from the model-analysis API. EpiPilot does not programmatically drive or scrape the ChatGPT Web chat.

## Tests

    ruff format --check .
    ruff check .
    mypy src
    pytest -q tests/test_research_onboarding_agent.py tests/test_third_party_research_model.py
    cd frontend && npm run build

API client tests use a fake transport and never spend API credits or contact a provider. A live provider E2E run, private-data review and a full quant_etf history reconstruction remain separately necessary.

References:
- https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat
- https://openrouter.ai/docs/guides/features/structured-outputs
- https://api-docs.deepseek.com/zh-cn/news/news0725/
- https://api-docs.siliconflow.cn/docs/userguide/guides/json-mode
