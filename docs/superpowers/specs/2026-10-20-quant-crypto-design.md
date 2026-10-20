# quant_crypto design (2026-10-20)

## Purpose

Create an independent Python research project at
`/Users/lendleblues/Documents/quant_crypto` for daily BTC/ETH spot allocation. Its
registered research goal is to find a frozen strategy whose stitched out-of-sample
results in the configured quote currency have CAGR >= 30% and maximum drawdown <= 20%.
This is a falsifiable research target, not a forecast or a promise of future returns.

The user selected historical data, strategy research and strict backtesting as the first
stage. The project does not submit orders, hold exchange credentials, connect to an
account or run a live signal service. The quant_crypto research engine remains standalone;
a thin EpiPilot CARTS host may orchestrate its experiments without making EpiPilot a
runtime dependency of the strategy package.

## Design choices

1. **Independent research repository with EpiPilot CARTS orchestration — recommended.** Own
   package, CLI, configuration, immutable input snapshots and reports; use EpiPilot for
   canonical research questions, experiment scheduling, evidence history and recovery.
   The strategy and measurement package remains reproducible without an EpiPilot checkout.
2. **Add crypto logic to the ETF research adapter.** This would reduce initial packaging
   work but would couple exchange data, continuous-market calendars and annualization to
   ETF-specific assumptions. It does not meet the user's request for an independent
   project.
3. **Build a multi-exchange derivatives platform.** This would cover basis, funding,
   liquidation and venue routing, but expands the first research question substantially.
   Defer it until spot data and baseline evaluation pass their gates.

## EpiPilot CARTS orchestration

Use the installed EpiPilot research skill to advance the research in bounded cycles.
EpiPilot owns canonical goals, requirements, unknowns, hypotheses, experiment contracts,
budgets, evidence, rankings, events and recovery in the existing canonical workspace
database. `quant_crypto` owns exchange snapshots, deterministic research code, backtest
ledgers and domain-specific independent verification. The EpiPilot project receives a
new project ID and its own canonical history; do not copy ETF state, create a second event
store or reset another project's budget.

An explicit `quant_crypto` host entry point connects the two layers through the documented
Python extension seams. It uses `AgentService.iterate` to obtain the next packet,
`apply_discovery` to register a reviewed discovery batch, and the existing
`ResearchEngine.run_next` with a statically registered crypto runner and verifier for an
approved measurement. It reconciles relevant questions after settlement. Data diagnostics
use an explicitly named `Computer` mapping. No dynamic imports, shell-provided adapter
names or arbitrary check commands are allowed. The host must preserve CARTS reservation,
verification, settlement, event replay and resume behavior. The ordinary
strategy/backtest CLI still works without the host.

The built-in CARTS `series` adapter is not suitable for this domain: it assumes a single
chronological return series and annualizes with 252 periods. The crypto verifier must
recompute from revision-pinned OHLCV, costs, trades and manifest using calendar-time CAGR
and a separate ledger calculation. The registered measurement contract must bind a
hash-pinned, role-bearing manifest that names development, validation, walk-forward test
and final holdout date ranges, then verify those ranges and hashes. Roles must never be
inferred from filenames or input order. Source code revision, cost schedule and all data
hashes are part of the experiment basis.

The final experiment may be F4 within the explicitly declared `development` scope: it
tests whether this frozen method met the numeric target on the pinned historical
walk-forward and holdout periods. A reviewed `CryptoEvidenceVerifier` may set promotion
qualification only when its independent recomputation confirms both windows, both
thresholds, minimum sample counts, data lineage and costs. This records a scoped
historical research result; it makes no claim about prospective returns. The normal
built-in `MeasurementAdapter` always leaves promotion qualification false and cannot
complete this target by itself. If this extra verifier seam is unavailable, CARTS may
still track measurements and unknowns, but the external `quant_crypto` acceptance report
remains authoritative and the EpiPilot goal must stay unmet.

Research rounds are agent-driven, not a background daemon. The coding agent calls the
research skill, follows each bounded packet and performs a cumulative synthesis every ten
settled checks. If periodic unattended operation is later desired, schedule a separate
host only after its permissions and resource limits are defined. The initial CARTS budget
must be explicitly set for this project; it is not inherited from the ETF project.
Runner/verifier wall time can be metered by CARTS, while semantic coding-agent/API usage
is not automatically measured and must be reported separately.

