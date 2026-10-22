#!/usr/bin/env bash
# run_variety_watch.sh — weekly in Nov-Mar by cron on the Droplet: email when a state
# posts a new season's variety-trial results (variety_watch.py --notify). The refresh
# itself is done by hand; see CLAUDE.md "Variety trials". flock prevents overlapping
# runs. Mirrors run_field_signal.sh.
set -uo pipefail

APP_DIR="/opt/yield-portal"                   # <-- clone path
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/variety_watch_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.variety_watch.lock"
if ! flock -n 9; then
    echo "$(date -Is) another variety watch is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    "$VENV/bin/python" variety_watch.py --notify
    rc=$?    # capture immediately — must precede any other command (e.g. date)
    echo "=== variety_watch finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'variety_watch_*.log' -mtime +200 -delete 2>/dev/null || true

exit "$rc"
