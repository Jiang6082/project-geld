# Project Geld

Project Geld is a standalone research, backtesting, and Alpaca paper-trading
engine for US equities. It is designed to answer a disciplined question:
does a strategy retain evidence of alpha out of sample, after realistic
execution costs, before it is allowed anywhere near paper execution?

This is research software, not investment advice. It is intentionally
paper-only and has no live-trading mode.

The platform also supports an isolated dual-account paper workflow: a slower
Daily V4 account and a separate 15-minute Intra V1 account with independent
Alpaca credentials, state, risk limits, and performance logs. See
`DUAL_ACCOUNT_PAPER.md` for the proposed allocation and exact commands.
Long-horizon intraday findings are recorded in `LONG_INTRADAY_RESEARCH.md` and
the short-continuation follow-up is in `INTRADAY_V7_V9_RESEARCH.md`.

## What is included

- A common strategy interface shared by research and paper execution.
- Alpaca historical OHLCV ingestion with explicit feed and adjustment settings.
- CSV and deterministic synthetic data sources.
- Exact-request CSV caching for reproducible research runs.
- Versioned daily and intraday strategy implementations, including:
  - cross-sectional momentum;
  - long-only trend strength;
  - regime-filtered short-term mean reversion.
  - buffered point-in-time Momentum V2;
  - market-residual, diversified Momentum V3.
  - `daily_v4`, the core-plus-alpha daily control;
  - `daily_v5`, its benchmark-aware challenger;
  - `intra_v1`, the 15-minute intraday control;
  - `intra_v2`, a lower-turnover selective intraday challenger.
  - `intra_v3`, an eight-name/80%-gross allocation experiment.
  - `intra_v4`, a rejected relative-continuation experiment;
  - `intra_v5`, delayed recovery confirmation;
  - `intra_v6`, a research-only selective confirmed-reversal challenger.
  - `intra_v7`, opt-in confirmed short continuation;
  - `intra_v8`, its prior-daily-trend-aligned challenger;
  - `intra_v9`, a rejected unusual-volume confirmation experiment.
- Next-session-open backtests, avoiding same-close look-ahead fills.
- Configurable slippage, per-share fees, fractional shares, and rebalance cadence.
- Gross-exposure and per-position limits.
- Benchmark return, excess return, alpha, beta, Sharpe, Sortino, drawdown,
  Calmar, annualized turnover, order count, and fee metrics.
- Parameter-grid experiments with separate training and test periods.
- Alpaca paper-order planning and submission with:
  - paper=True hard-coded;
  - submission disabled by default;
  - a second environment confirmation;
  - market-hours check;
  - daily-loss guard;
  - account-wide exposure accounting, including unmanaged positions;
  - open-order reconciliation;
  - deterministic client order IDs;
  - order-notional and position limits.

## Architecture

    Data source -> normalized bars -> strategy targets
                                      |             |
                                      v             v
                              next-open backtest   paper planner
                                      |             |
                                      v             v
                              metrics/experiments  Alpaca paper API

Strategies produce target portfolio weights. They never place orders.
The backtester and paper planner independently translate those same weights
into executions under their respective safety and fill rules.

## Installation

Python 3.11 or newer is required.

    python -m venv .venv
    .venv\Scripts\activate
    pip install -e ".[alpaca,dev]"

On macOS or Linux, activate with:

    source .venv/bin/activate

## First backtest

The default uses deterministic synthetic data and requires no credentials:

    geld --config config.example.toml backtest \
      --source synthetic \
      --start 2020-01-01 \
      --end 2025-12-31 \
      --output artifacts/backtest

Outputs:

- metrics.json
- equity.csv
- trades.csv
- targets.csv

To use Alpaca historical data:

    copy .env.example .env

Add Alpaca paper API credentials to .env, then run:

    geld --config config.example.toml backtest \
      --source alpaca \
      --start 2020-01-01 \
      --end 2025-12-31 \
      --output artifacts/alpaca-backtest

Alpaca-py uses StockHistoricalDataClient and StockBarsRequest for stock bars.
The default configuration selects the IEX feed and all corporate-action
adjustments explicitly.

## Strategy experiments

Run a training/test parameter grid:

    geld --config config.example.toml experiment \
      --source alpaca \
      --strategy momentum \
      --grid lookback=63,126,252 \
      --grid volatility_lookback=20,60 \
      --grid top_n=2,3,5 \
      --grid gross_exposure=0.75,0.9 \
      --train-fraction 0.70 \
      --output artifacts/experiments/momentum.csv

Results are ranked by robust_score, defined as the lower of training and test
Sharpe. This is deliberately conservative, but it is not proof of alpha.
Promising candidates still need rolling walk-forward tests, different market
regimes, broader universes, and paper observation.

List built-in strategies:

    geld list-strategies

To add one, implement the Strategy protocol from
src/project_geld/strategies/base.py and register the class in registry.py.
The only required output is one row per timestamp and symbol containing:

- timestamp
- symbol
- target_weight
- score

Signals may use information available through their timestamp. Project Geld
executes a signal no earlier than the following session open.

## Paper trading

First generate a plan without submitting anything:

    geld --config config.example.toml paper-once

