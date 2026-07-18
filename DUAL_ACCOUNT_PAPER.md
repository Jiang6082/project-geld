# Dual-account paper design

Project Geld now treats the slow and intraday systems as separate experiments.
They do not share positions, credentials, state files, order IDs, or performance
logs.

## Proposed paper allocation

Assuming the two paper accounts receive equal starting capital:

| Aggregate capital | Role |
|---:|---|
| 20% | SPY core: 40% of the swing account |
| 30% | Daily V4 active sleeve: 60% of the daily account |
| Up to 35% | Intra V1: 70% maximum exposure in the intraday account |
| At least 15% | Intraday-account cash reserve |

The intraday sleeve normally returns to cash before the close. These are paper
research allocations, not evidence that either alpha allocation is optimal.

## Credentials

Create two Alpaca paper accounts and put the following in `.env`:

```dotenv
ALPACA_SWING_API_KEY=...
ALPACA_SWING_SECRET_KEY=...
ALPACA_INTRADAY_API_KEY=...
ALPACA_INTRADAY_SECRET_KEY=...

PROJECT_GELD_SWING_CONFIRM_PAPER=NO
PROJECT_GELD_INTRADAY_CONFIRM_PAPER=NO
```

Leave both confirmation values at `NO` while backtesting and dry planning.

## Daily V4 account

Dry plan:

```powershell
geld --config configs/paper-daily-v4.toml paper-once --output artifacts/paper-daily-v4
```

The config uses 40% SPY and 60% Daily V4 active momentum, with the existing 21-session
rebalance cadence. Before submission, set `[paper] enabled = true` in the config
and `PROJECT_GELD_SWING_CONFIRM_PAPER=YES` in `.env`.

## Intra V1 account

Backtest completed 15-minute bars:

```powershell
geld --config configs/paper-intra-v1.toml intraday-backtest --source alpaca --start 2026-04-01 --end 2026-07-15 --output artifacts/intra-v1-backtest
```

Dry-plan the latest completed bar:

```powershell
geld --config configs/paper-intra-v1.toml intraday-paper-once --output artifacts/paper-intra-v1
```

The configured intraday research candidate ranks liquid short-horizon laggards
relative to SPY, admits positions only while SPY is above its session VWAP, and observes
the market every 15 minutes. It reselects no more than hourly, retains existing
names through a rank buffer, ignores trades smaller than 0.5% of account equity,
uses at most 70% gross exposure, and targets zero
exposure after 15:45 New York time. The engine also forces backtest positions
flat on the final bar of every session.

The paper planner uses day limit orders no more than two basis points through
the latest completed-bar reference price. An unfilled order is preferable to
paying a cost that the research indicates would erase the candidate signal.

Run the command once after each completed 15-minute bar. A persistent bar guard
prevents a submitted bar from being processed twice. Before paper submission,
set `[paper] enabled = true` in the config and
`PROJECT_GELD_INTRADAY_CONFIRM_PAPER=YES` in `.env`.

## Promotion rule

Do not compare raw trade count. Compare each account with its appropriate
benchmark after costs:

- Swing: SPY total return, drawdown, Sharpe, beta, and annual alpha.
- Intraday: cash/T-bill opportunity cost, daily Sharpe, drawdown, turnover,
  win/loss distribution, and estimated implementation shortfall.

The intraday account should remain paper-only until walk-forward results survive
spread and slippage stress and paper fills remain stable for several market
regimes.

See `VERSIONED_RESEARCH.md` for the Daily V5 and Intra V2 challenger results.
