#!/usr/bin/env bash
# run_isa_refresh.sh — pick up new ISA strip-trial reports and reload ISA_STRIP_TRIALS.
# Cron on the Droplet: the 1st and 15th of Oct-Dec, the 1st of other months (Kolten,
# 2026-10-22).
#
# `load_isa.py fetch` reads ISA's trial list through the current year and downloads
# only the reports not cached yet (data/isa/text, 2 s between requests), so a run
# with nothing new takes a minute. `load` then rebuilds ISA_STRIP_TRIALS from the
# whole cache (dropped and recreated; the schema's future grants re-apply). A failed
# fetch skips the load, so the table never shrinks to a partial cache. flock
# prevents overlapping runs. Mirrors run_field_signal.sh.
set -uo pipefail

APP_DIR="/opt/yield-portal"                   # <-- clone path
VENV="$APP_DIR/.venv"
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/isa_refresh_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

exec 9>"$LOG_DIR/.isa_refresh.lock"
if ! flock -n 9; then
    echo "$(date -Is) another ISA refresh is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== isa_refresh start $(date -Is) ==="
    LIST="$APP_DIR/data/isa/trials.csv"
    cp -p "$LIST" "$LIST.prev" 2>/dev/null
    "$VENV/bin/python" load_isa.py fetch --last-year "$(date +%Y)"
    rc=$?    # capture immediately — must precede any other command (e.g. date)
    # a short answer from ISA's server (a year that came back empty) would shrink the
    # table: keep the previous list and skip the load
    if [ "$rc" -eq 0 ] && [ -f "$LIST.prev" ] && \
       [ "$(wc -l < "$LIST")" -lt "$(wc -l < "$LIST.prev")" ]; then
        echo "trial list shrank ($(wc -l < "$LIST.prev") -> $(wc -l < "$LIST") lines): kept the old one"
        cp -p "$LIST.prev" "$LIST"
        rc=3
    fi
    if [ "$rc" -eq 0 ]; then
        "$VENV/bin/python" load_isa.py load --target snowflake
        rc=$?
    else
        echo "fetch failed or list shrank (rc=$rc): table left as it was"
    fi
    echo "=== isa_refresh finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

find "$LOG_DIR" -name 'isa_refresh_*.log' -mtime +120 -delete 2>/dev/null || true

exit "$rc"
