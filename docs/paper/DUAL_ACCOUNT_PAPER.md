# Dual-account paper design

Project Geld now treats the slow and intraday systems as separate experiments.
They do not share positions, credentials, state files, order IDs, or performance
logs.

## Proposed paper allocation

Assuming the two paper accounts receive equal starting capital:

| Aggregate capital | Role |
|---:|---|
| 37.5% | SPY core: 75% of the swing account |
| 12.5% | Daily V4 active sleeve: 25% of the daily account |
| Up to 22.5% | Intra V15: 45% maximum gross exposure in the intraday account |
| At least 27.5% | Combined unallocated cash while Intra V15 is active |

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

The config uses 75% SPY and 25% Daily V4 active momentum, with the existing 21-session
rebalance cadence. It uses delayed SIP and discards the current session's daily
bar so its feed matches the SIP research without contaminating the signal with
Monday's partial bar. The locked config and confirmation variable are enabled.
On a paper-trading day, start before 09:31 ET:

```powershell
.\scripts\run-daily-v4-paper.ps1
```

Use `-DryRun` to plan without submitting.

## Intra V15 account

One-cycle dry plan:

```powershell
geld --config configs/paper-intra-v15.toml intraday-paper-once --output artifacts/paper-intra-v15
```

Dry-plan the latest completed bar:

```powershell
.\scripts\run-intra-v15-paper.ps1 -DryRun
```

The configured intraday candidate combines a confidence-scaled SPY opening-trend
sleeve that acts at 10:30 with the point-in-time-universe V13 short-continuation overlay.
The overlay enters only after its 10:30 signal and 10:45 confirmation filters,
uses at most four 10% positions, and brings maximum gross exposure to 45%.
Every sleeve targets zero at 15:45 New York time.

The paper planner uses day limit orders no more than two basis points through
the latest completed-bar reference price. An unfilled order is preferable to
paying a cost that the research indicates would erase the candidate signal.

The runner invokes each completed 15-minute cycle and a persistent bar guard
prevents a submitted bar from being processed twice. Its locked config and
confirmation variable are enabled. See `PAPER_INTRADAY_V15.md`.

## Windows scheduling

Install the two weekday 09:25 ET execution tasks and the 16:25 ET read-only
Daily V4 close task with:

```powershell
.\scripts\install-paper-tasks.ps1
```

The registered tasks are `ProjectGeld-DailyV4-Paper` and
`ProjectGeld-IntraV15-Paper`. They use the repository as their working directory,
ignore duplicate task instances, start when available, may wake the computer,
and retry a failed runner three times. Each runner also owns a named process
lock, writes a heartbeat JSON file, and appends a dated log under its artifact
directory. The computer must be powered on and the Windows user logged in.

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
