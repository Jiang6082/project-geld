# Project Geld Research Report

Date: July 17, 2026

## Conclusion

The backtesting system is working, but the first round does not establish a
deployable stock-picking alpha.

The original megacap universe produced strong momentum and trend results, but
the advantage largely disappeared when NVDA and META were removed. That is a
clear concentration and selection-bias warning.

The most defensible lead is a lower-beta, 12-month sector-ETF momentum
strategy. It remained positive in both the training and validation periods,
survived higher assumed slippage, and improved when rebalanced every ten
sessions. It still underperformed buy-and-hold SPY in absolute return.

Status:

- Research engine: operational.
- Paper execution plumbing: operational.
- Confirmed alpha: no.
- Candidate for further walk-forward research: sector-ETF momentum.
- Candidate ready for submitted paper trading: not yet.

## Data and assumptions

- Source: Alpaca US-equity daily bars.
- Feed: IEX.
- Corporate-action adjustment: all.
- Period: January 4, 2021 through July 16, 2026.
- Sessions: 1,389 per symbol.
- Missing opens/closes: zero.
- Execution: signal at close, fill at the next available session open.
- Portfolio: long-only, fractional shares allowed.
- Default rebalance cadence: five sessions.
- Default slippage: 5 bps for the initial universe and 10 bps for the
  conservative sector tests.
- Commissions: zero.
- Benchmark: SPY.
- Split:
  - training: January 4, 2021 through November 12, 2024;
  - validation: November 13, 2024 through July 16, 2026.

The validation period was used to rank grid candidates, so it is not an
untouched final holdout. A rolling walk-forward process and a future unseen
period are still required.

## Experiment coverage

Each universe evaluated:

- 36 momentum configurations;
- 32 trend configurations;
- 36 mean-reversion configurations.

Candidates were ranked by robust score: the lower of training Sharpe and
validation Sharpe.

## Original nine-symbol universe

Universe:

SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, META, and GOOGL.

The following full-period figures use 10 bps of slippage.

| Strategy | Selected parameters | CAGR | Sharpe | Max drawdown | Total excess return vs SPY | Annual turnover |
|---|---|---:|---:|---:|---:|---:|
| Momentum | 63-day momentum, 60-day volatility, top 2, 90% gross | 20.08% | 1.16 | -31.16% | +55.68% | 16.58x |
| Trend | 20/100 trend, 126-day momentum, top 3, 90% gross | 20.49% | 1.14 | -25.24% | +60.98% | 13.78x |
| Mean reversion | 2-day reversal, 50-day regime, top 3, 75% gross | 7.43% | 0.69 | -21.13% | -70.62% | 31.62x |

The headline momentum and trend results are not sufficiently robust:

| Strategy | Full-universe CAGR | Ex-NVDA CAGR | Ex-NVDA-and-META CAGR |
|---|---:|---:|---:|
| Momentum | 20.08% | 10.18% | 7.42% |
| Trend | 20.49% | 12.54% | 7.86% |
| Mean reversion | 7.43% | 2.61% | 1.67% |

After removing NVDA, both momentum and trend underperformed SPY over the full
period. NVDA was also the most frequently selected stock in both strategies.
The apparent alpha is therefore highly dependent on hindsight exposure to a
small number of present-day winners.

## Sector-ETF universe

The second universe contains SPY and the eleven major US sector ETFs:

XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, and XLY.

This reduces single-company survivorship bias and tests whether the ranking
effect generalizes across broad equity groups.

### Selected candidates

| Strategy | Best robust Sharpe | Validation Sharpe | Validation return | Validation drawdown |
|---|---:|---:|---:|---:|
| Momentum | 0.80 | 1.01 | 15.43% | -8.72% |
| Mean reversion | 0.17 | 0.60 | 8.46% | -9.26% |
| Trend | 0.13 | 0.15 | 1.52% | -12.79% |

The selected momentum configuration uses:

- 252-session momentum;
- 20-session volatility;
- top two sectors;
- 60% gross exposure.

