# Project Geld: Broad Point-in-Time Universe Research

## Outcome

Expanding beyond the original 34-stock basket materially changes the conclusion. Momentum V2 does exhibit useful downside control in a broad, historically formed universe, but a strict locked validation does not show higher risk-adjusted performance than SPY.

The strongest full-sample variant holds 10 stocks, but that holding count was discovered after looking at the full sample and is therefore a research lead, not validated alpha.

## Universe construction

- Alpaca asset master records: 33,101
- Exchange-listed common-stock candidates after classification: 6,329
- Adjusted SIP daily bars: approximately 11 million
- Historical period: 2016-01-04 through 2026-07-15
- Monthly portfolio universe: top 500 stocks by trailing median dollar volume
- Distinct stocks appearing in at least one monthly universe: 1,092

At each month-end, a stock must satisfy all of the following using only data then available:

- NYSE, Nasdaq, or AMEX listing in Alpaca's asset master
- Common-stock-like security classification
- Price of at least $5
- At least 252 observed trading sessions
- At least $20 million trailing 60-session median dollar volume
- Rank among the 500 most liquid eligible stocks

IPOs enter only after accumulating sufficient history. Inactive and acquired companies are included where Alpaca retains their asset and bar history. Current `active` and `tradable` fields are saved for audit but are not used to decide historical membership.

Alpaca documents that the Assets endpoint includes an asset master and that historical SIP data is available from 2016. IEX is a single-exchange feed, while SIP consolidates US exchanges:

- <https://docs.alpaca.markets/us/v1.4.2/reference/get-v2-assets-1>
- <https://docs.alpaca.markets/us/docs/about-market-data-api>
- <https://docs.alpaca.markets/us/v1.4.2/docs/historical-stock-data-1>

## Original five-holding strategy

Results are highly sensitive to how a held stock that stops printing bars is processed:

| Missing-price exit assumption | CAGR | Sharpe | Maximum drawdown |
|---|---:|---:|---:|
| Last observed adjusted price, no haircut | 16.75% | 0.789 | -34.25% |
| Last observed price with 25% haircut | 10.00% | 0.522 | -46.65% |

There were only 11 forced exits, but Momentum V2 had concentrated positions in them. Alpaca's corporate-actions endpoint confirms eight as cash or stock-and-cash mergers. The other three—IDTI, RHT, and ZAYO—were also acquisitions according to the acquiring companies' completion announcements:

- <https://www.renesas.com/en/about/press-room/renesas-completes-acquisition-integrated-device-technology-0>
- <https://www.ibm.com/investor/news/ibm-completes-acquisition-of-red-hat>
- <https://www.zayo.com/newsroom/zayo-completes-transition-to-a-private-company/>

For this sample, no haircut is closer to the economic event than an arbitrary 25% loss, although exact merger consideration remains preferable.

## Diversification stress

Using zero haircut for the acquisition exits:

| Holdings | CAGR | Sharpe | Maximum drawdown | Annual turnover |
|---:|---:|---:|---:|---:|
| 5 | 16.75% | 0.789 | -34.25% | 7.86x |
| 10 | 18.08% | 0.926 | -25.38% | 8.59x |
| 20 | 12.18% | 0.797 | -22.69% | 7.84x |
| 40 | 11.18% | 0.786 | -20.69% | 6.79x |

Ten holdings has the best full-sample trade-off. Five holdings is too concentrated for a 500-stock candidate universe. During 2021, it concentrated in names including MVIS, MARA, RIOT, GME, AMC, OCGN and speculative biotechnology stocks; that cohort contributed to the subsequent drawdown.

## Strict locked validation

To avoid selecting the holding count from future performance, the engine used only 2017–2019 to choose among 5, 10, 20 and 40 holdings. That training window selected 40. The holding count was then locked for 2020 through 2026.

| 2020–2026 validation | CAGR | Sharpe | Maximum drawdown |
|---|---:|---:|---:|
| Broad Momentum V2, fixed 40 holdings | 12.86% | 0.817 | -20.29% |
| Broad top-500, 75% equal weight | 10.75% | 0.690 | -29.83% |
| SPY, 100% buy-and-hold | 15.40% | 0.821 | -33.79% |

Momentum V2 beat broad equal weight and substantially reduced drawdown relative to SPY. It did not beat SPY on return, and its Sharpe was effectively the same. This supports a defensive momentum interpretation, not a clear alpha claim.

## Rolling selection diagnostic

A three-year rolling holding-count selector produced 21.34% CAGR, 0.960 Sharpe and -29.44% drawdown from 2020 onward. This is encouraging but optimistic: switching between separately simulated strategy states at calendar-year boundaries does not exactly reproduce one continuously traded portfolio. It should not be used as the headline result until implemented as a single causal meta-strategy with explicit transition trades.

## Remaining limitations

- Alpaca's asset master does not provide a complete historical as-of security-type classification.
- The name-based common-stock classifier may exclude some legitimate REITs or include unusual securities.
- Reused tickers and historical symbol mappings can conflate entities.
- Exact merger proceeds and stock conversions are not yet booked by the backtester.
- Point-in-time sectors are unavailable, so the broad tests remove sector caps.
- The 500-stock, $5, $20-million-liquidity rules are reasonable research choices but were not independently validated.
- Taxes, borrow, market impact, opening-auction capacity, and signal crowding are not modeled.

## Decision

Keep the existing 34-stock paper pilot unchanged for now. Do not replace it with the broad strategy yet.

The broad research suggests the next candidate should use:

- The monthly top-500 point-in-time liquidity universe
- More than five holdings
- Explicit volatility or concentration controls
- Corporate-action-aware proceeds
- A continuously executed walk-forward parameter policy

The fixed-40 validation is defensible enough to continue research, while the full-sample 10-holding result is worth testing as a challenger. Neither result is sufficient to scale beyond paper trading.

## Reproduce

```powershell
.venv\Scripts\python.exe scripts\broad_universe_research.py `
  --start 2016-01-01 `
  --end 2026-07-16 `
  --top-n 500 `
  --minimum-price 5 `
  --minimum-dollar-volume 20000000 `
  --history-sessions 252 `
  --output artifacts\research-broad

.venv\Scripts\python.exe scripts\broad_exit_stress.py
.venv\Scripts\python.exe scripts\broad_diversification_stress.py
.venv\Scripts\python.exe scripts\broad_walk_forward.py
.venv\Scripts\python.exe scripts\broad_fixed_validation.py
```

Primary result files:

- `artifacts/research-broad/fixed-holdings-validation.csv`
- `artifacts/research-broad/diversification-stress.csv`
- `artifacts/research-broad/missing-price-exit-stress.csv`
- `artifacts/research-broad/holding-count-walk-forward-comparison.csv`
- `artifacts/research-broad/monthly-selected-universe.csv.gz`
- `artifacts/research-broad/forced-exit-corporate-action-audit.csv`
- `artifacts/research-broad/run-summary.json`
