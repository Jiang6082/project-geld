# Running the paper accounts unattended

The paper runners no longer need a PC that is switched on and logged in at
09:25 ET. The recommended split is:

| Account | Where | Why |
|---|---|---|
| Daily V4 | GitHub Actions (free) | One cycle a day; a few minutes of scheduler delay does not matter |
| Intra V15 | Small Linux server (systemd) | Needs a cycle every 15 minutes at the right time; Actions can start 5-15 minutes late |

Both use the same cross-platform runners, which replace the PowerShell loops:

- `geld run-daily`: refreshes the live universe (monthly), waits for the open
  and runs one Daily V4 cycle; the 21-session cadence state decides whether it
  trades. It retries failed cycles; order IDs are deterministic, so a retry
  never duplicates orders.
- `geld run-intraday`: the Intra V15 session loop, one cycle per completed
  15-minute bar until 15:50 ET. On early-close days (for example the day after
  Thanksgiving) it flattens every position 15 minutes before the close,
  because the strategy's own 15:30/15:45 exits would come after the market has
  closed. The flatten checks the account is actually flat and is retried (up
  to five times) if not; the runner never acts after the close.
- `geld run-close-check`: the read-only Daily V4 close reconciliation, once the
  20-minute-delayed close bar exists.
- `geld refresh-universe`: the monthly universe refresh on its own.

Every runner checks Alpaca's market calendar and exits on holidays, holds a
process lock so overlapping launches exit quietly, and writes
`runner-heartbeat.json` with its current phase. Add `--log-json <file>` (or set
`GELD_LOG_JSON`) for structured logs.

Safety is unchanged: `paper=True` is hard-coded, and submission still needs
`[paper] enabled = true`, the `--submit` flag and the confirmation variable.

## Daily V4 universe refresh

Daily V4's 500-stock universe used to come only from re-running the research
export, so the 45-day staleness guard stopped paper planning about six weeks
after each export. `geld refresh-universe` (called by `run-daily`) now rebuilds
it monthly from current Alpaca assets with the research rules: top 500 by
60-session median dollar volume, $5 minimum price, 252 sessions of history and a
$20 million minimum. Before publishing, every held name is recorded in the
ownership ledger, so a stock that drops out is sold at the next rebalance
instead of being stranded. A failed refresh never publishes a partial
universe; the runner continues on the previous one while it is within the
staleness guard. Like Intra V15's refresh, this is forward paper planning on
today's assets, not a point-in-time reconstruction.

## Daily V4 on GitHub Actions

The workflow ships as `deploy/github-actions/paper-daily-v4.yml`. Install it
once (or upload it to `.github/workflows/` in the GitHub web editor); it stays
off until you enable it:

```bash
mkdir -p .github/workflows
git mv deploy/github-actions/paper-daily-v4.yml .github/workflows/
git commit -m "Install the Daily V4 workflow" && git push
```

1. Repository **secrets**: `ALPACA_SWING_API_KEY`, `ALPACA_SWING_SECRET_KEY`.
2. Repository **variables**: `DAILY_V4_ENABLED=true`. Leave `DAILY_V4_SUBMIT`
   unset for a few days of dry runs, then set `DAILY_V4_SUBMIT=true`.
3. Optional: run the workflow manually (Actions -> paper-daily-v4 -> Run
   workflow) with task `refresh-universe`, then `cycle`, to check the setup.

Schedule (UTC, weekdays): 11:05 universe refresh; 13:35 and 14:35 trading cycle
(the runner keeps whichever starts in 09:30-10:30 ET, so daylight saving is
handled); 20:30 and 21:30 close check (the runner keeps the first launch after
the delayed close bar). Cycles execute at roughly 09:35-09:50 ET rather than
exactly at the open the backtest assumes. If GitHub delays a launch past
10:30 ET, that day is skipped and the rebalance runs the next session.

If the state branch cannot be read (a token or network problem), the job stops
before trading: running without the cadence file would force an off-schedule
rebalance.

**State.** Runners on Actions start empty, so the cadence state, ownership
ledger, performance log, universe snapshot and close summaries are committed to
the `paper-state/daily-v4` branch after every run. In a public repository that
branch is public. To keep it private, create a private repository, set the
variable `PAPER_STATE_REPO=<owner>/<repo>` and add a secret `PAPER_STATE_TOKEN`
(a fine-grained token with contents read/write on that repository).

**Moving from the Windows PC.** Stop the Windows Daily V4 tasks first (two
schedulers must not trade the same account). Then either copy the PC's
`artifacts/paper-daily-v4/` files into the state branch, or after the first
Actions refresh run, adopt existing positions so they stay managed:

```bash
geld --config configs/paper-daily-v4.toml adopt-positions --all   # dedicated account only
```

GitHub disables scheduled workflows in public repositories after 60 days without
repository activity; if that happens you will get an email, and the workflow
can be re-enabled from the Actions tab.

## Intra V15 on a Linux server

Any small Ubuntu 24.04 server (1 vCPU / 1 GB, about $5/month: Hetzner,
DigitalOcean, Lightsail, Vultr) is enough. On the server:

```bash
git clone https://github.com/Jiang6082/project-geld.git
cd project-geld
sudo bash deploy/install-vps.sh --dry-run  # installs the runner without submitting
nano .env                                  # ALPACA_INTRADAY_* keys, PROJECT_GELD_INTRADAY_CONFIRM_PAPER=YES
```

The installer creates the virtual environment, renders the systemd units in
`deploy/systemd/` and enables `geld-intra-v15.timer` (weekdays 09:20 ET, with
`Persistent=true` so a server that was down joins the session late). After a
day of dry runs, re-run `sudo bash deploy/install-vps.sh` without `--dry-run` to
allow paper orders. `--with-daily` also installs the Daily V4 and close-check
timers if you would rather run everything on the server than on Actions.

Useful commands:

```bash
systemctl list-timers 'geld-*'
journalctl -u geld-intra-v15 -f
cat artifacts/paper-intra-v15/runner-heartbeat.json
git pull && .venv/bin/pip install -e ".[alpaca]"   # update
```

Stop the Windows Intra V15 task before enabling the server timer.

## Staying on the Windows PC

`scripts/run-daily-v4-paper.ps1`, `run-daily-v4-close.ps1` and
`run-intra-v15-paper.ps1` now call the same runners, so the PC gets the Daily
V4 universe refresh, the market calendar and the early-close flatten too. After
pulling, reinstall once (`pip install -e ".[alpaca]"`); the existing scheduled
tasks keep working.

## Checking performance

```bash
geld performance-report             # paper results vs SPY, runner health
geld performance-report --backtest  # plus recent backtest replays of the deployed configs
```

The report (`artifacts/performance-report/report.md` and `report.json`) shows,
for each account: return, drawdown and Sharpe from the paper performance log,
SPY over the same sessions, when the log was last updated, whether the universe
is stale enough to block planning, runner log findings, the kill-switch state
and implementation shortfall. The replays are sanity checks with known biases
(Daily V4 applies today's universe to the past), not evidence.
