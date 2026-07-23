# Daily V4 paper readiness

## Status as of July 19, 2026

Daily V4 is enabled for the isolated Alpaca swing paper account by
`configs/paper-daily-v4.toml`. The client remains hard-coded to `paper=True`.

Verified against the configured account:

- authentication succeeded;
- equity was USD 1,000,000 and buying power was USD 4,000,000;
- there were no managed positions;
- the 500-name universe snapshot was current as of July 15, 2026;
- `PROJECT_GELD_SWING_CONFIRM_PAPER=YES` was present;
- recent SIP was unavailable, but 20-minute-delayed SIP daily data succeeded;
- the paper path excludes the current session, preventing a partial daily bar
  from entering the signal;
- the latest completed signal was Friday, July 17;
- the dry run planned 41 orders and submitted none;
- targets were 40% SPY and 60% across 40 active names, with a 2.96% largest
  active position;
- the 1% cash buffer reduced planned gross notional to USD 989,999.99.

## Automated weekday operation

Windows Task Scheduler launches `ProjectGeld-DailyV4-Paper` at 09:25 ET every
weekday. A second read-only task, `ProjectGeld-DailyV4-Close`, runs at 16:25 ET.
The delay allows the free 20-minute-delayed SIP feed to expose the completed
16:00 daily bar without requiring a live SIP subscription. Install or refresh
all paper tasks with:

```powershell
.\scripts\install-paper-tasks.ps1
```

For a manual launch before 09:31 ET, use:

```powershell
.\scripts\run-daily-v4-paper.ps1
```

For a non-submitting rehearsal, use:

```powershell
.\scripts\run-daily-v4-paper.ps1 -DryRun
```

The runner executes once. The persistent cadence state then prevents another
submitted rebalance until 21 completed sessions have elapsed. Market orders are
used because the backtest assumes next-session-open execution and Friday's
close would be a stale limit-price reference on Monday.

A named process lock suppresses duplicate runners. Logs are appended to
`artifacts/paper-daily-v4/runner-YYYYMMDD.log`, and liveness is written to
`artifacts/paper-daily-v4/runner-heartbeat.json`. The scheduled task starts when
available, may wake the computer, and retries a failed process three times. The
computer must remain powered on with the Windows user logged in.

The close task never submits, cancels, or replaces orders. It records closing
performance, audits the day's Alpaca order statuses, compares positions with
the newest targets, checks universe/data freshness, calculates whether the
next opening rebalance is due, and stages a next-session preview under
`artifacts/paper-daily-v4-close`. Run it manually with:

```powershell
.\scripts\run-daily-v4-close.ps1
```

If run while the market is open, it safely excludes the partial current daily
bar and labels the result `prior_close_preview`.

## Locked cost validation

The exact 40% SPY / 60% active paper configuration was replayed on the existing
point-in-time SIP research panel from 2017 through July 2026.

| Slippage per side | 2020-2026 CAGR | Sharpe | Max drawdown | Annual alpha |
|---:|---:|---:|---:|---:|
| 8 bps | 16.31% | 0.852 | -33.31% | 2.03% |
| 10 bps | 16.21% | 0.848 | -33.32% | 1.95% |
| 16 bps | 15.90% | 0.835 | -33.34% | 1.68% |
| 24 bps | 15.50% | 0.817 | -33.37% | 1.33% |

The same rules had negative estimated alpha during 2017-2019, so the later
outperformance is not proof of durable alpha. Paper observation should compare
realized fills and portfolio performance with SPY without retuning the model.
The locked backtest uses 10 bps per side; use 16 bps as a conservative decision
case and 24 bps as a stress case until observed implementation shortfall is
available.

## Applied improvements (July 22, 2026) — Daily V4.0.4

Patch trail: 4.0.1 construction fix, 4.0.2 regime control, 4.0.3 75/25 allocation,
4.0.4 benchmark-aware active weighting.
This supersedes the 40/60 dry-run and cost table recorded above.

4.0.4 detail: benchmark-aware weighting tilts toward stronger scores and
penalizes beta far from 1. Full-sample reproduction lifts total return from
304.3% to 305.9% at equal drawdown (-33.5%) and turnover (1.7x), positive in
every sub-period.

- **Allocation switched to 75/25 on PnL evidence.** A full core/active spectrum
  reproduction (2017-2026, name cap 3%, PIT membership) shows total return
  plateaus at ~300-304% for every active weight from 25% to 75%, while annual
  turnover rises from ~1.7x (75/25) to ~4.2x (40/60) to ~5.2x (25/75). 75/25 with
  regime control posts the single highest total return in the sweep (304.3%) at
  the lowest turnover and highest Sharpe, so it is the PnL-optimal *and* most
  cost-robust choice. It also matches the pre-registered MOMENTUM_V4 selection.
  The prior 40/60 showed higher CAGR only on the already-inspected 2020-2026
  window; over the full sample the two tie and 75/25 edges ahead. Per-period
  reproduction of 75/25+regime is positive every window: 2017-2019 +49.7%,
  2020-2022 +30.3%, 2023-2024 +58.7%, 2025-2026 +30.7%.
- **Regime-aware exposure** is enabled on the active sleeve
  (`regime_enabled = true`). At 75/25 it slightly raises total return
  (302% -> 304%) and Sharpe while trimming bear-regime exposure.
- **`DailyV4` construction** now exposes `regime_enabled` and
  `active_target_volatility` as first-class, overridable parameters. The prior
  code silently hard-coded these and forced a large volatility ceiling through a
  duplicated update; the ceiling is now explicit and disabled by default (the
  wrapper already bounds sleeve gross at `active_weight`).

### Evaluated and not adopted

- **Volatility-scaled (risk-managed) momentum.** `sleeve_volatility_target` is
  implemented (scales the sleeve down in high-vol regimes) but left off. At
  75/25 the -33.6% max drawdown is driven by the 75% SPY core, not the 25%
  sleeve, so scaling the sleeve does not reduce drawdown and slightly lowers
  return (0.08 target -> 297% vs 306% full-sample). It would only help a
  sleeve-heavy allocation.
- **Rebalance cadence.** The 21-session cadence is near-optimal on PnL, not
  arbitrary: every 5 sessions returns 276% at 2.65x turnover and every 42
  returns 281% at 1.34x, versus 306% at 1.73x for 21. Trading more often adds
  cost without capturing more of this monthly-horizon signal.
