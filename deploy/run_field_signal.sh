#!/usr/bin/env bash
# run_field_signal.sh — the field signal behind the Conditions dashboard's Field
# Check tab, twice a day by cron on the Droplet.
#
# Runs field_signal.py --apply: reads the reports and the NASS cache from
# Snowflake and replaces YIELD_REPORTS.PUBLIC.FIELD_SIGNAL in one statement.
# Needs only the Snowflake settings in .env (deploy/DROPLET.md). flock prevents
# overlapping runs. Mirrors run_weekly_email.sh.
set -uo pipefail

APP_DIR="/opt/yield-portal"                   # <-- clone path
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/field_signal_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.field_signal.lock"
if ! flock -n 9; then
    echo "$(date -Is) another field signal run is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== field_signal start $(date -Is) ==="
    "$VENV/bin/python" field_signal.py --apply
    rc=$?    # capture immediately — must precede any other command (e.g. date)
    echo "=== field_signal finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'field_signal_*.log' -mtime +30 -delete 2>/dev/null || true

exit "$rc"
