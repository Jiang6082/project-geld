#!/usr/bin/env bash
# Install Project Geld's paper runners on an Ubuntu/Debian server with systemd.
#
#   sudo deploy/install-vps.sh                # Intra V15 only (Daily V4 on GitHub Actions)
#   sudo deploy/install-vps.sh --with-daily   # also run Daily V4 and its close check here
#   sudo deploy/install-vps.sh --dry-run      # install, but never submit paper orders
#
# Run from the repository checkout, as root (sudo). The services run as the
# user who owns the checkout. Re-running is safe: units are rewritten and the
# virtual environment is upgraded in place.
set -euo pipefail

WITH_DAILY=0
SUBMIT="--submit"
for arg in "$@"; do
  case "$arg" in
    --with-daily) WITH_DAILY=1 ;;
    --dry-run) SUBMIT="" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo so the systemd units can be installed." >&2
  exit 1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OWNER="$(stat -c %U "$REPO")"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Python 3.11+ is required (Ubuntu 24.04 ships 3.12: sudo apt install python3 python3-venv)." >&2
  exit 1
fi

echo "Repository: $REPO (owner $OWNER), Python: $PYTHON"
sudo -u "$OWNER" "$PYTHON" -m venv "$REPO/.venv"
sudo -u "$OWNER" "$REPO/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$OWNER" "$REPO/.venv/bin/pip" install --quiet -e "$REPO[alpaca]"
sudo -u "$OWNER" mkdir -p "$REPO/artifacts/logs"

if [ ! -f "$REPO/.env" ]; then
  sudo -u "$OWNER" cp "$REPO/.env.example" "$REPO/.env"
  echo "Created $REPO/.env from .env.example: add the paper keys before the first session."
fi
chmod 600 "$REPO/.env"

units=(geld-intra-v15.service geld-intra-v15.timer)
if [ "$WITH_DAILY" -eq 1 ]; then
  units+=(geld-daily-v4.service geld-daily-v4.timer geld-daily-v4-close.service geld-daily-v4-close.timer)
fi
for unit in "${units[@]}"; do
  sed -e "s|@REPO@|$REPO|g" -e "s|@USER@|$OWNER|g" -e "s|@SUBMIT@|$SUBMIT|g" \
    "$REPO/deploy/systemd/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
for unit in "${units[@]}"; do
  case "$unit" in *.timer) systemctl enable --now "$unit" ;; esac
done

echo
echo "Installed: ${units[*]}"
echo "Next run:  systemctl list-timers 'geld-*'"
echo "Logs:      journalctl -u geld-intra-v15 -f   (JSON: $REPO/artifacts/logs/)"
echo "Dry run a session now: sudo -u $OWNER $REPO/.venv/bin/geld --config configs/paper-intra-v15.toml run-intraday --output artifacts/paper-intra-v15"
if [ -z "$SUBMIT" ]; then
  echo "Installed in DRY-RUN mode: no paper orders will be submitted."
fi
