#!/bin/bash
# Dashboard Keepalive (hourly cron)
# ---------------------------------
# Ensures the localhost:3737 dashboard server stays up even when no Claude
# session is open. Once the server is alive, the front-end already re-polls
# every 60s and the Tracker board reads journal/state/tickets.json live, so the
# board stays in sync with its SOT with no data rewrite here. This job does NOT
# touch Dashboard.md or tickets.json — it only guarantees the server is running.
#
# Wire in cron (hourly):
#   0 * * * * ./.agent/scripts/dashboard_keepalive.sh

# Resolve repo root portably (macOS / WSL / cron): prefer CLAUDE_PROJECT_DIR,
# else derive from this script's own location (.agent/scripts -> repo root).
REPO_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
LOG_FILE="$REPO_DIR/.agent/scripts/dashboard_keepalive.log"

cd "$REPO_DIR" || exit 1

echo "[$(date)] keepalive tick" >> "$LOG_FILE"

if bash "$REPO_DIR/.agent/scripts/ensure_dashboard.sh" >> "$LOG_FILE" 2>&1; then
  STATUS="ok"
  SUMMARY="localhost:3737 alive"
else
  STATUS="fail"
  SUMMARY="ensure_dashboard.sh failed to bring up 3737"
fi

# Record on the Routines tab so a silent failure is visible.
python3 "$REPO_DIR/.agent/scripts/heartbeat.py" \
  --job dashboard-keepalive --status "$STATUS" --summary "$SUMMARY" \
  >> "$LOG_FILE" 2>&1 || true

# WSL instance uptime, so a Windows-boot without the "WSL Autostart" Task
# Scheduler task kicking in (or a crashed instance) shows up on the Routines
# tab instead of silently going undetected until a cron job fails.
UPTIME_SECS="$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo unknown)"
python3 "$REPO_DIR/.agent/scripts/heartbeat.py" \
  --job wsl-instance --status ok --summary "uptime ${UPTIME_SECS}s" \
  >> "$LOG_FILE" 2>&1 || true
