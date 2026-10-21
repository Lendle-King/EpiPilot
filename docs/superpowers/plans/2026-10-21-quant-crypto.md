# quant_crypto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Python project that qualifies public Binance BTCUSDT/ETHUSDT daily spot data, runs cost-aware baseline and momentum/trend backtests, and independently verifies walk-forward and final-holdout results.

**Architecture:** Keep data retrieval, qualification, deterministic portfolio simulation, metric calculation, walk-forward selection, and independent verification in separate modules. The CLI writes immutable, content-addressed run artifacts; CARTS integration is covered by a separate plan so the strategy package remains usable without EpiPilot.

**Tech Stack:** Python 3.11+, standard-library `urllib`, `csv`, `json`, `decimal`, `dataclasses`, and `argparse`; PyYAML for the specified YAML configuration; pytest, Ruff, and mypy for development checks.

**Spec:** `docs/superpowers/specs/2026-10-20-quant-crypto-design.md`

## Global Constraints

- Research BTC and ETH spot only; allow quote-currency cash; prohibit borrowing, shorting, leverage, derivatives, staking yield, and account credentials.
- Use UTC daily candles; a signal from completed candle `t` first executes at candle `t+1` open.
- Record Binance Spot BTCUSDT/ETHUSDT venue, symbols, USDT quote, source, retrieval time, cutoff, raw SHA-256, parser revision, code revision, fee assumption, and slippage assumption.
- Do not forward-fill missing bars; unexplained gaps or invalid rows block strategy evaluation.
- Use decimal arithmetic for prices, quantities, fees, and cash; convert only final metric calculations to finite floats.
- Use the regular-user, non-BNB-discount Binance Spot taker fee of 10 bps per side as the initial declared assumption; the fee source is `https://www.binance.com/en/fee/trading`, checked 2026-10-21. Use fixed 10 bps per-side slippage and rerun both fee and slippage at 2x and 4x. Freeze these values and their sources in every run.
- Use Binance's official market-data-only REST documentation for `https://data-api.binance.vision/api/v3/klines`; no credentials are required for this read-only source.
- Calculate CAGR with `equity_end ** (365.2425 / elapsed_days) - 1`; calculate drawdown over the full daily equity path, including walk-forward fold transitions.
- Reserve the final 24 calendar months as holdout; walk-forward starts after 36 calendar months and uses contiguous full 12-month test folds; use only each training window's latest 12 months to select candidates.
- Select highest validation net CAGR subject to validation MDD <= 20%; break ties by lower MDD then turnover; select quote-currency cash only when no candidate passes the drawdown bound.
- The historical research acceptance target is CAGR >= 30%, MDD <= 20%, and at least 730 daily observations in both stitched walk-forward and untouched holdout; success is never represented as a forecast.
- Keep local raw data and generated run artifacts out of Git; tests use synthetic fixtures only.

## Review Focus

- A duplicate, missing, malformed, or still-open daily candle must block qualification; Task 2 tests each condition.
- A close-derived signal must not fill on the same bar, and costs must be charged once on executed notional; Task 3 tests timing, cash limits, and both buy/sell costs.
- A changed raw file or a manifest that swaps train/test/holdout roles must fail verification; Tasks 1 and 5 test digest binding and explicit roles.
- A short history, partial fold, or missing fold must not be silently scored as a complete acceptance window; Task 4 tests insufficient and incomplete schedules.
- A non-finite metric or ledger mismatch must fail closed even when the artifact reports target-passing metrics; Task 5 tests tampering and independent recomputation.

---

### Task 1: Create the standalone repository and validated configuration

**Files:**
- Create: `/Users/lendleblues/Documents/quant_crypto/pyproject.toml`
- Create: `/Users/lendleblues/Documents/quant_crypto/.gitignore`
- Create: `/Users/lendleblues/Documents/quant_crypto/configs/research.yaml`
- Create: `/Users/lendleblues/Documents/quant_crypto/src/quant_crypto/__init__.py`
- Create: `/Users/lendleblues/Documents/quant_crypto/src/quant_crypto/models.py`
- Create: `/Users/lendleblues/Documents/quant_crypto/src/quant_crypto/config.py`
- Test: `/Users/lendleblues/Documents/quant_crypto/tests/test_config.py`

