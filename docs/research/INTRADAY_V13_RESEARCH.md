# Intraday V13 research

## Decision

V13 becomes the current intraday research challenger. It preserves V12's
point-in-time liquid-100 universe and SIP control feed, then adds three causal
risk controls:

- a 20-session annualized volatility ceiling of 150%;
- a requirement that at least 45% of eligible stocks are above intraday VWAP;
- a 60-session pairwise-correlation ceiling of 0.85 when choosing multiple
  shorts.

V12 remains registered and reproducible. V13 does not replace it as historical
evidence, and neither version is enabled for paper submission.

## Why these changes

The point-in-time study showed that V12 was profitable on SIP, but its returns
were episodic and concentrated in volatile/crowded names. IEX and SIP also
selected very different trades. V13 therefore changes risk selection rather
than retuning V12's entry time, break threshold, volume rule, or exit time.

All new inputs are causal. Daily volatility and correlations use prices through
the prior session. Breadth is measured at the confirmation bar. Point-in-time
membership is still enforced at every signal.

## Component ablation

Matched period: July 27, 2020 through July 17, 2026. All tests use the
point-in-time liquid-100 universe, SIP bars, $100,000 initial cash, and 8 basis
points of one-way slippage.

| Variant | Return | Sharpe | Max drawdown | Positions |
|---|---:|---:|---:|---:|
| V12 control | 5.79% | 0.737 | -1.84% | 65 |
| Volatility only | 4.23% | 0.749 | -1.58% | 45 |
| Breadth only | 5.18% | 0.719 | -1.60% | 44 |
| Correlation only | 4.95% | 0.722 | -1.51% | 60 |
| Balanced combined draft | 3.76% | 0.827 | -1.03% | 25 |
| Final relaxed V13 | **5.91%** | **0.963** | **-1.03%** | **45** |

The volatility control is the only component that independently improves
Sharpe. Breadth and correlation controls mainly reduce drawdown. Together, less
restrictive thresholds preserve enough observations for the controls to work
as a useful ensemble.

## Threshold neighborhood

| Neighborhood | Volatility cap | Breadth | Correlation cap | Return | Sharpe | Max DD | Positions |
|---|---:|---:|---:|---:|---:|---:|---:|
| Strict | 75% | 55% | 0.65 | 1.13% | 0.838 | -0.11% | 9 |
| Balanced | 100% | 50% | 0.75 | 3.76% | 0.827 | -1.03% | 25 |
| Final relaxed | 150% | 45% | 0.85 | 5.91% | 0.963 | -1.03% | 45 |

All three remain positive and their activity changes monotonically in the
expected direction. The final thresholds were nevertheless chosen after seeing
these results, so V13's performance is in-sample research, not untouched
out-of-sample evidence.

## Feed, history, and cost robustness

| Test | Return | Sharpe | Max drawdown | Positions |
|---|---:|---:|---:|---:|
| Matched SIP, 8 bps | 5.91% | 0.963 | -1.03% | 45 |
| Matched SIP, 16 bps | 5.13% | 0.863 | -1.17% | 45 |
| Matched SIP, 24 bps | 4.37% | 0.756 | -1.32% | 45 |
| Matched IEX, 8 bps | 3.13% | 0.986 | -0.42% | 29 |
| Extended SIP from January 2019 | 6.86% | 0.901 | -1.03% | 60 |

The IEX result is particularly useful: V13 improves PIT/IEX V12's 2.40% return,
0.65 Sharpe, and -0.94% drawdown. V13 is still feed-dependent in which trades
it chooses, but the improvement is not exclusive to SIP.

## Concentration and calendar behavior

On matched SIP, V13 has a 66.7% position win rate versus V12's 60.0%. The best
three positions contribute 39.6% of net P&L, approximately unchanged from
V12's 40.2%. The best three symbols contribute 47.6%, down from 57.9%.

V13 removes 20 V12 positions and introduces no new ones. The removed positions
have slightly negative aggregate P&L. The model is therefore a causal risk
filter over the existing signal rather than a new return source.

| Year | Positions | Position P&L |
|---|---:|---:|
| 2020 partial | 1 | $14 |
| 2021 | 13 | $3,647 |
| 2022 | 9 | $1,690 |
| 2023 | 1 | -$188 |
| 2024 | 7 | -$130 |
| 2025 | 7 | -$149 |
| 2026 partial | 7 | $1,024 |

The main unresolved weakness is temporal concentration. Most profit still comes
from 2021, 2022, and 2026; 2023 through 2025 remain slightly negative.

## Statistical diagnostic

A deterministic 100,000-sample session bootstrap over V13's 36 matched-period
trade sessions gives an approximate 95% return interval of 1.34% to 10.56%,
with 99.5% positive draws. This looks stronger than V12's interval, which
included zero. It does not correct for selecting V13's rules and thresholds
after examining this same history, and must not be interpreted as a discovery
p-value.

## Operational status

The research configuration is `configs/research-intra-v13-pit-sip.toml`. It
loads the most recent published point-in-time universe snapshot, explicitly
uses SIP, and keeps paper execution disabled. The production paper path still
rejects negative weights, and this research does not model point-in-time
shortability, borrow fees, locates, recalls, spread variation, or partial fills.

Reproduce the study with:

```powershell
.venv\Scripts\python.exe -u scripts\intraday_v13_research.py --stage all
```

The paper executor now supports opt-in signed targets with account shorting,
equity, shortability, and easy-to-borrow gates. Current paper credentials do
not include recent SIP entitlement, so `configs/paper-intra-v13.toml` uses IEX;
the SIP configuration remains research-only. V13 is ready for locked paper
observation and should not be retuned again on the 2019-2026 sample.
