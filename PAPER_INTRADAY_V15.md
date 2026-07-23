# Intra V15 paper readiness

Intra V15 uses `configs/paper-intra-v15.toml` and the isolated intraday Alpaca
paper account. The Alpaca trading client remains hard-coded to `paper=True`.

The paper model combines:

- a confidence-scaled SPY opening-trend target at 10:30 ET;
- 5% long or 2.5% short after a five-basis-point move, and 0.5% otherwise;
- the selective V13 short overlay after its 10:30/10:45 filters;
- 45% maximum gross exposure;
- a 15:30 SPY exit and mandatory 15:45 overlay exit.

The weekday task `ProjectGeld-IntraV15-Paper` starts at 09:25 ET. The runner
evaluates each completed 15-minute bar, retries transient failures, owns a named
process lock, and writes dated logs plus `runner-heartbeat.json` under
`artifacts/paper-intra-v15`.

Execution recovery is enabled for the paper runner. Managed entry limits older
than five minutes are cancelled before the next decision cycle, then the target
is recalculated against the actual filled position and current reference price.
This also handles partial fills without doubling the desired exposure. Mandatory
15:30 and 15:45 flat targets use market orders so an unfilled exit limit cannot
remain overnight. Cancellation events are retained in
`artifacts/paper-intra-v15/order_cancellations.csv`.

Manual dry run:

```powershell
.\scripts\run-intra-v15-paper.ps1 -DryRun
```

The computer must be powered on, connected, and logged into Windows. V15 is a
paper experiment whose daily sleeve is highly cost-sensitive; paper fills do
not represent real queue position, spreads, impact, or borrow conditions.

## Applied improvements (July 22, 2026) — Intra V15.0.5

Patch trail: 15.0.1 minimum-trade floor fix, 15.0.2 implementation-shortfall
tracking, 15.0.3 intraday universe-staleness guard, 15.0.4 continuous confidence
sizing for the base sleeve, 15.0.5 automatic shortfall kill-switch.

15.0.5 detail: each submitting cycle checks the trailing implementation
shortfall for the base sleeve's core symbol. If the mean over the recent filled
orders exceeds 2 bps (the research's invalidation gate), the base sleeve is
forced flat for that cycle and only the overlay trades. The check reads
`implementation_shortfall.csv`, is fail-safe (a read error never blocks
trading), and the selective V13 overlay is unaffected.

15.0.4 detail: the base SPY sleeve now ramps continuously from the weak floor at
5 bps to full weight at 15 bps rather than stepping straight to full. Exact IEX
reproduction improves total return (3.26% -> 3.45%), Sharpe (0.82 -> 0.88), and
lowers both turnover (18.3x -> 16.2x) and drawdown, with all sub-periods positive
and activity held at 99.7%.

- **Implementation-shortfall tracking.** Each submitting cycle now compares the
  decision reference price to the realized Alpaca fill and appends per-order
  shortfall (bps), fill rate, and missed-order flags to
  `artifacts/paper-intra-v15/implementation_shortfall.csv`, printing the average.
  This operationalizes the research's two-basis-point invalidation gate for the
  daily SPY sleeve. Fill logic lives in `paper.implementation_shortfall` and is
  unit-tested.
- **Minimum-trade floor lowered to 0.4% of equity** (`min_trade_pct_equity`).
  The 0.5% weak-signal base leg previously equalled the floor and was suppressed
  by whole-share rounding. In the exact IEX reproduction the fix raises the
  active-session rate from 94.0% to 99.7% while total return and every
  train/validation/test period stay positive (3.26% total; +0.94% / +0.39% /
  +1.91%).
- **Universe-age guard** now also protects `intraday-paper-once`; a snapshot
  older than `max_universe_age_days` stops planning, matching the daily path.
