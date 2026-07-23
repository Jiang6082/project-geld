# Versioned strategy research

Canonical production-facing names use the trading horizon followed by a version:

- `daily_v4`: current daily control, 75% SPY core plus 25% active residual
  momentum with regime-aware exposure (see PAPER_DAILY_V4.md for the July 2026
  PnL-driven switch from the earlier 40/60 challenger).
- `daily_v5`: benchmark-aware Daily V4 challenger.
- `intra_v1`: current 15-minute relative-reversal control.
- `intra_v2`: selective once-daily relative-reversal challenger.
- `intra_v3`: broader V2 allocation with eight 10% name slots and 80% maximum gross.
- `intra_v4`: relative-continuation test using V3's allocation controls; rejected.
- `intra_v5`: delayed recovery confirmation after a 0.60% relative dislocation.
- `intra_v6`: stricter confirmed reversal requiring a 1.00% relative dislocation.
- `intra_v7`: opt-in confirmed downside continuation; paper-unsupported.
- `intra_v8`: V7 aligned with a prior 20-session downtrend.
- `intra_v9`: rejected V8 unusual-volume confirmation.
- `intra_v10`: rejected V8 prior-volatility normalization.
- `intra_v11`: rejected causal rolling ridge filter for V8 setups.
- `intra_v12`: current quiet-volume, decisive-break short challenger.

The old registry names `momentum_v4` and `intraday_momentum` remain aliases so
old notebooks do not break. New configs and artifacts must use canonical names.

## Daily results

The training period is 2017–2019. The already-inspected diagnostic period is
2020 through July 15, 2026. Both include 10 bps modeled slippage.

| Strategy and period | CAGR | Sharpe | Max drawdown | Annual alpha | Beta | Annual turnover |
|---|---:|---:|---:|---:|---:|---:|
| Daily V4 training | 14.60% | 1.032 | -21.66% | -0.33% | 1.019 | 4.20x |
| Daily V5 training | 14.31% | 1.019 | -21.57% | -0.59% | 1.019 | 4.20x |
| Daily V4 diagnostic | 16.21% | 0.848 | -33.32% | 1.95% | 0.919 | 4.23x |
| Daily V5 diagnostic | 16.65% | 0.858 | -33.64% | 2.16% | 0.933 | 4.18x |

Daily V5 modestly improves the inspected-period return, Sharpe, alpha, and
turnover, but is slightly worse on the earlier training period and drawdown.
It remains a challenger; Daily V4 remains the paper control.

A defensive Daily V5 overlay was also tested. It produced a 14.23% diagnostic
CAGR, 0.901 Sharpe, -24.52% maximum drawdown, 3.95% annual alpha, and 0.65 beta.
That is useful when drawdown reduction is the objective, but it is not the
canonical growth configuration because of the return sacrifice.

## Intraday results

IEX one-minute data were aggregated to completed 15-minute bars. Training is
April–May 2026 and the later test is June 1–July 17, 2026. These short periods
are candidate diagnostics, not durable evidence.

At the conservative eight-basis-point one-way slippage assumption:

| Strategy and period | Return | Sharpe | Max drawdown | Annual turnover | Orders |
|---|---:|---:|---:|---:|---:|
| Intra V1 full | -1.94% | -0.671 | -4.61% | 297.6x | 748 |
| Intra V1 later test | -1.97% | -1.339 | -4.08% | 300.9x | 327 |
| Intra V2 full | 2.78% | 1.118 | -3.08% | 103.7x | 215 |
| Intra V2 later test | -0.63% | -0.392 | -3.08% | 105.3x | 95 |

Intra V2 was selected using only the earlier training window: 10:30 entry,
two-bar lookback, 0.60% minimum SPY-relative dislocation, three names, and 45%
maximum gross exposure. It materially improves V1 and is less cost-sensitive,
but its untouched later test remains negative at four and eight bps. It is not
approved to replace Intra V1 or submit orders.

