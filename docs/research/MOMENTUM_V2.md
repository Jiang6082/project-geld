# Momentum V2

Momentum V2 is Project Geld's first forward paper candidate. It is configured
in configs/equity-momentum-v2.toml.

## Daily behavior

The strategy can be observed every day without trading every day:

1. paper-status records current Alpaca paper equity, cash, daily return,
   cumulative return since tracking began, and managed positions.
2. paper-once refreshes market data and previews the current target basket.
3. An actual paper-once --submit invocation can submit only when:
   - paper mode is enabled;
   - PROJECT_GELD_CONFIRM_PAPER is YES;
   - the US equity market is open; and
   - ten trading sessions have elapsed since the prior submitted rebalance.

The rebalance state is persisted in:

    artifacts/paper-v2/rebalance_state.json

Dry previews do not advance that state.

## Selection rules

- Universe: 34 predeclared liquid US stocks across eight sectors.
- Signal: return from 252 sessions ago through 21 sessions ago.
- Trend gate:
  - current close above the 200-session moving average;
  - 50-session moving average above the 200-session moving average.
- Ranking: 12-1 momentum divided by 60-session realized volatility.
- Portfolio:
  - at most five stocks;
  - at most two stocks per sector;
  - inverse-volatility weights;
  - 75% target gross exposure;
  - 20% maximum weight per stock.
- Turnover buffer:
  - enter from the top five;
  - retain an existing holding until it falls below rank ten or fails the
    trend gate.
- Rebalance: every ten trading sessions.

## Pilot order limits

The paper configuration caps each order at USD 100. This means the first paper
portfolio will be much smaller than the theoretical target weights. The cap is
intentional: the first phase tests data, scheduling, reconciliation, order
submission, fills, and performance logging rather than portfolio economics.

## Backtest evidence

Alpaca IEX adjusted daily bars, January 4, 2021 through July 16, 2026:

| Cadence | CAGR | Sharpe | Max drawdown | Annual turnover |
|---|---:|---:|---:|---:|
| 5 sessions | 13.43% | 1.06 | -20.17% | 5.00x |
| 10 sessions | 13.88% | 1.12 | -18.03% | 3.97x |
| 21 sessions | 15.17% | 1.14 | -20.42% | 2.81x |

The ten-session cadence is used for paper testing as a compromise between
feedback speed, turnover, and drawdown.

For the ten-session version:

- training Sharpe: 1.14;
- validation Sharpe: 1.09;
- validation return: 26.20%;
- validation maximum drawdown: -15.44%.

These results are exploratory. The stock universe was selected with current
knowledge, so the historical test retains survivorship and selection bias.
Forward paper tracking is intended to start producing evidence that does not
have that particular form of look-ahead.

## Commands

Record daily paper-account performance:

    geld --config configs/equity-momentum-v2.toml paper-status \
      --output artifacts/paper-v2

Preview the current basket without submitting:

    geld --config configs/equity-momentum-v2.toml paper-once \
      --output artifacts/paper-v2

Submit only when the preview is acceptable and the market is open:

    geld --config configs/equity-momentum-v2.toml paper-once \
      --submit \
      --output artifacts/paper-v2

Performance history is stored in:

    artifacts/paper-v2/performance.csv