## Scope and assumptions

- Research universe: BTC and ETH spot only, long or quote-currency cash, no borrowing,
  shorting, leverage, derivatives, staking yield or cross-sectional altcoin selection.
- Decision frequency: one signal per completed UTC daily candle. A signal made from
  candle `t` may first execute at the next UTC daily candle's open. Execution and return
  accounting must use the same convention.
- Venue assumption for the first implementation: one explicitly configured exchange and
  its BTC/ETH spot pairs, with quote currency recorded in every result. A Binance Spot
  public-data adapter is the proposed first source; venue and pair identifiers are
  configuration, never inferred. Results are venue-specific. USDT-denominated results
  must be labelled USDT and must not be described as USD returns.
- Data access is public and read-only. The source, retrieval time, time range, raw-file
  SHA-256, schema version and parser revision are recorded. Raw snapshots are immutable;
  corrected or refreshed data create a new snapshot and run identity.
- The initial implementation uses Python 3.11+, the standard library and existing
  dependencies where practical. Strategy and portfolio accounting are deterministic,
  typed and side-effect free; network access stays in source adapters.

## Research sequence

Establish baselines before strategy expansion:

1. Buy-and-hold BTC, buy-and-hold ETH and a 50/50 BTC/ETH buy-and-hold portfolio
   set to equal value at inception, with no later rebalancing.
2. A simple single-horizon momentum strategy with a quote-currency cash state.
3. Multi-horizon momentum and an absolute trend filter.
4. Volatility targeting and risk-budget variants, only when earlier measurements
   motivate them.

Every proposal records its hypothesis, frozen inputs, parameters, data window, execution
and cost assumptions, expected metric changes, rejection condition and code revision.
No model-based or reinforcement-learning methods enter this first ladder. The full
historical CAGR is never used to select a final strategy.

## Data and calculation contract

The project has four small boundaries:

- **Source adapter:** fetches or imports exchange OHLCV and writes raw, timestamped,
  hashed snapshots. It has no strategy logic or account access.
- **Qualification:** checks symbol identity, UTC interval boundaries, ordering and
  uniqueness, OHLC consistency, positive prices, volume validity, duplicate bars,
  missing intervals, common BTC/ETH coverage and cutoff. A missing bar is not silently
  forward-filled or treated as a zero return. Unexplained gaps block strategy evaluation.
- **Research engine:** consumes only qualified, revision-pinned bars and a frozen
  strategy configuration; produces positions, proposed fills, portfolio ledger and
  metrics. A signal from a completed daily close first fills at the next daily open with
  the configured adverse slippage applied to fill price. Each fill charges the configured
  fee once against traded notional. Equity is marked to market at each UTC daily close.
  All uninvested capital stays in the configured quote currency with zero yield; no
  stablecoin yield is imputed.
- **Independent verifier:** rereads the pinned bars, strategy parameters, fills and cost
  schedule; independently recomputes positions, cash, fees, equity, CAGR and drawdown.
  It compares expected and actual holdings at each timestamp and rejects mismatches,
  malformed ledgers, non-finite metrics and missing provenance.

Before performance runs, freeze a source and cost manifest. The base fee must cite the
selected exchange fee schedule and account tier assumption, while slippage must have a
declared measurement or conservative fixed assumption. Also run 2x and 4x all-in cost
stress by scaling both fee and slippage assumptions. Missing or ungrounded base costs
prevent target qualification. Do not fetch current market data or fee schedules
implicitly during replay.

The primary metric contract is:

- Compound portfolio equity using timestamped, net-of-cost returns.
- CAGR uses actual elapsed UTC time, `equity_end ** (365.2425 / elapsed_days) - 1`,
  rather than a 252-trading-day factor.
- Maximum drawdown is the largest peak-to-trough decline on the full stitched equity
  path, including transition boundaries between walk-forward folds.
- Risk and return comparisons use the full daily mark-to-market equity series. Return
  windows include the initial and terminal UTC timestamps; missing valuation dates are
  not silently dropped.
- Report each asset baseline, the fixed portfolio, gross and net returns, CAGR, maximum
  drawdown, volatility, Sharpe/Sortino, Calmar, turnover, fee and slippage totals, cash
  exposure, fold-level results and cost sensitivities.
- Returns use the configured quote currency. Report stablecoin and venue assumptions
  alongside any USDT results.

## Validation and promotion gates

