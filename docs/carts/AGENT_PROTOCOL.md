# Agent-native CARTS integration

The lightweight `feat/research-plugin-cost-aware-tree-search` workflow is adapted to the
existing CARTS backend. Its independent JSONL `ResearchState`/claim store is deliberately
not copied. All canonical entities and frontier metadata replay from the existing SQLite
`project_events`; discovery batches commit in one optimistic transaction. Legacy route,
checkpoint and workspace history remain intact.

An existing coding agent supplies semantic discovery and implementation. The protocol
returns a bounded next-action packet and never asks an unconfigured model to invent state.
This is an agent tool/skill, not an unattended semantic daemon. The host coding agent must
continue calling it; a scheduled host must be configured separately if desired.

Install the package in its Python environment (`pip install -e '.[dev]'`). Load the portable
[research skill](../../skills/epipilot-research/SKILL.md) in the coding agent. Module usage
works without installing the `epi` entry point:

```bash
python -m epipilot.research agent --root /path/to/research --project PROJECT --db /path/to/workspace.sqlite3 onboard
python -m epipilot.research agent --root /path/to/research --project PROJECT --db /path/to/workspace.sqlite3 init --goal 'An explicit authorized research goal' --wall-seconds 120
python -m epipilot.research agent --root /path/to/research --project PROJECT --db /path/to/workspace.sqlite3 iterate
python -m epipilot.research agent --root /path/to/research --project PROJECT --db /path/to/workspace.sqlite3 schema
```

`schema` emits the complete strict DiscoveryBatch JSON schema. The packet's event_version
must equal the batch's based_on_version. basis_ref must reference an existing canonical
requirement, user decision or evidence. Each Finding supplies canonical hypothesis/node,
unknown/question, facet (property, variable, boundary, bottleneck or method), stable topic
key, two distinct explanations, predictions and falsification conditions. Each SourceAnchor
contains a 40-character Git revision, relative path, one-based line and exact UTF-8 line.
Quotes validate wording, not scientific truth. Competing explanations persist on Question.

Each AgentProposal combines an ExperimentProposal and MeasurementContract. Contracts pin
code revision, SHA-256 input files, measurement adapter, rules and purpose. The proposal's
input_revision equals contract.fingerprint(). Use the typed Python models or schema to
build JSON; [regression fixtures](../../tests/research/test_agent_discovery.py) show a full
synthetic example. `snapshot --file PATH` emits input hashes and the current Git revision.

```bash
epi --root /path/to/research --project PROJECT --db /path/to/workspace.sqlite3 discover --file batch.json
epi --root /path/to/research --project PROJECT --db /path/to/workspace.sqlite3 run
epi --root /path/to/research --project PROJECT --db /path/to/workspace.sqlite3 iterate
```

Fresh discovery is mandatory after each review. A justified empty discovery can end an
epoch; repeating empty discovery in the same epoch is rejected. Duplicate measurement
fingerprints, stale versions, unsupported references or invalid source lines reject the
entire batch. No partially accepted claims or proposals are left behind.

## Cumulative understanding at ten-check boundaries

After every ten settled checks and before planning another batch, the semantic research
operator updates a versioned model of the whole subject, acceptance goal and open questions.
Follow [the synthesis protocol](SYNTHESIS_PROTOCOL.md): reconcile all cumulative claims,
cover every inventoried ID explicitly, preserve evidence scope and contradictions, and
record how the unified understanding changes the next experiments. A round recap is not
sufficient. Each synthesis also includes a bounded external literature/news search, an
auditable source ledger, counterevidence and explicit local/external reconciliation. Distinguish
what was read from search leads, and external study findings from this project's measurements.
Reports and coverage indexes are derived artifacts; they do not replace canonical
claims or automatically change scientific status. This is an operator protocol, not an
additional backend action or permission to extend a run budget.

## Measurement and extension boundary

Built-in adapters independently reread hashed inputs: `missingness` profiles observed flags
by a preregistered date boundary; `series` computes summary return metrics from one ordered,
unique daily return series (252-trading-day CAGR only at >=252 observations); `integrity`
checks input availability. Rule thresholds are preregistered, not selected after results.
The loopback workspace API also registers `sse_lifecycle_archive_v1` for QUANT_ETF source
checks. It binds each response body to a strict capture manifest, re-fetches only allowlisted
`www.sse.com.cn` fund archive, listing announcement and index sample-adjustment notice URLs
over HTTPS (including the official optional fund archive `/new/` path) without following
redirects, and independently checks current status, media type, HTTP Date and byte SHA-256.
Its quality is MEASUREMENT for the narrow live-retrieval property; its scope remains
`integrity`. It also records valid
`Last-Modified` values, compares their calendar dates with date segments in PDF archive URLs,
and records ETag presence. These are current response-header observations only: an archive URL
date is not a first-publication timestamp, and neither header establishes PIT availability.
It cannot establish an announcement's first-publication time, archive completeness, index or
ETF universe membership, or point-in-time qualification. Network errors and malformed capture
contracts fail closed; HTTP status or byte changes are measured as negative observations.

