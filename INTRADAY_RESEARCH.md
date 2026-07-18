# Intraday research status

## Data and protocol

- IEX one-minute Alpaca bars, aggregated to completed 15-minute bars.
- Liquid 21-symbol research basket plus SPY as non-tradable context.
- April 1 through July 17, 2026.
- Training diagnostic: April 1 through May 31.
- Later test diagnostic: June 1 through July 17.
- Signals are observed at a bar close and executed no earlier than the next bar.
- Positions are forced flat each session.
- A 0.5%-of-equity no-trade band prevents exact-weight micro-rebalancing.

This is a short, already-inspected period. It is useful for engineering and
candidate rejection, not as proof of durable alpha.

Longer July 2020–July 2026 results now reject both Intra V2 and Intra V3. See
`LONG_INTRADAY_RESEARCH.md`; those results supersede the short-period positives.

## Rejected first candidate

The first candidate repeatedly ranked short-horizon relative winners. At an
eight-basis-point slippage assumption, it lost 18.1% over the full period while
SPY gained approximately 14.0%. Its turnover was excessive. It was rejected
for paper submission.

## Intra V1 research candidate

The less-bad candidate buys up to six liquid stocks that lag SPY over the prior
four 15-minute bars while the broad market is above its session VWAP. It uses
stock/VWAP confirmation, a rank buffer, normally selects only once per session,
and exits by 15:45 New York time.

| Assumed one-way slippage | Full return | Full Sharpe | Later-test return | Later-test Sharpe |
|---:|---:|---:|---:|---:|
| 0 bps | 5.02% | 1.84 | 0.93% | 0.70 |
| 2 bps | 3.24% | 1.21 | 0.20% | 0.19 |
| 4 bps | 1.48% | 0.58 | -0.53% | -0.32 |
| 8 bps | -1.94% | -0.67 | -1.97% | -1.34 |

The candidate is too cost-sensitive to claim alpha or to use unrestricted
market orders. The intraday paper config therefore remains disabled and uses a
two-basis-point marketable-limit ceiling when submission is eventually enabled.
Missed fills and realized implementation shortfall are primary paper outcomes,
not inconveniences to bypass.

## Allocation decision

The intraday account has a configurable 70% maximum gross exposure, but this is
a ceiling rather than an approved deployment target. Start with dry planning,
then shadow paper. Increase actual paper utilization only if fill-adjusted
results remain positive and the later paper sample improves on cash after costs.

## Swing allocation diagnostic

Increasing Daily V4 from 25% to 60% active and reducing its SPY core from 75% to 40%
produced the following already-inspected 2020–July 15, 2026 diagnostic:

| Metric | 40% SPY / 60% Daily V4 active | Prior 75% SPY / 25% active | SPY |
|---|---:|---:|---:|
| CAGR | 16.21% | 15.99% | 15.40% |
| Sharpe | 0.848 | 0.857 | 0.821 |
| Maximum drawdown | -33.32% | -33.70% | -33.79% |
| Annual alpha | 1.95% | 0.88% | — |
| Annual turnover | 4.23x | 1.71x | Low |

The 40/60 version increased historical return and estimated alpha, but slightly
reduced Sharpe and more than doubled turnover. In the 2017–2019 training period,
its estimated annual alpha was slightly negative (-0.33%). It is therefore a
deliberately higher-risk paper challenger, not a replacement proven superior to
the 75/25 control.
