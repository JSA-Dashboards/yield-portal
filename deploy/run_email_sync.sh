#!/usr/bin/env bash
# run_email_sync.sh — the report-email sync on the Droplet, every 30 minutes by cron.
#
# Runs match_emails.py --source graph --auto: reads the report emails from the
# mailbox on the server through Microsoft Graph (graph_mail.py) and dates / adds
# reports in Snowflake YIELD_REPORTS. --auto writes, logs to logs/email_sync.log
# and records the run in EMAIL_SYNC_RUNS (Review & edit shows it). match_emails
# loads .env from its own directory. flock prevents overlapping runs. Mirrors
# river-fob-portal's deploy/run_vessel.sh. Setup: deploy/DROPLET.md.
set -uo pipefail

APP_DIR="/opt/yield-portal"                   # <-- clone path
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/email_sync_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

# Single-instance guard: skip silently if a run is already going.
exec 9>"$LOG_DIR/.email_sync.lock"
if ! flock -n 9; then
    echo "$(date -Is) another email sync is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== email_sync start $(date -Is) ==="
    "$VENV/bin/python" match_emails.py --target snowflake --source graph --auto
    rc=$?    # capture immediately — must precede any other command (e.g. date)
    echo "=== email_sync finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

# Every 30 minutes makes 48 files a day: keep a week.
find "$LOG_DIR" -name 'email_sync_*.log' -mtime +7 -delete 2>/dev/null || true

# Surface the real exit code to cron-alert.
exit "$rc"