1. **Data gate:** qualify an immutable common BTC/ETH daily history. Freeze pair, quote
   currency, UTC convention, source, cutoff, fee schedule and slippage assumption before
   opening strategy outcomes.
2. **Baseline gate:** reproduce the three baseline ledgers and independently recompute
   all metrics. Baselines are controls, not evidence that a new strategy works.
3. **Development gate:** preregister the finite strategy families, parameter grids and
   selection rule. For each test fold, fit candidates on expanding historical data and
   select among them using only the most recent 12 months of that training window; choose
   highest net CAGR subject to validation MDD <= 20%, breaking ties by lower drawdown then
   lower turnover. If no candidate meets the drawdown bound, select quote-currency cash.
   Record every tried configuration to expose selection breadth.
4. **Walk-forward gate:** reserve the final 24 calendar months of common eligible daily
   observations chronologically as the untouched final holdout. In the preceding history,
   start with 36 calendar months and run 12-month test windows stepped forward 12 months.
   Each window's strategy is frozen before its test window; the same preregistered
   selection rule uses only information available before that window. Concatenate test-only
   daily returns in time order. Any overlap or missing fold is explicit and blocks the
   aggregate acceptance calculation. If the qualified range cannot support this
   schedule, report insufficient history and do not claim the goal was evaluated.
5. **Final holdout gate:** use the same preregistered selection rule to select one
   configuration using only pre-holdout data, then score it on the reserved final 24
   months. The holdout result is reported separately and is never fed back into strategy
   selection.
6. **Goal gate:** consider the goal met only when both independently verified stitched
   walk-forward net results and the untouched final holdout meet CAGR >= 30% and MDD <=
   20%, each has at least 730 daily observations, and all mandatory integrity and cost
   gates pass. If a gate fails, preserve the result and leave the goal unmet.

No offline backtest, including a successful holdout, establishes future performance or
independent forward evidence. Any later forward test must start from its real capture
time, append observations without backfill, and have its own preregistered duration and
acceptance contract.

## Project layout and interface

Proposed standalone structure:

```text
quant_crypto/
  pyproject.toml
  README.md
  configs/
    research.yaml
  src/quant_crypto/
    models.py
    data/          # source adapter, normalization, qualification, manifests
    strategies/    # baseline and progressively approved strategies
    backtest/      # execution model, ledger and portfolio accounting
    metrics.py     # calendar-time metrics
    verify.py      # independent ledger and metric recomputation
    cli.py
  tests/
  data/            # ignored local raw snapshots and derived artifacts
  reports/         # versioned research summaries only when intentionally saved
```

The CLI should expose explicit commands for data import/fetch, qualification, baseline
run, strategy run, verification and report generation. Every run writes to a new
content-addressed run directory; it never overwrites raw data or an earlier report.
Machine-readable manifests and ledgers carry UTC timestamps and stable run IDs.

## Acceptance

The first usable release is complete when a clean checkout can:

- load or fetch a declared public BTC/ETH spot dataset without credentials;
- reject corrupted, duplicated, out-of-order and unexplained missing intervals;
- run frozen baselines and a simple daily strategy with next-bar execution and costs;
- independently reproduce its ledger and metrics from pinned inputs;
- emit a chronological walk-forward report with a final holdout kept separate;
- advance a separate EpiPilot project through a statically registered crypto runner and
  verifier, preserving canonical evidence, budget settlement and resume behavior;
- keep generated/raw data out of Git by default, with synthetic fixtures in tests.

The 30% CAGR / 20% drawdown goal is a research acceptance target, not an implementation
acceptance shortcut. Code and synthetic tests cannot meet the investment research goal.

## Risks and unresolved configuration

- The user should review the proposed Binance Spot source and USDT quote assumption;
  changing venue or quote currency changes the evidence and requires a new run identity.
- Public historical OHLCV can contain gaps and may not preserve original publication or
  correction times. Historical replay can establish only the provenance actually
  recorded; it must not claim point-in-time source availability that was not captured.
- Daily bars approximate fills. The chosen next-open convention and fixed slippage stress
  do not reproduce order-book queue position or realized live execution.
- The target may be unattainable at the declared drawdown and cost limits. A failed goal
  remains a valid outcome and must not trigger unbounded tuning on the same holdout.
- A single venue and two assets limit generalization. Results do not transfer to other
  exchanges, quote currencies, instruments or future market regimes.