The QUANT_ETF host also registers `wayback_sse_index_notice_v3`. It accepts a bounded,
hashed target manifest of exact SSE notice URLs on only `www.sse.com.cn` or `sse.com.cn`,
using `http` or `https`, with UTC cutoffs and expected notice dates. The host/scheme aliases
are queried as separate exact URLs; a capture for one alias does not imply coverage of another.
It queries only the Internet Archive CDX exact-URL endpoint, fetches at most two latest
pre-cutoff raw replay candidates per URL, and requires the replay SHA-1/length to match CDX
plus the page/effective dates to match the pinned target.
This can establish a third-party archive witness that matching content was captured no later
than the cutoff; missing results do not prove contemporaneous non-availability or archive
completeness. It does not establish source first-publication time, index membership
completeness, ETF PIT qualification or prospective strategy performance. Requests are
anonymous HTTPS GETs without cookies, do not follow redirects, reject non-allowlisted source
URLs, and cap each request at four seconds. Valid empty JSON arrays are recorded as zero CDX
rows with response hashes; network failures and malformed responses remain inconclusive
observations. The adapter remains `MEASUREMENT` quality and `integrity` scope.

The QUANT_ETF host also registers `wayback_sse_pdf_attachment_v1` for exact official SSE
index-notice PDF attachment URLs paired with their notice page, displayed date, effective date
and UTC cutoff. It queries exact-URL CDX rows for `application/pdf` and replays at most the two
latest eligible captures; a witness requires the capture timestamp to be no later than the
cutoff, byte length and SHA-1 to match CDX, and a PDF signature/EOF marker. The URL pair must
use an allowlisted SSE notice path and attachment path with the same notice ID. This verifies
archived bytes for the pinned attachment URL only; it does not parse PDF text, verify a link
inside a historical page capture, establish first-publication time or archive completeness, or
qualify historical ETF membership, PIT eligibility or strategy performance. Empty indexes and
digest, length or format mismatches are not evidence that the source was unavailable. Requests
are anonymous HTTPS GETs without cookies or redirects, capped at four seconds, and this is a
`MEASUREMENT`-quality `integrity` check.

The QUANT_ETF host also registers `wayback_sse_pcf_archive_v1` for exact official SSE PCF
download URLs of the form `downloadETF2Bulletin.do?fundCode=NNNNNN`. Each pinned target includes
the fund code, PCF trading day, and 09:15 Asia/Shanghai cutoff. The adapter queries exact-URL CDX
rows and replays at most the two latest pre-cutoff candidates; a witness requires exact URL,
HTTP success, CDX SHA-1 and byte-length agreement, and a matching fund code/trading day parsed
from either SSE XML or text PCF content. It proves only that the exact file response was archived
by the pinned cutoff; it does not prove first publication time, complete historical universe
coverage, or strategy performance. Empty or unavailable archive results remain inconclusive.
Requests are anonymous HTTPS GETs without cookies or redirects and are byte/time bounded; this
is a `MEASUREMENT`-quality `integrity` check.

The QUANT_ETF host also registers `szse_pcf_wayback_archive_v1`. Its hashed target manifest
must contain exactly the four pinned SZSE PCF URLs for 159915 (2019-01-11, 2020-11-30) and
159949 (2023-01-03, 2026-10-16), each with a fixed 09:15 Asia/Shanghai cutoff. It queries only
the Internet Archive CDX exact-URL endpoint, replays at most the two latest candidates per
target, and counts a witness only when its capture time is strictly before the cutoff, raw
replay SHA-1 and byte length match CDX, and strict GBK decoding contains the target ETF code
and date. Empty indexes, unavailable replays and mismatches do not prove historical absence or
source first-publication time. Manifest v1 checks up to two latest candidates; v2 pins one
candidate per target to bound the R254 measurement and independent re-fetch cost. Requests are
anonymous HTTPS GETs without cookies or redirects;
this adapter is `MEASUREMENT` quality and `integrity` scope, not PIT qualification or strategy
performance evidence.

