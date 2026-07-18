# Momentum V4.1 progress

## Implemented

V4.1 extends V4 without changing the current paper candidate. It now supports:

- filing-dated SEC Company Facts ingestion;
- gross profitability, cash profitability, accruals, leverage, and dilution;
- year-over-year revenue and earnings confirmation;
- a one-calendar-day post-filing availability lag to prevent same-day filing
  look-ahead;
- cross-sectional quality and earnings scores;
- explicit price/quality/earnings score weights;
- benchmark-aware active weighting that penalizes beta far from one and tilts
  toward stronger scores;
- component-ablation research; and
- a separate 15%-cash defensive portfolio.

The SEC downloader requires `PROJECT_GELD_SEC_USER_AGENT` in `.env`, containing
an application name and contact email. It deliberately makes no request when
that value is absent, in accordance with SEC fair-access requirements.

## Completed price-only comparison

All variants use the same broad point-in-time universe, 10-basis-point
slippage, next-session-open execution, and 21-session rebalance schedule.

| Variant | Diagnostic CAGR | Sharpe | Max drawdown | Annual turnover |
|---|---:|---:|---:|---:|
| V4 price control | 15.99% | 0.857 | -33.70% | 1.71x |
| Benchmark-aware price | 16.46% | 0.873 | -33.55% | 1.69x |
| Defensive price, 15% cash | 14.20% | 0.879 | -28.95% | 1.71x |

Benchmark-aware weighting improved the already-inspected 2020-2026 diagnostic,
but was slightly worse in 2017-2019 training: 14.45% versus 14.63% CAGR and
1.105 versus 1.117 Sharpe. It therefore remains a forward challenger rather
than replacing the frozen V4 rule based on hindsight.

The defensive variant achieved its intended tradeoff. It reduced drawdown by
4.75 percentage points and produced the highest diagnostic Sharpe, while
giving up 1.79 percentage points of annual return versus the price control.

## Pending real-data ablation

After the SEC user agent is configured, run:

    .venv\Scripts\python.exe scripts\fetch_sec_fundamentals.py
    .venv\Scripts\python.exe scripts\broad_v41_research.py

The second command will compare price-only, price plus quality, price plus
earnings, the full V4.1 combination, benchmark-aware weighting, and the
defensive full portfolio. Machine-readable price-only results already exist at
`artifacts/research-broad/momentum-v41/ablation-metrics.csv`.

## Evidence limitation

No older price source has been added. Alpaca's available history remains too
short to turn repeated 2020-2026 design changes into independent evidence.
Longer-history validation requires a separate point-in-time price dataset; the
code does not pretend that the current diagnostic is fresh out-of-sample.
