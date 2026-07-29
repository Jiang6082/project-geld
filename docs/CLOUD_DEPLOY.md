# Fully autonomous deployment (24/7 cloud host)

Local Windows Task Scheduler can't run when the laptop is off or asleep. Moving
the paper runners to a small always-on Linux host removes that dependency
entirely: Alpaca is a remote REST API, and the live paper cycles only fetch
recent bars, plan, and submit — so a ~$5/month VPS is more than enough. (The
heavy *research* backtests still want more RAM; keep running those locally or on
a bigger box on demand. Only the live paper loop needs to be always-on.)

Nothing about safety changes: `paper=True` stays hard-coded, submission stays
gated behind the config flag + confirmation env var, and no live-trading path
exists.

## 1. Provision

- Any small Linux VPS (Ubuntu 22.04+), 1 vCPU / 1 GB RAM: AWS Lightsail,
  DigitalOcean, Hetzner, or a GCP e2-micro all work. ~$5/month.
- SSH in and set the box timezone to Eastern so cron times match the market:
  `sudo timedatectl set-timezone America/New_York`.

## 2. Install

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv git
git clone https://github.com/Jiang6082/project-geld.git
cd project-geld
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[alpaca]"
```

## 3. Credentials (never commit these)

Create `.env` in the repo root with the isolated paper-account keys and the
confirmation flags set to YES so submission is allowed:

```dotenv
ALPACA_SWING_API_KEY=...
ALPACA_SWING_SECRET_KEY=...
ALPACA_INTRADAY_API_KEY=...
ALPACA_INTRADAY_SECRET_KEY=...
PROJECT_GELD_SWING_CONFIRM_PAPER=YES
PROJECT_GELD_INTRADAY_CONFIRM_PAPER=YES
FMP_API_KEY=...            # optional; only for the market-posture skills
```

`chmod 600 .env`. Refresh the Daily V4 universe snapshot on the box (or copy it
up) so the 45-day staleness guard passes:
`.venv/bin/python scripts/export_current_universe.py`.

Sanity-check without trading first (no `--submit`):

```bash
.venv/bin/python -m project_geld.cli --config configs/paper-daily-v4.toml paper-once
.venv/bin/python -m project_geld.cli --config configs/paper-intra-v15.toml intraday-paper-once
```

## 4. Schedule with cron (Eastern time)

`crontab -e` and add (paths assume the repo at `~/project-geld`):

```cron
CRON_TZ=America/New_York
PG=/home/USER/project-geld

# Daily V4 swing execution — 09:25 ET weekdays (cadence guard still gates trades)
25 9 * * 1-5   cd $PG && .venv/bin/python -m project_geld.cli --config configs/paper-daily-v4.toml paper-once --submit >> artifacts/cron-daily-v4.log 2>&1

# Daily V4 read-only close reconciliation — 16:25 ET
25 16 * * 1-5  cd $PG && .venv/bin/python -m project_geld.cli --config configs/paper-daily-v4.toml daily-close-check >> artifacts/cron-daily-v4-close.log 2>&1

# Intra V15 — every 15 min through the session; each run processes the newly
# completed bar (the cycle-state guard prevents double-processing, and the
# 15:30/15:45 flattens fire on their bars).
*/15 9-15 * * 1-5  cd $PG && .venv/bin/python -m project_geld.cli --config configs/paper-intra-v15.toml intraday-paper-once --submit >> artifacts/cron-intra-v15.log 2>&1
```

`CRON_TZ` handles DST automatically. The intra line fires at :00/:15/:30/:45 of
09:00–15:45 ET; pre-open invocations find no completed bar and no-op.

## 5. Keep the code current

```bash
cd ~/project-geld && git pull && .venv/bin/pip install -e ".[alpaca]"
```

## Alternative: GitHub Actions (serverless)

A scheduled Actions workflow with the keys in repo **secrets** can run the
cycles with no VPS. Fine for **Daily V4** (monthly, timing-insensitive). Not
recommended for **Intra V15**: Actions cron can be delayed 5–15 minutes, which
breaks the 10:30 signal precision and the exact 15:30/15:45 flattens. Use the
VPS + cron above for the intraday account.
```
