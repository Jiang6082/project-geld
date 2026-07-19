# Intraday V7–V10 short-continuation research

## Question and protocol

The V3 rank analysis suggested that the largest morning laggards often kept
falling rather than reverting. V7–V9 test that observation directly with short
positions. The backtester now supports signed target weights only when
`backtest.allow_short = true`. Gross exposure uses absolute position values,
short sales receive adverse sell slippage, and covers receive adverse buy
slippage. Paper planning still rejects every negative target.

The broad diagnostic uses 3,699,872 native 15-minute IEX bars for 100 stocks
plus SPY from January 3, 2019 through July 17, 2026. The universe is a fixed
July 15, 2026 top-liquidity snapshot, not point-in-time historical membership.
It therefore has survivorship and future-membership bias. Results use eight
basis points of modeled one-way slippage and no borrow fees. Historical
shortability, locate availability, hard-to-borrow charges, bid/ask spreads, and
market impact are unavailable and could make real results worse.

## Strategies

- **V7:** At 10:30, mark stocks at least 1% behind SPY and below session VWAP.
  Short only if the next completed bar closes below the signal bar's low while
  SPY is above its VWAP. Cover before the close.
- **V8:** Apply V7 only when the prior close is below its prior 20-session
  average and the current price is below the prior close.
- **V9:** Apply V8 only when the signal-bar volume is at least 1.5 times its
  prior 20-session median for the same bar time.
- **V10:** Apply V8 only when the morning dislocation is at least two times the
  stock's own prior 20-session variability at the same signal time.

All variants allow at most four names, 10% per name, and 40% gross exposure.

## Results

| Strategy and universe | Total return | CAGR | Sharpe | Max drawdown | Annual turnover | Orders |
|---|---:|---:|---:|---:|---:|---:|
| V6 long reversal, broad 100 | -2.97% | -0.40% | -0.173 | -7.56% | 8.95x | 702 |
| V7 short continuation, original 21 | -0.00% | -0.00% | 0.004 | -2.23% | 2.69x | 248 |
| V7 short continuation, broad 100 | -7.48% | -1.03% | -0.527 | -10.31% | 10.75x | 1,281 |
| V8 trend-aligned short, original 21 | 0.25% | 0.04% | 0.105 | -0.86% | 0.54x | 50 |
| V8 trend-aligned short, broad 100 | 0.93% | 0.12% | 0.149 | -2.63% | 2.53x | 299 |
| V9 volume-confirmed short, broad 100 | -1.25% | -0.17% | -0.219 | -2.48% | 1.62x | 195 |
| V10 volatility-normalized short, broad 100 | -1.78% | -0.24% | -0.344 | -2.86% | 1.25x | 151 |
| SPY buy and hold, broad dates | 153.66% | — | — | — | — | — |

V8 is the best result but is not stable across time. Its broad calendar returns
are approximately 0.20% in 2021, -0.06% in 2022, -0.15% in 2023, -1.02% in
2024, -0.24% in 2025, and 2.22% in partial 2026. Almost all profit therefore
comes from the latest partial year. It forms only 94 completed day-symbol
positions across 55 stocks, with a 45.7% win rate and a negative median trade.

V7's broad loss shows that unfiltered downside breakouts are not alpha. V8's
daily trend filter removes a bad subset, but the remaining return is too small
and recent-year-dependent. V9's failure shows that unusual volume does not
repair the instability. V10's failure shows that normalizing by prior morning
variability also removes useful trades without creating a stable edge.

## Decision

No tested intraday version demonstrates true alpha. V8 is retained as a locked
research hypothesis, not promoted to paper execution. Its public configuration
has `paper.enabled = false`, and the paper planner refuses short targets even in
dry-run order planning. The next valid evidence must be a new forward shadow
sample that records signal-time bid/ask spreads, shortable/easy-to-borrow status,
locate failures, and hypothetical limit fills. Further threshold fitting on
this historical sample would increase overfitting rather than confidence.

Broad Alpaca retrieval is now cached in resumable 25-symbol batches so future
universe studies can restart without losing completed downloads.