**Interfaces:**
- Produces `DailyBar(symbol: str, open_time: datetime, open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: Decimal)`, `MarketDay(open_time: datetime, bars: tuple[DailyBar, ...])`, `WeightVector(btc: Decimal, eth: Decimal)`, `CostSchedule(taker_fee_bps: Decimal, slippage_bps: Decimal, fee_source: str, fee_checked_at: date)`, and frozen `ResearchConfig(exchange: str, symbols: tuple[str, str], quote_asset: str, interval: str, costs: CostSchedule, momentum_lookbacks: tuple[int, ...], trend_windows: tuple[int, ...], cost_multipliers: tuple[int, ...], max_validation_mdd: Decimal, target_cagr: Decimal, target_mdd: Decimal, holdout_months: int, initial_training_months: int, test_months: int)` dataclasses.
- Produces `load_config(path: Path) -> ResearchConfig`; reject symbols outside BTCUSDT/ETHUSDT, non-USDT quote, non-UTC timestamps, nonpositive costs, fees without source metadata, unsupported horizons, and any setting that permits leverage or short positions.

- [ ] **Step 1: Write failing configuration tests** named `test_config_loads_spot_universe_and_cost_provenance`, `test_config_rejects_non_spot_universe`, `test_config_requires_fee_source_and_positive_costs`, and `test_weight_vector_rejects_short_or_leveraged_exposure`.
- [ ] **Step 2: Run `pytest tests/test_config.py -q` and confirm the new imports/types are missing.**
- [ ] **Step 3: Create the package scaffold and validated frozen dataclasses; configure the initial 10 bps taker fee, 10 bps slippage, and 2x/4x cost stresses in `configs/research.yaml`.** Add `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `data/`, and `runs/` to `.gitignore`; initialize a Git repository in the new project directory.
- [ ] **Step 4: Run `pytest tests/test_config.py -q`, `ruff check src tests`, and `mypy src`; all must pass.**
- [ ] **Step 5: Commit the scaffold as `feat: initialize standalone quant crypto research project`.**

### Task 2: Fetch, snapshot, normalize, and qualify daily spot data

**Files:**
- Create: `src/quant_crypto/data/__init__.py`
- Create: `src/quant_crypto/data/binance.py`
- Create: `src/quant_crypto/data/snapshot.py`
- Create: `src/quant_crypto/data/qualify.py`
- Test: `tests/data/test_binance.py`
- Test: `tests/data/test_qualify.py`
- Test: `tests/data/fixtures/binance_klines.json`

**Interfaces:**
- Consumes `DailyBar`, `MarketDay`, and `ResearchConfig` from Task 1.
- Produces `SnapshotMetadata(venue: str, symbol: str, interval: str, source_url: str, retrieved_at: datetime, requested_start: datetime, requested_end: datetime, parser_revision: str)`, `DataSnapshot(metadata: SnapshotMetadata, raw_path: Path, normalized_path: Path, raw_sha256: str, normalized_sha256: str)`, and `QualifiedDataset(days: tuple[MarketDay, ...], start: datetime, end: datetime, symbols: tuple[str, str], source_hashes: tuple[str, ...])`.
- Produces `BinanceSpotClient.fetch_daily_klines(symbol: str, start: datetime, end: datetime) -> bytes`, `write_snapshot(raw: bytes, metadata: SnapshotMetadata, output_dir: Path) -> DataSnapshot`, `parse_klines(raw: bytes, symbol: str, retrieved_at: datetime) -> tuple[DailyBar, ...]`, and `qualify_bars(btc: tuple[DailyBar, ...], eth: tuple[DailyBar, ...], cutoff: datetime) -> QualifiedDataset`.
- Data source is the official public market-data host `https://data-api.binance.vision`, endpoint `/api/v3/klines`, interval `1d`; use no API key, follow no redirects to a different host, and require explicit start/end UTC dates in the CLI.
- `DataSnapshot` records venue, pair, interval, source URL, retrieval time, requested range, raw byte SHA-256, normalized-file SHA-256, parser revision, and source response metadata. Each fetch writes a new snapshot and refuses to overwrite an existing content-addressed artifact.

