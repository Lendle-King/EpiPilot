# Source-grounded semantic onboarding (v2 integration)

Ported from `feat/semantic-research-onboarding` (PR #11, pinned commit
`f0225e595a91f6e63bcb243c76e36d0ce341d040`) into the current
local-first workspace on `main`.

The optional extractor uses **server-only** HTTPS OpenAI-compatible Chat
Completions configuration. It requires explicit user consent in the UI (or
`consent=true` in the local API request) before any Git document excerpt
is transmitted. The local API does not export credentials. Alternative
manual import copies pinned excerpts and requires exact source-line/quote
matches from the immutable Git commit.

The research model's interpretation is **always a candidate**, never canonical
experiment evidence. `semantic_onboarding` reports and review events live in
a separate staging SQLite table. They cannot set a Claim as supported in the
v2 EpiPilot research Event Store.

Start the UI with `pip install -e '.[ui]'`,
`python -m epipilot.workspace.api`, and `cd frontend && npm install && npm run dev`.
Then open a previously adopted research project and select **Semantic onboarding**.

Server-only optional variables:

- `EPIPILOT_RESEARCH_API_BASE_URL` — public HTTPS OpenAI-compatible API URL
- `EPIPILOT_RESEARCH_API_KEY` — secret key (never put in frontend or Git)
- `EPIPILOT_RESEARCH_API_MODEL` — provider-specific model identifier
- `EPIPILOT_RESEARCH_API_FORMAT` — `json_schema` or `json_object`

Privacy notes: even verified source quotes may contain sensitive information.
Review the Git documents and provider terms before granting consent.
No automatic remote transmission occurs when the consent checkbox is not
selected. Model validation checks citation bytes and structure; it does not
establish the scientific truth of the cited claim.

This compatibility port does **not** include PR #11's ETF-specific archival
collection, Browser Bridge/ChatGPT automation, autonomous run loop or old
CARTS engine. Those remain on the preserved source branch pending separate
safety, reproducibility and state-schema review.
