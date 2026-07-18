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

## V4 through V6 follow-up

Three subsequent versions changed signal quality while preserving the requested
ceiling of eight names, 10% per name, and 80% gross exposure:

- V4 bought 10:30 relative winners above VWAP to test continuation.
- V5 marked 0.60% relative laggards at 10:30, waited one 15-minute bar, and
  entered only after a close above the original signal bar's high.
- V6 made the V5 setup more selective by requiring a 1.00% relative lag.

All signals execute no earlier than the following 15-minute bar. All versions
flatten before the session ends. Research configurations remain paper-disabled.

| Strategy, 8 bps one way | Total return | CAGR | Sharpe | Max drawdown | Annual turnover | Orders |
|---|---:|---:|---:|---:|---:|---:|
| Intra V3 | -20.83% | -3.84% | -0.907 | -23.08% | 37.22x | 2,294 |
| Intra V4 | -33.07% | -6.50% | -0.877 | -35.22% | 70.80x | 4,407 |
| Intra V5 | -3.04% | -0.52% | -0.321 | -4.32% | 8.64x | 529 |
| Intra V6 | -0.37% | -0.06% | -0.066 | -1.86% | 2.21x | 142 |
| SPY buy and hold | 152.36% | — | — | — | — | — |

V4's zero-cost return is -5.31%, so reversing the signal direction does not
solve the problem. V5's confirmation step cuts activity and turns its zero-cost
result slightly positive at 1.02%, but eight-basis-point costs still produce a
loss. V6 has the following full cost curve:

| One-way slippage | Total return | Sharpe | Max drawdown | Annual turnover |
|---:|---:|---:|---:|---:|
| 0 bps | 0.69% | 0.133 | -1.63% | 2.22x |
| 2 bps | 0.42% | 0.083 | -1.69% | 2.22x |
| 4 bps | 0.16% | 0.034 | -1.75% | 2.22x |
| 8 bps | -0.37% | -0.066 | -1.86% | 2.21x |

V6 is sparse: it has exposure on 61 sessions and 66 completed day-symbol
positions. Its maximum realized gross exposure is about 20.7%, far below the
80% ceiling, and 20 of those positions are COIN. At eight bps the median trade
return is about -0.05% and the win rate is 47.0%. Calendar returns are positive
in 2022, 2023, 2025, and partial 2026, but negative in partial 2020, 2021, and
2024. This is neither broad nor stable enough to claim a durable edge.

## Updated decision

V6 is the best intraday research version so far because genuine recovery and
greater selectivity largely remove the V3/V4 losses. It still does not beat cash
after conservative costs, has only a small number of trades, and was chosen from
an already-inspected fixed-universe sample. It therefore remains a research
challenger, not a paper-order strategy. The next evidence should come from a
locked, forward shadow period with recorded bid/ask spreads and simulated or
observed limit-fill rates, rather than more threshold tuning on this history.

Reproduce the final studies with:

```powershell
geld --config configs/research-intra-v6.toml intraday-backtest --source alpaca --native-bars --start 2016-01-01 --end 2026-07-18 --output artifacts/research-intra-v6-2016-2026
python scripts/intraday_cost_stress.py --config configs/research-intra-v6.toml --bars data/cache/intraday/bars_61fe7c2756358b2e.csv --output artifacts/research-intra-v6-cost-stress
python scripts/intraday_v5_research.py --config configs/research-intra-v5.toml --bars data/cache/intraday/bars_61fe7c2756358b2e.csv --output artifacts/research-intra-v5-variants
```