- [ ] **Step 1: Write failing tests** named `test_fetch_uses_public_klines_without_credentials`, `test_snapshot_is_hash_pinned_and_never_overwritten`, `test_parser_excludes_unclosed_candle`, `test_qualification_rejects_duplicate_or_out_of_order_bars`, `test_qualification_rejects_missing_common_day`, `test_qualification_rejects_invalid_ohlc_or_volume`, and `test_qualification_requires_common_btc_eth_coverage`.
- [ ] **Step 2: Run `pytest tests/data -q` and confirm failures expose the missing adapter and qualification behavior.**
- [ ] **Step 3: Implement the urllib-based public adapter, immutable raw/normalized snapshots, and fail-closed UTC/common-coverage qualification.** Keep network code outside domain models; use bounded pagination at the venue's documented daily-kline limit.
- [ ] **Step 4: Run `pytest tests/data -q`, `ruff check src tests`, and `mypy src`; all must pass without a live network request.**
- [ ] **Step 5: Commit as `feat: add immutable Binance spot data snapshots`.**

### Task 3: Simulate spot execution, portfolio accounting, and baseline strategies

**Files:**
- Create: `src/quant_crypto/strategies/__init__.py`
- Create: `src/quant_crypto/strategies/base.py`
- Create: `src/quant_crypto/strategies/baselines.py`
- Create: `src/quant_crypto/strategies/momentum.py`
- Create: `src/quant_crypto/backtest/__init__.py`
- Create: `src/quant_crypto/backtest/engine.py`
- Create: `src/quant_crypto/backtest/ledger.py`
- Test: `tests/backtest/test_execution.py`
- Test: `tests/strategies/test_strategies.py`

**Interfaces:**
- Consumes `QualifiedDataset` and `CostSchedule` from Task 2.
- Produces `Strategy.initial_weights -> WeightVector` and `Strategy.target_weights(closed_history: tuple[MarketDay, ...]) -> WeightVector | None`; a `None` result keeps the current target until a signal exists. `Fill(timestamp, signal_at, symbol, side, quantity, price, notional, fee, slippage_cost)`, `LedgerRow(timestamp, cash, btc_quantity, eth_quantity, equity, daily_return, cumulative_fees, cumulative_slippage, turnover, signal_at)`, and `BacktestResult(strategy_id, fills, ledger, source_hashes, cost_schedule, code_revision)` are immutable result types.
- Produces `BacktestEngine.run(dataset: QualifiedDataset, strategy: Strategy, costs: CostSchedule, initial_equity: Decimal = Decimal("1"), evaluation_start: datetime | None = None, evaluation_end: datetime | None = None) -> BacktestResult`.
- `BacktestResult` contains immutable fills, daily ledger rows, run identity, strategy identifier/configuration, and explicit data/cost/code manifest references. The ledger records cash, BTC/ETH units, close equity, daily return, fees, slippage cost, turnover, signal timestamp, and execution timestamp.
- Baselines are BTC buy-and-hold, ETH buy-and-hold, and 50/50 buy-and-hold fixed at inception without rebalancing. Initial strategy family is positive momentum with lookbacks `[30, 90, 180, 365]`; optional absolute trend filter windows are `[0, 100, 200]`, where `0` disables the filter. Positive assets share capital equally; if none qualify, hold quote cash.

- [ ] **Step 1: Write failing tests** named `test_close_signal_executes_at_next_daily_open`, `test_initial_50_50_baseline_does_not_rebalance`, `test_fee_is_charged_once_per_fill_notional`, `test_slippage_is_adverse_for_buys_and_sells`, `test_buy_orders_cannot_spend_more_than_available_cash`, `test_strategy_weights_remain_cash_only_or_long_spot`, `test_momentum_uses_only_completed_history`, and `test_no_positive_momentum_holds_quote_cash`.
- [ ] **Step 2: Run `pytest tests/backtest tests/strategies -q` and confirm the expected timing/accounting assertions fail.**
- [ ] **Step 3: Implement a deterministic Decimal ledger.** At each daily open, apply only the prior close's signal, execute sells before buys, cap buys at cash after fee/slippage, and mark holdings at raw close. Use an initial allocation at the first evaluation open; do not carry hidden yield on cash.
- [ ] **Step 4: Run `pytest tests/backtest tests/strategies -q`, `ruff check src tests`, and `mypy src`; all must pass.**
- [ ] **Step 5: Commit as `feat: add cost-aware daily spot backtester`.**

