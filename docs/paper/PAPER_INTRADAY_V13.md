# Intra V13 paper readiness

## Status as of July 19, 2026

Intra V13 is enabled for the isolated Alpaca paper account by
`configs/paper-intra-v13.toml`. Live trading is impossible through this code:
the Alpaca trading client remains hard-coded to `paper=True`.

Verified against the configured account:

- authentication succeeded;
- equity was USD 1,000,000 and buying power was USD 4,000,000;
- short selling was enabled and the account exceeded Alpaca's USD 2,000 gate;
- there were no managed positions;
- all 100 current point-in-time-universe names were shortable and
  easy-to-borrow at the time of the check;
- `PROJECT_GELD_INTRADAY_CONFIRM_PAPER=YES` was present;
- recent SIP was rejected by the subscription, while current IEX minute bars
  succeeded through July 17, 2026 at 15:59 ET;
- a dry synthetic negative target produced a whole-share short plan and no
  order was submitted;
- a full strategy dry run completed with zero orders because Friday's latest
  completed bar was after the strategy's 15:45 flatten time;
- all 76 automated tests passed.

Asset borrow status is checked again whenever the strategy asks to increase a
short. An unavailable name is skipped. Covers and other risk-reducing exits are
never blocked by borrow status, minimum-trade thresholds, order-notional caps,
or cash budgeting. A requested long/short sign reversal flattens first and can
open the opposite side on a later cycle.

## Automated weekday operation

Windows Task Scheduler launches `ProjectGeld-IntraV13-Paper` at 09:25 ET every
weekday. The task starts when available, may wake the computer, ignores a
duplicate instance, and retries a failed process three times at one-minute
intervals. Install or refresh both paper tasks with:

```powershell
.\scripts\install-paper-tasks.ps1
```

For a manual launch from the repository root, use:

```powershell
.\scripts\run-intra-v13-paper.ps1
```

For a non-submitting rehearsal, use:

```powershell
.\scripts\run-intra-v13-paper.ps1 -DryRun
```

The runner operates every 15 minutes from 09:31 through 15:46 ET. V13 observes
the 10:30 signal bar, confirms on the 10:45 bar, and targets zero from the
15:45 bar onward. It retries a failed cycle after 30 seconds. A named process
lock prevents two copies from running concurrently. Logs are appended to
`artifacts/paper-intra-v13/runner-YYYYMMDD.log`, and current liveness is written
to `artifacts/paper-intra-v13/runner-heartbeat.json`.

The computer must be powered on and the Windows user logged in. Wake-on-run
cannot start a powered-off machine, and the task uses the interactive user's
access to the local `.env` file.

The first 90-day data-cache build took about 119 seconds. The warmed incremental
cycle took about 13 seconds. The cache is local and ignored by Git.

## What paper results mean

The 3.13% IEX result is cumulative across the July 2020-July 2026 backtest. It
is not an expected one-day or one-year paper return. For evaluation, retain 8
bps as the locked baseline, use 16 bps as the conservative decision case, and
treat 24 bps as a severe stress case until observed implementation shortfall is
available.

Paper fills do not prove executable alpha. Alpaca paper trading does not model
market impact, queue position, latency slippage, borrow fees, or forced stock
recalls. Compare the paper account's realized return, drawdown, fills, rejected
orders, and missed signals with the locked IEX backtest; do not retune V13 from
individual paper trades.
