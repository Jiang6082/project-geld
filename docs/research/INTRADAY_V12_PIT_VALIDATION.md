# Intraday V12 point-in-time and independent-feed validation

> Follow-up: `INTRADAY_V13_RESEARCH.md` adds causal risk controls to this
> PIT/SIP control and is the current intraday research challenger.

## Bottom line

V12's original fixed-July-2026 IEX result is not robust by itself. On identical
dates, the fixed basket falls from a 2.61% return and 1.03 Sharpe on IEX to a
0.11% return and 0.03 Sharpe on SIP. The point-in-time liquid universe is more
credible: it returns 2.40% on IEX and 5.79% on SIP, with Sharpes of 0.65 and
0.74 respectively. The extended January 2019 through July 2026 SIP run returns
6.99% with a 0.74 Sharpe.

This is evidence that the short-continuation concept merits further research,
but not proof of alpha. The strongest point-in-time SIP run has only 65
positions on the matched period, its 95% session-bootstrap interval includes
zero, and results are episodic. V12 remains research-only and paper submission
remains disabled.

## Point-in-time universe reconstruction

The pipeline requests both active and inactive US-equity records from Alpaca,
then removes obvious funds, notes, warrants, rights, units, preferred shares,
depositary receipts, OTC securities, and security-suffix symbols. It found:

- 33,101 unique active/inactive asset-master symbol records;
- 6,329 common-stock candidates on NASDAQ, NYSE, and AMEX;
- 914 currently inactive candidates;
- 89 monthly snapshots from March 2019 through July 2026;
- exactly 100 stocks per snapshot and 324 distinct selected stocks.

At every month-end the selection uses only information available through that
close:

- at least 60 observed daily sessions;
- closing price of at least $5;
- trailing 20-session median dollar volume of at least $10 million;
- top 100 remaining stocks by that trailing median dollar volume.

The selection becomes tradable on the next market session. A stock removed at
the next month-end remains eligible through that month-end and is removed on
the following session. This prevents a month-end observation from affecting an
earlier intraday signal. The median month replaces 12 names. Thirteen selected
stocks are now inactive: AABA, AGN, CELG, DATA, DISCK, DWDP, LVGO, RTN, TIF,
WCG, WORK, WP, and XLNX.

The published membership artifacts are:

- `universes/pit-liquid-100-monthly-2019-2026.csv.gz`
- `universes/pit-liquid-100-membership-2019-2026.json`

This is a rules-based point-in-time reconstruction, not an official historical
index. It is materially better than using today's winners throughout history,
but Alpaca's current active/inactive asset master is not a CRSP-grade historical
security master. Symbol reuse, incomplete delisted coverage, and Alpaca's
default historical ticker-rename mapping remain limitations.

## Independent-feed design

The exact same adjusted 15-minute requests were downloaded from IEX and SIP.
Alpaca documents IEX as one exchange and SIP as the consolidated tape to which
all US exchanges report activity. The historical endpoint's `feed` parameter
selects between them. See Alpaca's
[Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq).

This is an independent underlying market feed, but not an independent vendor:
both datasets are served and bar-aggregated by Alpaca. A later audit should
repeat the PIT/SIP result with a second institutional vendor.

The cache contains 325 symbols and assembled:

- 6,750,766 IEX bars;
- 8,604,201 SIP bars.

IEX has a discontinuity before July 27, 2020, while SIP begins January 2, 2019.
The primary four-way comparison therefore uses the common uninterrupted period
from July 27, 2020 through July 17, 2026. A separate extended SIP run preserves
the earlier history.

Across the common period, all IEX bars have matching SIP bars and SIP contains
156,501 additional bars. For matching bars, median absolute close disagreement
is 2.22 basis points, the 95th percentile is 18.07 basis points, and median IEX
volume is only 3.66% of SIP volume. Only 9 of 49 unique fixed-universe
session/symbol positions and 17 of 85 PIT positions overlap between feeds
(Jaccard similarities of 18.4% and 20.0%). Feed choice materially changes the
signals.

## Matched-period results

All runs use V12's locked parameters, $100,000 initial cash, 8 basis points of
one-way slippage, no commission, 10% maximum position size, and 40% maximum
gross exposure.