### Task 4: Compute calendar metrics, cost stresses, and nested walk-forward selection

**Files:**
- Create: `src/quant_crypto/metrics.py`
- Create: `src/quant_crypto/walk_forward.py`
- Create: `src/quant_crypto/selection.py`
- Test: `tests/test_metrics.py`
- Test: `tests/test_walk_forward.py`
- Test: `tests/test_selection.py`

**Interfaces:**
- Consumes `BacktestEngine`, strategy grid, and qualified daily dataset from Tasks 1–3.
- Produces `PerformanceMetrics(gross_return, net_return, cagr, max_drawdown, annualized_volatility, sharpe, sortino, calmar, turnover, fees, slippage, cash_exposure, samples, start, end)`, `StrategySpec(family, momentum_lookback_days, trend_window_days)`, and `WalkForwardReport(folds, stitched_result, holdout_result, candidate_trials, cost_stresses, complete, blockers)`.
- Produces `compute_metrics(result: BacktestResult) -> PerformanceMetrics`, `select_candidate(results: tuple[BacktestResult, ...], max_validation_mdd: Decimal = Decimal("0.20")) -> StrategySpec`, `run_walk_forward(dataset: QualifiedDataset, config: ResearchConfig) -> WalkForwardReport`, and `run_cost_stresses(dataset: QualifiedDataset, config: ResearchConfig) -> tuple[WalkForwardReport, ...]`.
- Metrics include net/gross return, calendar CAGR, maximum drawdown on full equity, annualized daily volatility, Sharpe and Sortino with zero cash yield, Calmar, turnover, fees, slippage, cash exposure, folds, and sample counts.
- The final 24 calendar months are holdout. In earlier history, use a 36-calendar-month first training period and full 12-calendar-month test windows. For each fold, candidate evaluation uses only its training window's latest 12 months, with earlier history available only as signal warm-up; each chosen configuration is frozen before its test. Carry portfolio cash/holdings and equity continuously across test folds, including rebalance costs when the selected strategy changes. Unused pre-holdout tail shorter than one complete fold is named explicitly; any gap inside a scheduled fold blocks the aggregate report.
- Choose the final holdout strategy using only the latest 12 months before holdout; freeze it across all 24 holdout months. The holdout starts a separately initialized portfolio and never contributes to candidate selection.

- [ ] **Step 1: Write failing tests** named `test_cagr_uses_elapsed_calendar_days`, `test_drawdown_includes_initial_mark_and_fold_transition`, `test_selection_uses_only_recent_training_validation`, `test_selection_tie_breaks_by_drawdown_then_turnover`, `test_cash_is_selected_when_no_candidate_meets_drawdown`, `test_walk_forward_reserves_final_24_calendar_months`, `test_walk_forward_rejects_missing_or_partial_scheduled_fold`, `test_walk_forward_carries_equity_across_fold_boundaries`, `test_holdout_is_selected_before_and_scored_after_boundary`, and `test_insufficient_history_blocks_goal_metrics`.
- [ ] **Step 2: Run `pytest tests/test_metrics.py tests/test_selection.py tests/test_walk_forward.py -q` and confirm failures.**
- [ ] **Step 3: Implement finite strategy-grid selection, elapsed-time metrics, continuous stitched test-fold simulation, separate holdout simulation, and 2x/4x cost-stress reports.** Store every candidate and its validation result in the report to expose selection breadth.
- [ ] **Step 4: Run the focused tests and `ruff check src tests`; all must pass.**
- [ ] **Step 5: Commit as `feat: add calendar metrics and nested walk-forward research`.**

