# Momentum V4: core plus alpha

## Decision

Momentum V4 fixes the main V3 portfolio-construction failure and is the first
broad-universe challenger worth advancing to **forward shadow observation**.
It is not proof of durable alpha and paper submission remains disabled.

The version selected using 2017-2019 was:

- 75% persistent SPY core;
- 25% active residual-momentum sleeve;
- up to 40 stocks;
- 2% maximum target weight per active stock;
- 21-session rebalance;
- 0.25-percentage-point target-weight no-trade band; and
- 10-basis-point backtest slippage.

## Why this is different

V3 tried to use the same model to select stocks and decide whether most of the
portfolio should be invested. It correctly reduced exposure in 2022 but missed
large parts of the 2020, 2021, and 2023 advances.

V4 separates those jobs. SPY supplies stable market participation. The active
sleeve attempts to add value by selecting liquid stocks with market-residual
momentum, trend confirmation, lower downside volatility, and correlation-aware
diversification.

## Protocol

- Broad monthly point-in-time top-500 liquid universe.
- 1,092 historically eligible stocks.
- Alpaca SIP daily adjusted bars, 2016-01-04 through 2026-07-16.
- Signals at the close and execution at the next session's open.
- Training selection: 2017-01-01 through 2019-12-31.
- Later diagnostic: 2020-01-01 through 2026-07-16.
- Three variants were declared before the V4 run: 75/25, 60/40, and 60/40
  with SPY, IWM-minus-SPY, and IWD-minus-SPY residual factor proxies.

The later period is not fresh out-of-sample evidence because prior V2 and V3
work had already inspected it. The result is useful for rejecting broken
designs and selecting a forward-shadow candidate, not for claiming alpha.

## Results

Training selected the 75% core / 25% active version.

| Portfolio | CAGR | Sharpe | Max drawdown | Beta | Annual alpha | Turnover |
|---|---:|---:|---:|---:|---:|---:|
| V4 75/25 | 15.92% | 0.854 | -33.70% | 0.969 | 0.85% | 1.71x |
| V4 60/40 | 15.91% | 0.852 | -33.37% | 0.945 | 1.24% | 2.81x |
| V4 60/40 multifactor | 15.18% | 0.832 | -32.77% | 0.933 | 0.73% | 2.67x |
| SPY buy-and-hold | 15.30% | 0.816 | -33.79% | 1.000 | -0.14% | 0.00x |

The selected V4 gained about 0.62 percentage points of CAGR and improved
Sharpe by 0.038 relative to SPY in this diagnostic. The active sleeve did not
materially reduce crash drawdown because the portfolio deliberately retains a
large SPY core.

The multifactor proxy did not improve the result. It remains implemented for
future controlled research but is not in the selected configuration.

## Execution-cost stress

| Slippage per trade | CAGR | Sharpe | Max drawdown |
|---:|---:|---:|---:|
| 0 bps | 16.12% | 0.863 | -33.68% |
| 10 bps | 15.92% | 0.854 | -33.70% |
| 25 bps | 15.61% | 0.841 | -33.71% |
| 50 bps | 15.10% | 0.818 | -33.74% |

The edge survives 25-basis-point slippage in this sample but is essentially
gone by 50 basis points. This makes low turnover and liquid execution central
to the thesis.

## Next gate

Use the selected 75/25 configuration as a shadow portfolio with no order
submission. Record its daily hypothetical holdings and compare it with SPY and
V2 for genuinely unseen data. Do not tune the rule from the 2020-2026 result.

Paper submission should be considered only after the shadow record shows that:

1. active-sleeve excess return remains positive after observed spread/slippage;
2. realized turnover stays near the backtest range;
3. orders remain practical in the selected stocks; and
4. the advantage is not concentrated in one stock, sector, or short interval.

## Alpaca dry cycle

The paper configuration now reads a file-backed snapshot of the same broad
liquid universe used by research. The current snapshot contains 500 symbols as
of 2026-07-15. A 45-day stale-universe guard stops paper planning if that file
is not refreshed.

The selected configuration completed an Alpaca-connected broad-universe dry
cycle on 2026-07-17. It generated 41 planned orders: SPY plus 40 active stocks.
A 1% cash buffer scaled planned gross exposure to 99%, consisting of 74.25%
SPY and 24.75% active exposure. The largest active order was 1.16% of account
equity. Planned notional was USD 989,999.99 on the USD 1 million paper account.
No orders were submitted.

Risk controls now distinguish the core from the sleeve. Active stocks are
capped at 2% position weight and 2% of account equity per order; SPY alone has
a 75% position and order override. This prevents the former USD 100 pilot cap
from blocking portfolio formation without weakening individual-stock limits.

Recent SIP data was not available under the paper account's subscription, so
paper signal generation uses IEX daily bars while the historical research used
SIP. This feed difference must be monitored during forward observation.

## Reproduce

    .venv\Scripts\python.exe scripts\broad_v4_research.py
    .venv\Scripts\python.exe scripts\broad_v4_cost_stress.py

Machine-readable outputs are under `artifacts/research-broad/momentum-v4/`.
