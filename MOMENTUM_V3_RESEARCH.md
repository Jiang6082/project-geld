# Momentum V3 broad-universe research

## Decision

Momentum V3 is implemented, but the locked candidate is **not approved for
paper submission**. Its fixed-split result did not improve on Momentum V2 or
SPY on a risk-adjusted basis.

The current V2 paper configuration is unchanged. V3's separate configuration
has `paper.enabled = false`.

## What V3 does

At each 21-session rebalance, V3 starts from the point-in-time monthly top-500
liquid-stock universe and:

1. calculates 12-1 and 6-1 momentum;
2. removes the portion associated with each stock's rolling beta to SPY;
3. combines residual momentum, trend strength, and low downside volatility;
4. requires positive raw momentum, price above its 200-day average, and the
   50-day average above the 200-day average;
5. retains eligible incumbents inside an exit-rank buffer;
6. selects 30 or 40 names while rejecting excessively correlated additions;
7. uses capped inverse-downside-volatility weights, with a 4% name limit;
8. varies gross exposure using SPY's trend and broad-market breadth; and
9. scales exposure down when forecast portfolio volatility exceeds 15%.

SPY is signal-only context. The backtest and paper planner explicitly prevent
it from becoming a V3 stock position. An optional sector-count cap is present,
but the Alpaca asset master does not supply sector classifications, so the
broad run used the correlation constraint rather than pretending to have
sector data.

## Research protocol

- Bars: cached Alpaca SIP daily data, 2016-01-04 through 2026-07-15.
- Universe: monthly point-in-time top 500 by trailing dollar volume after the
  established price, history, and liquidity filters.
- Distinct historically eligible stocks: 1,092.
- Execution: signal at close, fill at the following session's open.
- Slippage: 10 basis points.
- Training selection: 2017-01-01 through 2019-12-31.
- Fixed validation: 2020-01-01 through 2026-07-15.
- Training objective: Sharpe + CAGR + 0.5 × max drawdown − 0.01 × annual
  turnover.

The fixed validation is only pseudo-out-of-sample: earlier V2 research had
already inspected these years. True confirmation now requires future shadow
or paper observation.

## Locked result

Training selected `strict_correlation_40` without looking at its validation
score.

| Portfolio | CAGR | Sharpe | Max drawdown | Annual turnover |
|---|---:|---:|---:|---:|
| V3 selected: strict correlation, 40 stocks | 8.89% | 0.655 | -21.25% | 6.25x |
| V2 fixed 40 stocks | 12.86% | 0.817 | -20.29% | 6.75x |
| Broad equal weight, 75% exposure | 10.75% | 0.690 | -29.83% | 1.24x |
| SPY buy-and-hold, 75% exposure | 13.59% | 0.832 | -28.52% | 0.00x |
| SPY buy-and-hold, 100% exposure | 15.40% | 0.821 | -33.79% | 0.00x |

The other V3 validation variants were:

| Variant | CAGR | Sharpe | Max drawdown | Average gross exposure |
|---|---:|---:|---:|---:|
| balanced 40 | 9.93% | 0.704 | -21.13% | 72.3% |
| balanced 30 | 10.39% | 0.707 | -21.67% | 67.7% |
| strict correlation 40 | 8.89% | 0.655 | -21.25% | 74.3% |
| always on 40 | 11.48% | 0.652 | -33.33% | 83.8% |

## Interpretation

The residual-momentum and diversification ideas were useful, but the 15%
forecast-volatility ceiling was too blunt: it reduced return more than it
improved the already-good V2 drawdown. Removing the ceiling in an earlier
diagnostic raised the balanced-40 validation CAGR to 14.04% with a 0.818
Sharpe and a -21.13% drawdown, but that observation is now in-sample knowledge
and cannot be promoted as clean evidence.

This means V3 produced a promising research direction, not a deployable alpha
result. The honest next experiment is a forward-only shadow portfolio
comparing V2, V3 balanced-40 without the hard ceiling, and SPY at matched
exposure. Changing the historical rule again and selecting whichever version
wins 2020-2026 would be overfitting.

## Reproduce

    .venv\Scripts\python.exe scripts\broad_v3_research.py

Machine-readable results are under
`artifacts/research-broad/momentum-v3/`, including the training ranking,
variant metrics, fixed-validation comparison, equity curves, trades, and run
summary.