At two bps, Intra V2 returned 4.63% over the full period and 0.16% in the later
test. This narrow execution margin is why limit prices, missed-fill tracking,
and a longer shadow sample are mandatory.

## Intra V3 allocation experiment

Intra V3 preserves V2's signal and changes only the allocation ceiling: up to
eight names, 10% per name, and 80% gross exposure. At 10:30 it ranks eligible
names by `SPY two-bar return - stock two-bar return`. Eligibility requires at
least $1 million of latest-bar dollar volume, a stock below its session VWAP,
at least 0.60% underperformance versus SPY, and SPY above its own session VWAP.

| 8-bps result | Intra V2 | Intra V3 |
|---|---:|---:|
| Full-period return | 2.78% | 1.74% |
| Full-period Sharpe | 1.118 | 0.914 |
| Maximum drawdown | -3.08% | -2.22% |
| Annual turnover | 103.7x | 93.8x |
| Later-test return | -0.63% | -0.84% |

The broader cap did not increase realized exposure much. Across 74 sessions,
no name qualified on 28 days, the median was one name, the mean was 1.85 names
(18.5% entry gross), and all eight slots filled on only one day. Therefore the
0.60% dislocation and VWAP filters—not the eight-name ceiling—usually determine
capital usage. V3 is retained as requested but does not replace V2 based on this
diagnostic.

The later multi-year test in `LONG_INTRADAY_RESEARCH.md` supersedes this short
diagnostic for strategy decisions. Both V2 and V3 lose money from July 2020 to
July 2026 even before modeled slippage, so neither is approved for submission.

## Intra V4 through V6 long-history follow-up

The same native 15-minute IEX dataset was used from July 27, 2020 through July
17, 2026. V4 tested the opposite direction and bought morning relative winners;
it also lost money before costs. V5 returned to reversal but waited one bar and
required the stock to close above the original signal bar's high. V6 made that
confirmed setup rarer by raising the required dislocation from 0.60% to 1.00%.

| Strategy, 8 bps one way | Total return | Sharpe | Max drawdown | Annual turnover | Orders |
|---|---:|---:|---:|---:|---:|
| Intra V3 | -20.83% | -0.907 | -23.08% | 37.22x | 2,294 |
| Intra V4 | -33.07% | -0.877 | -35.22% | 70.80x | 4,407 |
| Intra V5 | -3.04% | -0.321 | -4.32% | 8.64x | 529 |
| Intra V6 | -0.37% | -0.066 | -1.86% | 2.21x | 142 |

V6 is a substantial rejection-quality improvement, not demonstrated alpha. It
earns only 0.69% total before costs, turns negative at eight bps, trades on 61
sessions, and remains paper-disabled. The small grid that produced V6 used this
same history, so the result is in-sample and requires new forward evidence.

## Intra V7 through V9 short-continuation follow-up

The broad 100-stock transfer rejects V6 and V7 at eight-basis-point slippage.
V8 is positive by 0.93% with a 0.149 Sharpe, but almost all profit occurs in
partial 2026. V9 loses 1.25%. No version demonstrates stable alpha. Short
targets are supported only by an opt-in backtest path; paper planning rejects
them. See `INTRADAY_V7_V9_RESEARCH.md` for the protocol, limitations, and full
comparison. V10 subsequently lost 1.78% with a -0.344 Sharpe, so the locked V8
forward-shadow study remains unchanged.

V11's rolling, prior-session-only ridge filter lost 1.28% with a -0.502 Sharpe
and is rejected. V12 reverses the failed V9 volume idea: it excludes unusually
high-volume setups and requires the confirmation close to finish at least 0.25%
below the signal low. It returned 2.61% at eight-basis-point slippage, with a
1.033 Sharpe and -0.21% maximum drawdown. Only 19 completed positions generated
that result, and the hypothesis was developed from the same biased broad sample,
so V12 remains research-only with paper execution disabled.
