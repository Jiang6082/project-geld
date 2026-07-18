# Long-horizon intraday research

## Dataset

The requested range was January 2016 through July 18, 2026. Alpaca's IEX
response available to this account begins July 27, 2020, so the realized test
contains 879,214 native 15-minute bars for 22 symbols through July 17, 2026.
Native bars are timestamped at their start by Alpaca and shifted to their end
before signal generation. Signals execute no earlier than the next bar.

This is a fixed current universe, not point-in-time historical membership.
PLTR and COIN enter only after their listings, but delisted names and historical
universe changes are absent. Results therefore retain survivorship and universe-
selection limitations. IEX is also not the consolidated SIP feed.

Reproduction commands:

```powershell
geld --config configs/research-intra-v2.toml intraday-backtest --source alpaca --native-bars --start 2016-01-01 --end 2026-07-18 --output artifacts/research-intra-v2-long
geld --config configs/research-intra-v3.toml intraday-backtest --source alpaca --native-bars --start 2016-01-01 --end 2026-07-18 --output artifacts/research-intra-v3-long
```

## Full-period results

| Metric | Intra V2 | Intra V3 | SPY benchmark |
|---|---:|---:|---:|
| Total return, 8 bps | -25.84% | -20.83% | 152.36% |
| CAGR, 8 bps | -4.88% | -3.84% | — |
| Sharpe, 8 bps | -0.868 | -0.907 | — |
| Maximum drawdown, 8 bps | -28.86% | -23.08% | — |
| Annual turnover | 47.77x | 37.22x | — |
| Orders | 2,012 | 2,294 | — |
| Total return, zero slippage | -6.77% | -5.37% | — |
| Sharpe, zero slippage | -0.183 | -0.199 | — |

The zero-cost losses reject the idea that implementation cost is the only
problem. The underlying relative-reversal signal is negative over this sample.

## Calendar results at eight bps

2020 and 2026 are partial years.

| Year | Intra V2 | Intra V3 | SPY |
|---:|---:|---:|---:|
| 2020 | -1.72% | -1.23% | 17.18% |
| 2021 | -9.59% | -6.91% | 28.76% |
| 2022 | -3.26% | -2.82% | -18.16% |
| 2023 | 1.52% | 1.63% | 26.18% |
| 2024 | -5.16% | -4.78% | 24.73% |
| 2025 | -8.45% | -6.80% | 18.48% |
| 2026 | -2.11% | -1.77% | 9.60% |

Only 2023 is positive. The strategy does not become defensive in the 2022 bear
market; it still loses money while holding mostly cash.

## Why V3 behaves differently

V2 gives the top three qualifiers 15% each. V3 gives up to eight qualifiers 10%
each. Because few names pass the 0.60% dislocation and VWAP filters, V3's mean
entry exposure is only about 7.4%, versus 9.5% for V2. V3 therefore loses less
in dollars and has lower drawdown largely because it takes less risk.

Average trade return by V3 entry rank after modeled costs:

| Rank | Trades | Mean trade return | Win rate |
|---:|---:|---:|---:|
| 1 | 518 | -0.407% | 45.8% |
| 2 | 272 | -0.184% | 48.2% |
| 3 | 159 | -0.070% | 49.1% |
| 4 | 82 | -0.427% | 46.3% |
| 5 | 46 | 0.083% | 47.8% |
| 6 | 22 | -0.290% | 31.8% |
| 7 | 11 | 0.060% | 45.5% |
| 8 | 4 | 0.242% | 75.0% |

Ranks 7 and 8 have far too few observations to infer an edge. The worst average
result comes from rank 1, the largest laggard. That is evidence of short-horizon
continuation or unresolved sell pressure, not reliable mean reversion.

## Decision

Neither Intra V2 nor Intra V3 should submit paper orders in its current form.
V3 remains implemented as an allocation experiment, but a subsequent strategy
should change the signal itself—such as requiring a genuine recovery pattern or
testing continuation—rather than merely increasing position limits.
