#!/usr/bin/env bash
# run_weekly_email.sh — the weekly yield-report email, Tuesdays 7am CT by cron on the Droplet.
#
# Runs weekly_email.py --scheduled --via graph: builds the email from Snowflake
# YIELD_REPORTS (tiles, charts, the season's report text) and sends it through
# Microsoft Graph as the shared mailbox. Recipient, reply-to and the Graph
# settings come from .env (deploy/DROPLET.md); the WEEKLY_EMAILS table makes it
# send at most once per week. flock prevents overlapping runs. Mirrors
# river-fob-portal's deploy/run_vessel.sh.
set -uo pipefail

APP_DIR="/opt/yield-portal"                   # <-- clone path
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/weekly_email_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.weekly_email.lock"
if ! flock -n 9; then
    echo "$(date -Is) another weekly email run is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== weekly_email start $(date -Is) ==="
    "$VENV/bin/python" weekly_email.py --scheduled --via graph
    rc=$?    # capture immediately — must precede any other command (e.g. date)
    echo "=== weekly_email finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'weekly_email_*.log' -mtime +90 -delete 2>/dev/null || true

exit "$rc"