At 10 bps of slippage and a five-session rebalance cadence:

- CAGR: 7.77%;
- Sharpe: 0.87;
- maximum drawdown: -10.81%;
- beta to SPY: 0.37;
- estimated annual beta-adjusted alpha: +2.14%;
- annual turnover: 11.39x.

SPY returned 119.24% over the same period, approximately 15.26% annualized.
The candidate is therefore a lower-volatility allocation strategy, not an
absolute-return replacement for SPY.

### Slippage stress

| Slippage | CAGR | Sharpe | Max drawdown |
|---|---:|---:|---:|
| 10 bps | 7.77% | 0.87 | -10.81% |
| 20 bps | 6.60% | 0.75 | -10.94% |
| 30 bps | 5.45% | 0.63 | -11.06% |

The result remains positive under higher costs, although its edge weakens
substantially.

### Rebalance-cadence stress

All rows use 10 bps of slippage.

| Rebalance cadence | CAGR | Sharpe | Max drawdown | Annual alpha | Annual turnover |
|---|---:|---:|---:|---:|---:|
| 5 sessions | 7.77% | 0.87 | -10.81% | 2.14% | 11.39x |
| 10 sessions | 8.20% | 0.91 | -10.80% | 2.48% | 7.35x |
| 21 sessions | 6.35% | 0.71 | -10.26% | 0.69% | 5.39x |
| 42 sessions | 6.11% | 0.68 | -9.37% | 0.19% | 3.10x |

Ten sessions is the best tested cadence, but this comparison used the full
sample and must be confirmed in rolling walk-forward evaluation.

## Important limitations

1. The initial stock universe was selected with present-day knowledge and is
   subject to survivorship and winner-selection bias.
2. A single training/validation split is not enough to distinguish a stable
   effect from regime luck.
3. The validation set was used for model selection and is therefore no longer
   pristine.
4. Alpaca IEX bars are not the same as consolidated SIP data.
5. The simulator includes slippage but not every regulatory fee, tax,
   market-impact effect, or cash yield.
6. The current alpha estimate is a simple beta adjustment, not a full
   multi-factor attribution.
7. Turnover remains high, even for the sector candidate.
8. The system has not yet tested point-in-time stock universes or delisted
   securities.

## Recommended next iteration

1. Implement rolling walk-forward optimization with several non-overlapping
   validation windows.
2. Lock the 252-day sector momentum specification before observing future
   results.
3. Use the ten-session cadence as a hypothesis, not a final selection.
4. Add cash yield and a factor-attribution report.
5. Separate the benchmark series from the tradable universe in the engine.
6. Expand to a stable, predeclared ETF universe and test different market
   regimes.
7. Run the candidate in shadow mode: generate and archive dry paper plans
   without submitting orders.
8. Require several months of stable shadow/paper behavior before considering
   any separate live-money system.

## Reproducing the results

Initial grids:

    geld --config config.example.toml experiment --source alpaca ...

Sector universe configuration:

    configs/sector-etfs.toml

Cost stress:

    python scripts/research_stress.py \
      --config configs/sector-etfs.toml \
      --research-dir artifacts/research-sector-etfs \
      --slippage-bps 10,20,30

Cadence stress:

    python scripts/cadence_stress.py \
      --config configs/sector-etfs.toml \
      --research-dir artifacts/research-sector-etfs \
      --strategy momentum \
      --cadences 5,10,21,42 \
      --slippage-bps 10

Primary machine-readable outputs:

- artifacts/research/momentum-grid.csv
- artifacts/research/trend-grid.csv
- artifacts/research/mean-reversion-grid.csv
- artifacts/research/cost-stress.csv
- artifacts/research/universe-stress.csv
- artifacts/research-sector-etfs/momentum-grid.csv
- artifacts/research-sector-etfs/trend-grid.csv
- artifacts/research-sector-etfs/mean-reversion-grid.csv
- artifacts/research-sector-etfs/cost-stress.csv
- artifacts/research-sector-etfs/momentum-cadence-stress.csv
