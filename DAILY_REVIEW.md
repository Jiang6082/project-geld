# Daily pre-market review routine

An advisory oversight layer around the two paper accounts. It never places,
cancels, or previews orders and makes no API calls — it reads the accounts'
logged performance and reports where things stand before the session.

## What runs automatically

The scheduled task **`ProjectGeld-MorningReview`** runs every weekday at
**09:00 ET** (25 minutes before the execution tasks) via
`scripts/run-morning-review.ps1`, which calls `scripts/morning_review.py`.
Install/refresh it with the usual:

```powershell
.\scripts\install-paper-tasks.ps1
```

Output is written to `artifacts/morning-review/latest.md` (and a dated copy),
and the runner log to `artifacts/morning-review/runner-YYYYMMDD.log`.

## What the brief contains

For each account (Daily V4 swing, Intra V15 intraday):

- equity, day return, cumulative return, drawdown from peak, down-session streak;
- a **circuit-breaker verdict** — `OK` / `CAUTION` / `HALT`:
  - `HALT`: today's return breached the config `max_daily_loss_pct`, or drawdown
    from peak is at/below **-10%**;
  - `CAUTION`: drawdown at/below **-5%**, or **3+** consecutive down sessions;
  - `OK`: within all limits.
- for Intra V15: the base-sleeve trailing implementation shortfall and whether
  the **2 bps kill-switch** is active.

The verdict is advisory recordkeeping. It does not stop the scheduled strategies;
it is a signal for *your* oversight.

## Layering the skills (in a Claude session)

The offline discipline skills and the market-posture skills complete the routine.
After the brief is generated, open a Claude session and:

1. `exposure-coach` (with `macro-regime-detector` / `market-breadth-analyzer`) for
   market posture — **requires `FMP_API_KEY`** in the environment.
2. `drawdown-circuit-breaker` and `pre-trade-discipline-gate` before any *manual*
   action — both are offline and place no orders. They read/write
   `trader-memory-core` state under `state/theses/`.
3. `weekly-performance-digest` on Fridays for the week's win rate / expectancy.

These skills assume a manual discretionary workflow and are hubbed on
`trader-memory-core`; they are an oversight/journaling layer, not wired into the
automated scheduled strategies.

## Prerequisites

- The paper tasks must have logged `artifacts/<account>/performance.csv` (they do
  on each cycle); otherwise the account shows `NO DATA`.
- `FMP_API_KEY` (free tier at financialmodelingprep.com) is only needed for the
  market-posture skills, not for the brief itself.
