# Locked V8 forward-shadow study

## Purpose

This study collects genuinely new evidence for the locked broad-universe Intra
V8 hypothesis. It never submits an Alpaca order and never changes paper-account
positions. Each cycle uses only completed bars, delays a signal until the next
completed-bar cycle, and records whether a hypothetical marketable limit would
have crossed the observed quote.

The locked inputs are:

- strategy: `intra_v8`;
- dated universe: `universes/intraday-liquid-100-20260715.csv`;
- 15-minute bars and 10:30 signal observation;
- 1% SPY-relative dislocation;
- one-bar breakdown confirmation;
- prior-close/below-20-session-trend filter;
- four names, 10% each, 40% maximum gross;
- two-basis-point marketable-limit offset;
- USD 100,000 shadow capital.

Changing any of these starts a different study and must use a new output folder.

## Cycle

```powershell
geld --config configs/research-intra-v8-broad.toml intraday-shadow-once `
  --output artifacts/forward-shadow-v8
```

`state.json` contains pending targets and hypothetical open positions.
`events.csv` is append-only and records entries, exits, missed limits, blocked
shorts, bid, ask, spread, shortability, ETB status, fill price, and realized
hypothetical P&L. Repeating a cycle for the same completed bar is idempotent.

The market adapter is read-only and has no submit method. The normal paper
planner separately rejects negative target weights.

## Interpretation

The Assets API is checked at every relevant cycle because borrow availability
can change. The installed Alpaca SDK exposes `easy_to_borrow`; Alpaca has
announced its replacement by `borrow_status`, so this collector also writes a
normalized `borrow_status` value for migration. A non-ETB symbol is recorded as
blocked rather than assumed fillable.

Alpaca's latest-quote endpoint provides best bid and ask. A hypothetical short
entry fills only when its sell limit is at or below the observed bid. A cover
fills only when its buy limit is at or above the observed ask. This still does
not model latency, queue priority, market impact, or available quote size.

Do not assess the strategy until there are at least 100 completed positions and
at least three months of forward observations. Advancement requires positive
net return after observed spread costs, positive results outside a single month,
no dominant symbol, and materially better evidence than cash. These rules are
fixed before the sample begins.

Official Alpaca references:

- https://docs.alpaca.markets/us/reference/stocklatestquotes-1
- https://docs.alpaca.markets/us/docs/margin-and-short-selling
- https://docs.alpaca.markets/us/docs/paper-trading
