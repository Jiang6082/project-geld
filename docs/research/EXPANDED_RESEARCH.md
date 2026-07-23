# Project Geld: Expanded Momentum V2 Research

## Decision

Momentum V2 is suitable for a small paper pilot, but the expanded evidence does **not** establish durable alpha. The attractive full-sample result depends materially on the present-day stock basket. The strategy does not consistently beat simpler equal-weight portfolios in walk-forward or historical-universe tests.

Do not scale the pilot beyond the existing approximately $100-per-order cap until forward paper results cover several rebalance cycles.

## Data and evaluation design

- Alpaca adjusted SIP daily bars from 2016-01-04 through 2026-07-15.
- 2,647 SPY trading sessions and 62 requested stocks/ETFs.
- A common evaluation start of 2017-04-05 gives every parameter variant the same 316-session signal warmup.
- Signals are calculated after a session closes and trades occur at the next session's open.
- Base simulations include 10 basis points of one-way slippage.
- The paper/live configuration remains on IEX. SIP is used only for historical research.

Alpaca documents historical equity coverage since 2016 and permits the historical SIP feed subject to its recency restriction. IEX is a single-exchange feed, whereas SIP consolidates US exchanges:

- <https://docs.alpaca.markets/us/docs/about-market-data-api>
- <https://docs.alpaca.markets/us/v1.4.2/docs/historical-stock-data-1>

## Main comparison

| Approach | CAGR | Sharpe | Maximum drawdown |
|---|---:|---:|---:|
| Momentum V2, current 34-stock basket | 20.00% | 1.198 | -21.09% |
| Same basket, 75% equal weight | 16.48% | 1.200 | -24.79% |
| SPY, maintained near 75% exposure | 11.41% | 0.868 | -26.04% |
| SPY, 100% buy-and-hold | 15.15% | 0.869 | -33.79% |

The strategy improved return and drawdown over equal weighting on the current basket, but it did not improve Sharpe. This is a warning that stock selection may explain much of the apparent advantage.

## Rolling walk-forward evidence

Each test year from 2019 onward used only the preceding three years to select from a compact, one-parameter-at-a-time stability set. The selection objective rewarded training Sharpe and penalized drawdown and turnover.

| Approach, 2019-2026 | CAGR | Sharpe | Maximum drawdown |
|---|---:|---:|---:|
| Walk-forward-selected Momentum V2 | 13.62% | 0.910 | -23.98% |
| SPY, maintained near 75% exposure | 13.14% | 0.942 | -26.04% |
| Same current basket, 75% equal weight | 18.01% | 1.255 | -24.79% |
| SPY, 100% buy-and-hold | 17.51% | 0.943 | -33.79% |

The walk-forward strategy slightly exceeded matched-exposure SPY on return and drawdown, but had a lower Sharpe and substantially underperformed equal weighting. Parameter selection changed frequently across folds, so no single specification emerged as stable.

## Universe and survivorship stress

| Universe | Approach | CAGR | Sharpe | Maximum drawdown |
|---|---|---:|---:|---:|
| Current 34 stocks | Momentum V2 | 20.00% | 1.198 | -21.09% |
| Current 34 stocks | Equal weight | 16.48% | 1.200 | -24.79% |
| Current basket excluding NVDA, META, AVGO | Momentum V2 | 14.87% | 0.989 | -19.18% |
| Current basket excluding NVDA, META, AVGO | Equal weight | 14.90% | 1.143 | -24.62% |
| Frozen January 2016 Dow proxy | Momentum V2 | 11.76% | 0.882 | -18.92% |
| Frozen January 2016 Dow proxy | Equal weight | 11.08% | 0.873 | -27.70% |
| Point-in-time Dow proxy | Momentum V2 | 7.73% | 0.623 | -18.92% |
| Point-in-time Dow proxy | Equal weight | 9.21% | 0.751 | -26.45% |
| Sector ETFs | Momentum V2 | 4.71% | 0.484 | -23.66% |
| Sector ETFs | Equal weight | 9.34% | 0.771 | -27.88% |

The point-in-time Dow proxy contains exactly 30 eligible members at each change date and forces removed constituents to zero weight while retaining price data for liquidation. The membership schedule incorporates the 2018, 2020, and 2024 changes described by S&P Dow Jones Indices:

- <https://press.spglobal.com/2018-06-19-Walgreens-Boots-Alliance-Set-to-Join-Dow-Jones-Industrial-Average>
- <https://press.spglobal.com/2020-08-24-Salesforce-com-Amgen-and-Honeywell-International-Set-to-Join-Dow-Jones-Industrial-Average>
- <https://www.spglobal.com/spdji/en/documents/indexnews/announcements/20240220-1470711/1470711_djiadjtawbajblu-feb2024.pdf>

This is a useful survivorship stress test, not a CRSP-quality historical database. It uses adjusted Alpaca bars and current symbol lineage mapping, and it does not model every delisting, merger cash payment, tax, or index announcement lag.

## Cost stress

| One-way slippage | CAGR | Sharpe | Maximum drawdown |
|---:|---:|---:|---:|
| 0 bps | 20.54% | 1.226 | -20.97% |
| 10 bps | 20.00% | 1.198 | -21.09% |
| 25 bps | 19.18% | 1.155 | -21.28% |
| 50 bps | 17.83% | 1.084 | -21.58% |
| 100 bps | 15.16% | 0.941 | -23.67% |

The current-basket result is not destroyed by severe execution costs. That does not cure its universe-selection bias.

## Regime observations

- During 2022, Momentum V2 returned 5.77%, versus -18.64% for 100% SPY and -7.91% for the 75% equal-weight current basket.
- During 2023 through mid-2026, Momentum V2 was strong, but equal weighting had the higher Sharpe.
- Before COVID and during 2020-2021, equal weighting also produced the higher Sharpe.

The trend gate and 25% cash reserve appear useful for downside control. The ranking layer has not yet demonstrated consistent incremental alpha over diversified equal weighting.

## Reproduce

```powershell
.venv\Scripts\python.exe scripts\expanded_research.py `
  --start 2016-01-01 `
  --end 2026-07-16 `
  --output artifacts\research-expanded
```

Primary outputs:

- `artifacts/research-expanded/baseline-comparison.csv`
- `artifacts/research-expanded/walk-forward-folds.csv`
- `artifacts/research-expanded/walk-forward-baseline-comparison.csv`
- `artifacts/research-expanded/parameter-stability.csv`
- `artifacts/research-expanded/cost-stress.csv`
- `artifacts/research-expanded/regime-comparison.csv`
- `artifacts/research-expanded/universe-stress.csv`
- `artifacts/research-expanded/dow-membership-proxy.csv`
- `artifacts/research-expanded/data-coverage.csv`

## Paper-forward requirement

Backtests cannot substitute for elapsed forward evidence. The paper engine already records daily equity and prevents another submitted rebalance for ten trading sessions. A useful first checkpoint is at least six completed rebalance cycles; three months is a more meaningful minimum observation window. Paper results must be evaluated against the same 75%-exposure SPY and equal-weight baselines.