### Task 5: Add independent artifact verification and a reproducible CLI

**Files:**
- Create: `src/quant_crypto/verify.py`
- Create: `src/quant_crypto/cli.py`
- Create: `src/quant_crypto/__main__.py`
- Create: `src/quant_crypto/report.py`
- Test: `tests/test_verify.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes immutable snapshots, run manifest, strategy/cost settings, fills, and ledger from Tasks 1–4.
- Produces `RunManifest(run_id, code_revision, parser_revision, venue, symbols, quote_asset, cutoff, source_hashes, cost_schedule, strategy_grid, roles)`, with each role represented by `DateRole(name, start, end, input_hashes, purpose)`; roles include `development`, `validation`, each `walk_forward_test` fold, and `final_holdout`.
- Produces `VerificationReport(run_id, valid, failures, recomputed_metrics, samples, manifest_sha256)` and `verify_run(project_root: Path, run_id: str) -> VerificationReport`; the verifier has a separate accounting path and must not call `BacktestEngine.run` or trust report metrics.
- Every manifest assigns explicit `development`, `validation`, `walk_forward_test`, and `final_holdout` date roles, names each input path and SHA-256, and records venue/pair/quote, costs, strategy grid, cutoff, code revision, parser revision, and run ID. Roles are read from manifest fields and are never inferred from filenames/order.
- The CLI uses `argparse` commands `data fetch`, `data import`, `data qualify`, `run baseline`, `run strategy`, `run walk-forward`, `verify`, and `report`; each command requires explicit config and range/input arguments. JSON artifacts are written to a content-addressed new run directory and existing run directories are never overwritten.

- [ ] **Step 1: Write failing tests** named `test_verifier_recomputes_ledger_without_backtest_engine`, `test_verifier_rejects_changed_raw_digest`, `test_verifier_rejects_role_swap_or_overlap`, `test_verifier_rejects_mismatched_holdings_or_fills`, `test_verifier_rejects_nan_or_infinite_metrics`, `test_cli_requires_explicit_date_range_for_fetch`, and `test_cli_refuses_to_overwrite_existing_run`.
- [ ] **Step 2: Run `pytest tests/test_verify.py tests/test_cli.py -q` and confirm failures.**
- [ ] **Step 3: Implement the independent reconciliation of signals, holdings, cash, costs, equity, metrics, window roles, and lineage; then add CLI serialization and human-readable report output.**
- [ ] **Step 4: Run the focused tests, `ruff format --check src tests`, `ruff check src tests`, and `mypy src`; all must pass.**
- [ ] **Step 5: Commit as `feat: add independent verification and research CLI`.**

### Task 6: Document the research protocol and verify a synthetic end-to-end run

**Files:**
- Create: `/Users/lendleblues/Documents/quant_crypto/README.md`
- Create: `/Users/lendleblues/Documents/quant_crypto/docs/research_protocol.md`
- Create: `/Users/lendleblues/Documents/quant_crypto/tests/fixtures/synthetic_btc_eth.json`
- Test: `/Users/lendleblues/Documents/quant_crypto/tests/test_end_to_end.py`

**Interfaces:**
- Consumes the CLI and project artifacts from Tasks 1–5.
- Documents setup, fetch/import, qualification, baseline, walk-forward, separate holdout, verify, report, cost assumptions, accepted evidence scope, and the fact that a historical pass is not a future-return guarantee.

- [ ] **Step 1: Write `test_clean_checkout_runs_synthetic_qualification_backtest_and_verification` with assertions that the synthetic fixture produces a verified report and cannot promote the historical target unless both required windows meet every gate.**
- [ ] **Step 2: Run `pytest tests/test_end_to_end.py -q` and confirm the CLI flow is incomplete.**
- [ ] **Step 3: Document only commands and outputs exercised by the end-to-end test; do not include downloaded user data.**
- [ ] **Step 4: Run `pytest`, `ruff format --check src tests`, `ruff check src tests`, and `mypy src`; all must pass from the standalone project root.**
- [ ] **Step 5: Commit as `docs: document crypto research protocol and verification`.**
