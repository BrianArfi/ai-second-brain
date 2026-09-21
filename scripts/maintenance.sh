#!/bin/bash
# Google Calendar Maintenance Script
# This script refreshes OAuth tokens by performing a silent sweep.

# Resolve repo root portably (macOS / WSL / cron): prefer CLAUDE_PROJECT_DIR,
# else derive from this script's own location (scripts/ -> repo root).
REPO_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
LOG_FILE="$REPO_DIR/scripts/maintenance.log"

echo "-----------------------------------------------" >> "$LOG_FILE"
echo "Daily Maintenance Started: $(date)" >> "$LOG_FILE"

cd "$REPO_DIR"

# Refresh all Google Services (Calendar & Drive) via Unified Auth Manager
echo "[$(date)] Running Unified Auth Manager..." >> "$LOG_FILE"
# GNU `timeout` exists on Linux/WSL but not stock macOS; degrade gracefully.
if command -v timeout >/dev/null 2>&1; then
  timeout 600s python3 scripts/auth_manager.py >> "$LOG_FILE" 2>&1
else
  python3 scripts/auth_manager.py >> "$LOG_FILE" 2>&1
fi
AUTH_RC=$?

# Self-report to the Routines panel so harness-health sees this job directly.
# Summary carries the healthy-service ratio (e.g. "3/6") so partial token
# expiries are visible without flapping the job to fail for known-dead profiles.
# Degraded (some services unhealthy but the sweep itself ran) reports ok+needs-reauth,
# which the dashboard/harness-health already surface as a 'warn' state -- distinct
# from a hard fail (auth_manager itself crashed/timed out).
RATIO=$(grep -o 'Routine Finished: [0-9]*/[0-9]*' "$LOG_FILE" | tail -1 | grep -o '[0-9]*/[0-9]*')
FAILED_NAMES=$(grep '] Failed services:' "$LOG_FILE" | tail -1 | sed -E 's/^.*\] Failed services: //')
HEALTHY_COUNT="${RATIO%%/*}"
TOTAL_COUNT="${RATIO##*/}"

if [ "$AUTH_RC" -ne 0 ]; then
  python3 "$REPO_DIR/.agent/scripts/heartbeat.py" --job maintenance --status fail --summary "auth_manager exit $AUTH_RC (${RATIO:-?} healthy)" >> "$LOG_FILE" 2>&1
elif [ -n "$HEALTHY_COUNT" ] && [ -n "$TOTAL_COUNT" ] && [ "$HEALTHY_COUNT" -lt "$TOTAL_COUNT" ]; then
  python3 "$REPO_DIR/.agent/scripts/heartbeat.py" --job maintenance --status ok --needs-reauth --summary "token refresh degraded: ${RATIO} healthy, failed: ${FAILED_NAMES:-unknown}" >> "$LOG_FILE" 2>&1
else
  python3 "$REPO_DIR/.agent/scripts/heartbeat.py" --job maintenance --status ok --summary "token refresh sweep: ${RATIO:-?} services healthy" >> "$LOG_FILE" 2>&1
fi

# Model id drift: are the agy-bridge chains still pointing at models that exist?
# Vendors retire ids without warning and a dead chain entry fails silently --
# run.py refuses it and falls through, so bulk work quietly lands on a Pro tier.
# --heartbeat self-reports to the Routines panel, so the app surfaces this the
# same way it surfaces every other job, rather than it living in a log nobody opens.
# Never fatal: drift is a finding, not a broken maintenance run, so the exit code
# is recorded and swallowed.
echo "[$(date)] Checking agy-bridge model id drift..." >> "$LOG_FILE"
if command -v timeout >/dev/null 2>&1; then
  timeout 300s python3 "$REPO_DIR/.agent/scripts/model_drift_check.py" --heartbeat >> "$LOG_FILE" 2>&1
else
  python3 "$REPO_DIR/.agent/scripts/model_drift_check.py" --heartbeat >> "$LOG_FILE" 2>&1
fi
echo "[$(date)] model drift check exit $?" >> "$LOG_FILE"

echo "Daily Maintenance Finished: $(date)" >> "$LOG_FILE"
echo "-----------------------------------------------" >> "$LOG_FILE"