This still connects to the Alpaca paper account so it can reconcile equity,
positions, cash-equivalent exposure, and existing open orders.

To enable submission:

1. Set enabled = true under the paper section in the TOML configuration.
2. Set PROJECT_GELD_CONFIRM_PAPER=YES in .env.
3. Run during regular US market hours:

       geld --config config.example.toml paper-once --submit

The TradingClient is always constructed with paper=True. There is no option
that points this project at Alpaca live trading.

## Safety and research rules

- Never commit .env.
- Keep paper submission disabled while developing strategies.
- Treat IEX and SIP as different datasets; record the selected feed.
- Judge results after slippage and other realistic costs.
- Do not choose parameters from test-period performance.
- Prefer repeated walk-forward evidence over one favorable backtest.
- Compare against a relevant benchmark and inspect turnover and capacity.
- Run a candidate in paper for an extended period before considering any
  separate production system.

## Tests

    pytest

The suite covers data validation, strategy output and causality, next-bar
execution, transaction costs, risk caps, out-of-sample experiments, paper
order idempotency inputs, and the daily-loss guard.

## Current scope

Version 0.1 supports daily and intraday research. Intraday backtests can opt in
to signed short targets; paper execution remains long-only and rejects negative
weights. Momentum V2 can accept point-in-time membership periods for
survivorship stress tests. The engine does not model historical borrow
availability, locate fees, tick/order-book events, factor attribution, a
distributed experiment scheduler, or a persistent paper daemon.

## Current research

The first Alpaca-data strategy study, including concentration, universe,
transaction-cost, and rebalance-cadence stress tests, is documented in
RESEARCH_REPORT.md. The main finding is that the original megacap results are
concentrated in NVDA and META; 12-month sector-ETF momentum is the only
candidate currently worth advancing to rolling walk-forward research.

Momentum V2 is documented in MOMENTUM_V2.md. It adds 12-1 momentum, trend
confirmation, inverse-volatility weights, sector caps, an entry/exit rank
buffer, daily paper performance tracking, and a persistent ten-session paper
rebalance guard. The pilot configuration caps each paper order at USD 100.

## Expanded robustness research

The expanded suite uses Alpaca SIP daily bars from 2016 onward and runs a
common-warmup long-history comparison, rolling three-year/one-year walk-forward
selection, parameter stability, execution-cost stress, market regimes,
matched-exposure SPY/equal-weight baselines, and a point-in-time Dow membership
proxy.

    .venv\Scripts\python.exe scripts\expanded_research.py `
      --start 2016-01-01 `
      --end 2026-07-16 `
      --output artifacts\research-expanded

Read EXPANDED_RESEARCH.md before changing the paper allocation. The expanded
evidence supports only a small paper pilot; it does not establish durable alpha.

## Broad point-in-time universe

The broad-universe pipeline enumerates active and inactive Alpaca US-equity
assets, downloads SIP bars in resumable batches, constructs a monthly
point-in-time top-liquidity universe, and handles missing-price exits with an
explicit stress assumption. The completed study covers 6,329 candidate symbols
and 1,092 stocks that entered the monthly top 500. See
BROAD_UNIVERSE_RESEARCH.md for the results and limitations.

    .venv\Scripts\python.exe scripts\broad_universe_research.py `
      --top-n 500 `
      --minimum-price 5 `
      --minimum-dollar-volume 20000000 `
      --output artifacts\research-broad

Momentum V3 applies market-residual momentum, trend and downside-volatility
scores, correlation-aware selection, 4% position caps, market-regime exposure,
and a forecast-volatility ceiling to that broad universe. Its locked candidate
did not beat V2 or SPY risk-adjusted, so paper submission remains disabled.
See `MOMENTUM_V3_RESEARCH.md` and reproduce the study with:

    .venv\Scripts\python.exe scripts\broad_v3_research.py

Momentum V4 separates market participation from stock selection: a persistent
SPY core is combined with a smaller, diversified residual-momentum sleeve.
Training selected the 75% SPY / 25% active version. Its later diagnostic was
modestly better than SPY after 10-basis-point slippage and remained ahead under
25-basis-point stress, so it is approved for forward shadow observation only.
Paper submission is disabled. See `MOMENTUM_V4_RESEARCH.md`.

    .venv\Scripts\python.exe scripts\broad_v4_research.py
    .venv\Scripts\python.exe scripts\broad_v4_cost_stress.py

The V4 paper configuration loads its 500-stock universe from a dated CSV
snapshot, refuses snapshots older than 45 days, applies separate SPY and active
stock risk limits, and reserves a 1% market-order cash buffer. Refresh the
snapshot after updating the broad-universe research data:

    .venv\Scripts\python.exe scripts\export_current_universe.py

## Momentum V4.1 research

V4.1 adds filing-dated SEC quality and earnings features, component ablation,
benchmark-aware active weighting, and a separate 15%-cash defensive variant.
See `MOMENTUM_V41_PROGRESS.md`. SEC downloads require a declared contact user
agent in `.env`:

    PROJECT_GELD_SEC_USER_AGENT=ProjectGeld your-email@example.com

Then run:

    .venv\Scripts\python.exe scripts\fetch_sec_fundamentals.py
    .venv\Scripts\python.exe scripts\broad_v41_research.py