The QUANT_ETF host also registers `arquivo_szse_pcf_v1` for the same four fixed SZSE PCF
targets and cutoffs. It queries Arquivo.pt's exact-URL CDX endpoint with a bounded result limit,
then replays at most the newest strict pre-cutoff candidate through the archive's raw `noFrame`
endpoint. A witness requires an exact target URL, a valid CDX capture timestamp before 09:15
Asia/Shanghai, replay SHA-1 matching the CDX payload digest, and strict GBK text containing the
target ETF code and date. Arquivo.pt's CDX `length` is the compressed WARC/ARC record length;
the adapter records it separately and does not compare it with the raw replay payload length.
Empty CDX bodies are valid zero-row observations; timeouts, malformed rows, failed replay, and
digest or identity mismatches do not establish historical absence. This independent archive
witness can establish only that matching bytes were captured by Arquivo.pt no later than the
capture timestamp. It does not establish first publication, complete archive coverage, historical
ETF membership, full PIT qualification or prospective strategy performance. The adapter is
`MEASUREMENT` quality with `integrity` scope, uses anonymous HTTPS GETs without cookies, rejects
redirects, and caps each request at eight seconds.

The QUANT_ETF host also registers `huaan_pcf_archive_v1` for exactly two issuer-archive
targets: Huaan ETF 159949 PCF files dated 2023-01-03 and 2026-10-16. It queries the issuer's
date-specific historical page and follows only the exact matching `pcf_159949_YYYYMMDD.xml`
download link from the same allowlisted host. The adapter validates the XML fund code, trading
date, record counts and component rows, and records content digests. Requests are anonymous
HTTPS GETs without cookies or redirects. This measures current retrieval of historical issuer
files; the page has no source-time-qualified publication timestamp, so the result cannot
establish pre-open availability, first publication, historical universe completeness, PIT
qualification or prospective strategy performance. It is a `MEASUREMENT` integrity check.

The QUANT_ETF host also registers `commoncrawl_sse_index_notice_v2`. A preregistered target
binds an exact official SSE notice URL, a named Common Crawl collection and its published
window, a UTC decision cutoff, and the page/effective dates already observed in the notice.
The adapter queries only that collection's exact-URL CDX index, limits candidate WARC replays,
requests the indexed byte range, and verifies the WARC target URI, capture timestamp, HTTP
200 status, CDX/WARC/raw-payload SHA-1 and notice dates. It rejects non-allowlisted URLs,
redirects, malformed ranges, oversized records and invalid capture timestamps. Empty index
responses and the exact HTTP 404 JSON `No Captures found for: <target URL>` are recorded as zero
captures; other non-200 responses, missing pre-cutoff rows, unavailable ranges or digest/date
mismatches do not prove that the source was unavailable or that the crawl is complete. Requests
are anonymous HTTPS GETs without cookies, do not follow redirects, and use an eight-second
timeout. This is an archive integrity witness only; it cannot establish first publication, full
historical universe membership, PIT qualification or prospective strategy performance. The
adapter is registered by the local workspace API and uses `MEASUREMENT` quality with
`integrity` scope.

The QUANT_ETF host also registers `commoncrawl_sse_pcf_v1` for exact official SSE code-addressed
PCF download URLs. A pinned target binds the fund code, trading day, Common Crawl collection and
published window, and 09:15 Asia/Shanghai cutoff. The adapter queries only the collection's exact
URL index, requests at most two eligible pre-cutoff WARC byte ranges, and verifies the complete
range, exact WARC target URI, collection timestamp, CDX/WARC/payload digest and internal PCF
fund-code/trading-day identity. It rejects other hosts, malformed ranges, oversized records,
redirects, future/mismatched timestamps and invalid identities. Empty index rows and the exact
HTTP 404 JSON `No Captures found for: <target URL>` are bounded zero-capture observations; they
do not prove archive-wide absence, first-publication time, complete PIT universe membership or
strategy performance. Requests are anonymous HTTPS GETs without cookies, use an eight-second
timeout and do not follow redirects. This is a `MEASUREMENT`-quality `integrity` check.

The QUANT_ETF host also registers `commoncrawl_szse_pcf_v1` for the four pinned SZSE PCF
targets. Full and one-target manifests are bound to fixed URLs, collection windows and
09:15 Asia/Shanghai cutoffs; the response-only probe records index response semantics
without replaying WARC bytes. All v1 requests use an eight-second timeout. The historical
`commoncrawl_szse_pcf_v2` adapter accepts one pinned target and a manifest-pinned 12-second
timeout, but hashes raw no-captures response bytes; R259 showed this can make verification
invalid when JSON whitespace changes. New long-timeout checks use
`commoncrawl_szse_pcf_v3`, which canonicalizes only the exact fixed-URL no-captures message
and still validates any replayed WARC range against CDX length/digest, target URI, capture
timestamp and GBK code/date. None of these versions treats timeouts, empty indexes or
mismatches as evidence of historical absence or source first publication. They remain
read-only `MEASUREMENT` integrity checks and cannot qualify PIT membership or strategy
performance.

