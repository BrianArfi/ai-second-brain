#!/bin/bash
# Replaces the running AI Second Brain with the staged build. ONE SHOT.
#
# Runs under launchd, not as a child of the app: the session that schedules this lives INSIDE the
# app, so a child would be killed halfway through, which is the one moment /Applications holds a
# half-copied bundle.
#
# Lives in ~/asb-artifacts rather than /tmp because /tmp was purged mid-job last time, taking the
# script, its log and the staged build with it.
#
# NEVER schedule this with `launchctl submit`. That command sets KeepAlive, so launchd restarts a
# one-shot script forever. It happened twice on 22 Aug 2026: 87 reinstalls, 7 GB of backups, and
# the app blinking on and off every 11 seconds. Use schedule_install.sh, which writes a plist with
# KeepAlive false. The four guards below make a repeat harmless even if someone forgets.

set -uo pipefail
ART="$HOME/asb-artifacts"
LABEL="com.aisecondbrain.install"
exec >> "$ART/install.log" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') install starting (pid $$) ==="

STAGE="$ART/stage/AI Second Brain.app"
TARGET="/Applications/AI Second Brain.app"
LOCK="$ART/.install.lock"
FPFILE="$ART/.installed_fingerprint"
STARTS="$ART/.install_starts"

# Guard 0: whatever happens below, take the launchd job down on the way out.
cleanup() {
  rmdir "$LOCK" 2>/dev/null
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" 2>/dev/null
  launchctl remove "$LABEL" 2>/dev/null   # legacy `submit` jobs answer to this, not to bootout
}
trap cleanup EXIT

# Guard 1: circuit breaker. More than 3 starts in 5 minutes is a loop, not a user.
now=$(date +%s)
touch "$STARTS"
awk -v n="$now" '$1 > n-300' "$STARTS" > "$STARTS.tmp" 2>/dev/null; mv "$STARTS.tmp" "$STARTS"
echo "$now" >> "$STARTS"
if [ "$(wc -l < "$STARTS")" -gt 3 ]; then
  echo "ABORT: $(wc -l < "$STARTS") starts in the last 5 minutes. This is a relaunch loop."
  echo "       Tearing the job down. Check with: launchctl print gui/$(id -u)/$LABEL"
  : > "$STARTS"
  exit 1
fi

# Guard 2: one at a time.
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "another install is already running, exiting"
  trap - EXIT; exit 0
fi

[ -d "$STAGE" ] || { echo "no staged build, aborting"; exit 1; }
codesign --verify --strict "$STAGE" || { echo "staged build does not verify, aborting"; exit 1; }

# Guard 3: this exact build is already installed. Re-running is a no-op, not another 84 MB backup.
FP=$(codesign -dvvv "$STAGE" 2>&1 | awk -F= '/^CDHash=/{print $2}')
[ -n "$FP" ] || FP=$(stat -f '%z-%m' "$STAGE/Contents/MacOS/desktop" 2>/dev/null)
if [ -n "$FP" ] && [ "$FP" = "$(cat "$FPFILE" 2>/dev/null)" ] \
   && [ "$FP" = "$(codesign -dvvv "$TARGET" 2>&1 | awk -F= '/^CDHash=/{print $2}')" ]; then
  echo "staged build $FP is already installed, nothing to do"
  exit 0
fi

sleep 8

echo "asking the app to quit"
osascript -e 'tell application "AI Second Brain" to quit' 2>/dev/null
for i in $(seq 1 30); do
  pgrep -f "AI Second Brain.app/Contents/MacOS/desktop" >/dev/null || break
  sleep 1
done
if pgrep -f "AI Second Brain.app/Contents/MacOS/desktop" >/dev/null; then
  echo "did not quit in 30s, ending it"
  pkill -f "AI Second Brain.app/Contents/MacOS/desktop"
  sleep 3
fi

BACKUP="$HOME/.asb-backups/AI Second Brain $(date '+%Y%m%d-%H%M%S').app"
mkdir -p "$HOME/.asb-backups"
ditto "$TARGET" "$BACKUP" || { echo "backup failed, refusing to replace"; exit 1; }
echo "backed up to $BACKUP"

rm -rf "$TARGET"
ditto "$STAGE" "$TARGET" || { echo "copy failed, restoring"; ditto "$BACKUP" "$TARGET"; exit 1; }

if ! codesign --verify --strict "$TARGET"; then
  echo "installed copy does not verify, restoring"
  rm -rf "$TARGET"; ditto "$BACKUP" "$TARGET"; exit 1
fi
VER=$(defaults read "$TARGET/Contents/Info.plist" CFBundleShortVersionString)
echo "installed $VER, signature verifies"
[ -n "$FP" ] && echo "$FP" > "$FPFILE"

# Guard 4: keep the 5 newest backups. The old script kept every one, forever.
ls -1dt "$HOME/.asb-backups/AI Second Brain "*.app 2>/dev/null | tail -n +6 | while IFS= read -r old; do
  echo "pruning old backup: $(basename "$old")"
  rm -rf "$old"
done

xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null
open -a "$TARGET"
echo "=== $(date '+%H:%M:%S') done ==="