| Universe / feed | Return | CAGR | Sharpe | Max drawdown | Positions | Trade sessions |
|---|---:|---:|---:|---:|---:|---:|
| Fixed July-2026 / IEX | 2.61% | 0.43% | 1.034 | -0.21% | 19 | 17 |
| Point-in-time liquid 100 / IEX | 2.40% | 0.40% | 0.645 | -0.94% | 37 | 33 |
| Fixed July-2026 / SIP | 0.11% | 0.02% | 0.025 | -2.57% | 39 | 37 |
| Point-in-time liquid 100 / SIP | 5.79% | 0.95% | 0.737 | -1.84% | 65 | 50 |

The strategy is a small, intermittently deployed intraday short sleeve, so SPY
is not a matched-exposure benchmark. SPY returned about 151% over the common
period, while V12 was in cash almost all the time. V12 does not replace buy-and-
hold SPY; the relevant question is whether its sparse returns survive costs and
add diversifying alpha. Its estimated beta is near zero, but the sample is too
small for a confident alpha claim.

## Extended history, concentration, and calendar behavior

The native-history PIT/SIP run from January 2019 through July 2026 returns
6.99%, has a 0.74 Sharpe and -1.84% maximum drawdown, and forms 82 positions on
63 sessions across 61 symbols.

On the matched-period PIT/SIP run:

- 60.0% of positions are profitable;
- the best position contributes 14.8% of net P&L;
- the best three positions contribute 40.2%;
- the best three symbols contribute 57.9%;
- removing RIOT and MARA still leaves an approximate 3.28% return.

The result is episodic rather than persistent:

| Year | Positions | Position P&L | Strategy return |
|---|---:|---:|---:|
| 2020 partial | 1 | $14 | 0.01% |
| 2021 | 20 | $3,395 | 3.39% |
| 2022 | 11 | $1,530 | 1.48% |
| 2023 | 1 | -$187 | -0.18% |
| 2024 | 15 | -$141 | -0.13% |
| 2025 | 7 | -$149 | -0.14% |
| 2026 partial | 10 | $1,332 | 1.27% |

## Execution-cost stress

| One-way slippage | Return | Sharpe | Max drawdown |
|---:|---:|---:|---:|
| 8 bps | 5.79% | 0.737 | -1.84% |
| 16 bps | 4.68% | 0.611 | -2.12% |
| 24 bps | 3.58% | 0.479 | -2.40% |

The return remains positive at three times the baseline cost assumption. This
does not model short locate availability, borrow fees, hard-to-borrow recalls,
spread variation, market impact, rejected orders, or partial fills. Those are
especially important because several contributors are volatile or crowded
shorts.

## Statistical diagnostic

A deterministic 100,000-sample bootstrap resamples the 50 PIT/SIP trade
sessions as blocks. Its median approximate portfolio return is 5.74%, its 95%
interval is -0.26% to 12.00%, and 96.96% of draws are positive. The interval
includes zero. The bootstrap also assumes the observed sessions are independent
and representative and does not correct for the many strategy versions and
tests that produced V12. It must not be read as a formal discovery p-value.

## Reproduction

The download is resumable and `.env` is never written to an artifact:

```powershell
.venv\Scripts\python.exe -u scripts\intraday_v12_pit_validation.py --stage universe
.venv\Scripts\python.exe -u scripts\intraday_v12_pit_validation.py --stage data
.venv\Scripts\python.exe -u scripts\intraday_v12_pit_validation.py --stage validate
```

Large raw caches and detailed results remain ignored under `data/` and
`artifacts/`. The causal membership implementation, reconstruction script,
tests, and compact universe artifacts are versioned.

## Research decision

Reject the fixed-current-basket IEX result as the main evidence. Promote the
PIT/SIP specification to the research control, without retuning V12 on these
results. Before paper orders, add point-in-time shortability/borrow controls and
validate the locked PIT/SIP strategy on a genuinely vendor-independent feed or
future untouched paper observations. Because Project Geld's paper path still
rejects negative weights, these backtests do not make V12 paper-ready.
