# Intraday V12 robustness study

> Superseded evidence note: the later point-in-time IEX/SIP study in
> `INTRADAY_V12_PIT_VALIDATION.md` shows that the fixed-current-universe IEX
> result is feed-dependent. Use the PIT/SIP result as the current research
> control.

## Purpose

V12 improved the broad historical result to 2.61% with a 1.033 Sharpe, but it
formed only 19 completed positions. This study tries to falsify that result
without selecting a new best parameter from the same history. Every run uses
the same fixed July 2026 liquidity universe, native 15-minute IEX bars, and
eight basis points of modeled one-way slippage.

## Parameter and execution neighborhood

| Variant | Total return | Sharpe | Max drawdown | Positions |
|---|---:|---:|---:|---:|
| Baseline: 1.50x volume, 0.25% break | 2.61% | 1.033 | -0.21% | 19 |
| Volume cap 1.25x | 2.44% | 1.066 | -0.08% | 17 |
| Volume cap 2.00x | 2.46% | 0.835 | -0.55% | 24 |
| Confirmation break 0.10% | 1.92% | 0.672 | -0.43% | 25 |
| Confirmation break 0.50% | 1.40% | 0.822 | -0.21% | 11 |
| Entry delayed another 15 minutes | 1.69% | 0.756 | -0.20% | 19 |
| Exit at 15:00 instead of 15:45 | 2.37% | 1.115 | -0.10% | 19 |

Every neighboring specification remains positive. The result therefore does
not collapse at the exact 1.50x volume or 0.25% break thresholds. Delayed entry
reduces the result but does not erase it, and an earlier exit remains positive.
The baseline parameters remain unchanged; the strongest neighboring result was
not selected as a replacement.

## Concentration and universe checks

The 19 positions span 17 sessions and 17 symbols. The best trade contributes
25.8% of net P&L and the best three contribute 58.2%. Removing any single trade
leaves approximate portfolio return between 1.94% and 2.82%.

An IID bootstrap of the 19 position returns gives a 95% interval of 0.43% to
2.37% for mean position return and a 99.9% frequency of a positive resampled
mean. This is descriptive only: it assumes independent, representative trades
and does not correct for strategy selection, universe bias, or repeated
hypothesis testing.

| Universe check | Stocks | Total return | Sharpe | Positions |
|---|---:|---:|---:|---:|
| Alternating liquidity half A | 50 | 1.12% | 0.600 | 5 |
| Alternating liquidity half B | 50 | 1.47% | 0.811 | 14 |
| Original small universe | 21 | 0.30% | 0.410 | 1 |
| History present at common July 2020 start | 85 | 1.11% | 0.671 | 14 |

Both non-overlapping halves are positive, although half A and the original
universe are too sparse for independent confirmation. Requiring history at the
cache's common 2020 start removes several newer-listing winners and remains
positive.

## Data-integrity finding

The cache does not provide a uniform 2019 start: 89 symbols begin in July 2020,
and only MPWR appears in 2019. Some symbol histories also require corporate-
action and ticker-reuse auditing. For example, NBIS appears before the current
company's public history. Adjusted historical data may represent a predecessor,
SPAC, or ticker mapping rather than the security intended by the current
universe row.

## Extended timing and concentration tests

| Exact backtest | Total return | Sharpe | Max drawdown | Positions |
|---|---:|---:|---:|---:|
| Signal at 10:15 | 0.90% | 0.292 | -1.29% | 37 |
| Baseline signal at 10:30 | 2.61% | 1.033 | -0.21% | 19 |
| Signal at 10:45 | -0.29% | -0.107 | -1.37% | 22 |
| Signal at 11:00 | 1.12% | 0.283 | -1.34% | 17 |
| Exit at 12:00 | 1.06% | 0.659 | -0.23% | 19 |
| Exit at 13:30 | 2.68% | 1.111 | -0.12% | 19 |
| Exit at 15:00 | 2.37% | 1.115 | -0.10% | 19 |
| Exclude IREN, AAOI, and RKLB | 0.91% | 0.578 | -0.21% | 15 |

Holding-period evidence is reasonably stable: profit is present by noon and is
retained through multiple afternoon exits. The signal-time evidence is weaker.
The 10:15 and 11:00 variants remain positive but have much worse drawdown and
Sharpe, while 10:45 is negative. The strong baseline result is therefore
localized around 10:30 rather than invariant across the morning. Removing the
three largest contributor symbols leaves the strategy positive, so those names
do not solely create the result.

## Statistical and execution simulations

A 100,000-sample session-block bootstrap produces a 95% portfolio-return range
of approximately 1.03% to 4.26%, with 99.96% positive resamples. V12's mean
selected-position return exceeds 98.7% of random 19-position subsets drawn
without replacement from V8's 94 completed positions; the descriptive one-sided
randomization probability is 1.26%.

A conservative simulation independently applies 80% short availability, 85%
fill probability, 8–40 basis points of one-way slippage, and 5–50% annualized
borrow rates. It produces a median 1.28% return, a 5th percentile of 0.40%, and
99.3% positive trials. A severe scenario with 60% availability, 70% fills,
8–75 basis points of slippage, and 20–100% borrow rates produces a median 0.40%,
a negative 5th percentile of -0.39%, and 78.6% positive trials.

These simulations condition on the researched V12 trade sample. They measure
trade concentration and execution sensitivity, not data-mining-adjusted
statistical significance. The bootstrap and randomization probabilities must
not be read as proof of out-of-sample alpha.

## Verdict

V12 passes parameter, holding-period, concentration, execution, and
split-universe stress. It is more robust than V8 and remains the current
intraday research leader. Signal-time sensitivity is a new material weakness:
the strategy is much stronger at 10:30 than at nearby observation times. It
still does not establish true alpha: 19 baseline positions are too few, the
universe is selected with 2026 knowledge, histories are not fully point-in-time,
and V12 was developed after examining this sample. Paper execution remains
disabled until short-order controls and symbol-history validation are added.
