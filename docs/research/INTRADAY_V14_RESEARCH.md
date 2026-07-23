# Intraday V14 high-activity research

## Objective

V14 tests whether the point-in-time liquid-stock universe can support a
near-daily intraday strategy. It ranks three completed 15-minute relative
returns at 10:30 ET, holds four winners long and four losers short at 10% each,
and targets zero at 15:45 ET. The resulting book is market-neutral with 80%
gross exposure and trades on approximately 99% of sessions.

V14 is a research challenger. It is not the scheduled paper strategy.

## Executable IEX result

The matched July 2020-July 2026 screen used point-in-time membership, next-bar
entry and exit prices, two-sided slippage, daily compounding, and the existing
same-day flatten convention.

| Slippage per side | Total return | Sharpe | Max drawdown | Active sessions |
|---:|---:|---:|---:|---:|
| 0 bps | 40.28% | 0.634 | -16.34% | 99.0% |
| 2 bps | -11.14% | -0.156 | -25.82% | 99.0% |
| 4 bps | -43.72% | -0.945 | -48.55% | 99.0% |
| 8 bps | -77.43% | -2.524 | -78.48% | 99.0% |

The zero-cost result was not stable: the 2025-July 2026 test period returned
-7.90% before slippage. At 8 bps, annualized turnover was approximately 399
times capital. Both the momentum and reversal families lost money after costs.

## Decision

V14 is rejected for paper promotion. It achieves the requested activity by
forcing roughly 16 orders per session, but its raw edge is smaller than even a
two-basis-point-per-side implementation assumption. Paper fills would not make
this economically executable because Alpaca paper trading omits real queue
position, spread, and market-impact costs.

The current V13 schedule remains in place while the next challenger focuses on
lower-turnover daily instruments or a materially higher-confidence signal. The
research screen is reproducible with:

```powershell
.venv\Scripts\python.exe -u scripts\intraday_v14_research.py
```
