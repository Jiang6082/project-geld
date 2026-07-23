# Intraday V15 hybrid research

## Design

V15 retains the selective V13 stock-short overlay and adds a confidence-scaled
SPY opening-trend sleeve. At the completed 10:30 ET bar, an opening move of at
least five basis points targets 5% long or 2.5% short. Weaker nonzero moves use
a 0.5% fallback target so the strategy remains active on most sessions without
taking full risk on noise. The SPY sleeve targets zero at 15:30; the overlay
targets zero at 15:45. Maximum gross exposure is 45%.

The base sleeve exists to make the intraday account active on most sessions
without imposing V14's approximately 399-times annual turnover. SPY uses a
two-basis-point research assumption; the individual-stock overlay retains its
eight-basis-point assumption.

## Matched IEX hybrid result

The July 2020-July 2026 diagnostic combines the locked executable V13 IEX
return stream with the causal SPY base sleeve.

| SPY cost per side | Total return | Sharpe | Max drawdown | Active sessions |
|---:|---:|---:|---:|---:|
| 0.5 bps | 4.80% | 1.197 | -0.49% | 98.9% |
| 1.0 bps | 4.26% | 1.068 | -0.54% | 98.9% |
| 2.0 bps | 3.21% | 0.809 | -0.63% | 98.9% |
| 4.0 bps | 1.13% | 0.289 | -1.12% | 98.9% |
| 8.0 bps | -2.91% | -0.751 | -3.92% | 98.9% |

At the two-basis-point decision case, training returned 0.93%, 2023-2024
validation returned 0.34%, and the untouched 2025-July 2026 test returned
1.90%. A production-path replay of the recent period returned 1.92%, with a
1.38 Sharpe, -0.35% maximum drawdown, and trades on 93.6% of sessions. The
difference in activity is caused by missing IEX prints and executable minimum
order thresholds.

The old unconditional 10:00 V15 returned 2.60% with a 0.535 Sharpe and -1.52%
drawdown at the same two-basis-point assumption. The revised rule therefore
improves return, risk-adjusted return, and drawdown, but remains a
paper-observation candidate rather than established alpha. Sparse V13 remains
the more efficient control; activity is not itself evidence of an edge. Its
viability depends on keeping actual SPY implementation shortfall below
approximately two basis points per side.

## Promotion status

V15 replaces V13 only in the isolated Alpaca paper schedule. V13 remains
registered and reproducible as the selective control. Paper review must track
SPY decision price, limit price, fill price, fill rate, and missed orders; an
average implementation shortfall above two basis points invalidates the daily
base sleeve.