Quality is set by trusted measurement code, not by an observation or model. Scientific
questions require MEASUREMENT or above. Built-in offline measurements cannot claim
independent/prospective evidence; their recomputation does not establish market causality,
point-in-time validity, absence of survivor bias or independent forward alpha. No built-in
adapter can promotion-qualify a goal. A failed threshold refutes only if the proposal
preregistered a definitive negative; otherwise it remains inconclusive.

For a new domain, implement `Computer.measure(root, contract) -> Measured` and `quality`,
test its real outputs and failure cases, and inject an explicit reviewed name-to-Computer
mapping into `AgentService(engine, root, computers=...)`. The same adapter recomputes fresh
measurements in verification. Input fingerprints deliberately ignore filenames/order and
compare content digests, preventing alias-only retries; duplicate content files are rejected.
Custom Computers must not derive semantic roles from filenames or input ordering. A domain
that needs train/test or treatment/control roles requires explicit role-bearing contract
fields and validation before that adapter can be registered. For costly/non-deterministic training, use existing
`ExperimentRunner` / `EvidenceVerifier` ports with an independently specified artifact
verifier; these are Python extension seams, not a dynamic arbitrary `--check` command.
A domain host must supply its registered binding before the generic CLI can execute it.
The local workspace API supplies an explicit QUANT_ETF binding when the adopted
repository contains `scripts/run_epipilot_algorithm_round.py` and
`EPIPILOT_ALGORITHM_PYTHON` is configured. Adapter IDs are allowlisted one-to-one to
fixed algorithms; the runner receipt must match the proposal's adapter, canonical review
number, and exact input digest set. Verification rechecks the receipt and recomputes
metrics from the daily artifacts. These measurements remain development evidence and do
not qualify PIT membership or prospective performance.

The QUANT_ETF host also registers `quant_etf_exposure_sensitivity_v1` through an isolated
subprocess. It requires one role-labelled phase panel and one market-event manifest, then
measures 18%, 19%, and 20% linear scales of the supplied lag-1 target path at 0/5/10/20 bp.
The adapter pins the ETF code paths that implement the exposure transform and backtest, but
does not infer the strategy identity of the supplied base targets. A proposal must therefore
bind and explain the base-path provenance; this adapter alone cannot qualify PIT membership,
prospective performance, or the project's goal.

The built-in runner meters wall_seconds. Unknown units are rejected instead of treated as
free. Existing generic ports support typed multi-unit cost vectors and real overrun
settlement. Cost of semantic coding-agent reasoning/API use is NOT metered automatically;
budget it in an integrating host before claiming total research cost/efficiency.

## Progression, recovery and compatibility

Phases depend on weighted resolution of relevant unknowns. Any open critical unknown
prevents optimization phase. Failed infrastructure checks and mere evidence counts do
not advance phase. Current support and AND/OR prerequisites must remain valid. New
counterevidence reopens questions; reusing that evidence cannot close them again.

The phase score combines information, improvement, jointly affordable one-step unlocking
and uncertainty per normalized cost. Priors and phase thresholds are heuristic. A declared
exploration share (default .2) and starvation guard preserve a feasible exploration option;
this may forgo an expensive optimization path and is not an optimal-policy guarantee.

The CLI and workspace API use the same default artifact directory derived from project ID.
If a standalone host explicitly passes --output, retain that flag for all recovery commands;
the workspace API uses its canonical directory and does not adopt custom standalone paths.

`start`, raw `record`, `verify`/`resume` and `cancel` preserve interruption semantics. An
active reservation without an observation requires explicit recovery, never silent retry.
Actual measured costs, including verification, remain recorded even after overruns.
Verification evidence, task outcome and assessment commit atomically; restart cannot mix
a previous passing check with a changed-input failing assessment. Active recovery precedes
goal completion until settlement finishes. Pending knowledge reconciliation after settlement
also returns verification_required after restart. Duplicate resolved file identities and
noncanonical path aliases cannot increase measurement sample counts. New mutually exclusive hypotheses register
as one validated graph addition. Use Purpose.VERIFY for changed-input rechecks of a
reopened refuted hypothesis; this does not unlock dependent optimization before support.

Existing CLI commands remain available. Existing workspace loop/run/resume endpoints
dispatch registered agent measurements when frontier metadata is enabled; returned `agent`
contains the next packet. A discovery-required response returns control to the coding
agent, rather than performing a fixed ETF audit or nested model call.

Security: no .env input paths, shell command interpolation, dynamic executable imports,
second truth store, public history rewrites, remote writes or trading integrations.
Source/error artifacts may be sensitive; generated raw outputs remain outside Git.
