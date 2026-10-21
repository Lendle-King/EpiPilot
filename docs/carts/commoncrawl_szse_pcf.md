# Common Crawl SZSE PCF measurement contract

`commoncrawl_szse_pcf_v1` is a read-only integrity adapter for the four pinned
SZSE ETF PCF URLs. A `szse-pcf-commoncrawl-targets/v1` manifest must contain all
four URL, collection-window and 09:15 China-time cutoff tuples. A bounded
`szse-pcf-commoncrawl-probe/v1` manifest contains exactly one of those same
predefined tuples. A `szse-pcf-commoncrawl-response-probe/v1` manifest also
contains exactly one predefined tuple, but measures only the index response and
never replays WARC bytes. These schemas permit narrow follow-ups within the
registered target set; they do not permit caller-supplied URLs, collection IDs,
or cutoff times.

The adapter sends sequential HTTPS requests only to `index.commoncrawl.org`
and `data.commoncrawl.org`, with an eight-second per-request timeout. For each
target it accepts no more than the latest candidate whose capture timestamp is
strictly before the preregistered cutoff. A witness requires a complete HTTP
byte range matching the index offset and length, a valid response WARC record,
an exact target URI and timestamp match, the payload digest matching the CDX
digest, and strict GBK decoding with the expected ETF code and date. The CARTS
measurement adapter independently repeats the same input-bound measurement
before it can verify the recorded observation. Response-only probes
canonicalize the exact `No Captures found for: <fixed URL>` message so harmless
JSON whitespace changes do not invalidate semantic rechecks; they do not
qualify a PCF capture.

An empty HTTP 200 index, Common Crawl's exact no-captures response, timeout,
incomplete result, or failed byte/identity check does not establish that a
historical file or archive capture never existed. A verified capture only
establishes a bounded archive observation; it does not establish original
exchange publication time, point-in-time universe membership, strategy
performance, or prospective success. The adapter never reads or writes the ETF
forward log and does not interact with the V2.5 frozen configuration.
