#!/bin/bash
# The ONLY correct way to hand install.sh to launchd.
#
# Do not use `launchctl submit`. It sets KeepAlive, which restarts a one-shot script forever.
# This writes a plist with RunAtLoad true and KeepAlive false, so the job runs exactly once,
# and install.sh boots the job out of launchd itself when it finishes.
set -euo pipefail
LABEL="com.aisecondbrain.install"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_=$(id -u)

[ -x "$HOME/asb-artifacts/install.sh" ] || { echo "install.sh missing or not executable"; exit 1; }

launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
launchctl remove "$LABEL" 2>/dev/null || true

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$HOME/asb-artifacts/install.sh</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>AbandonProcessGroup</key><true/>
  <key>StandardErrorPath</key><string>$HOME/asb-artifacts/install.log</string>
</dict>
</plist>
PLISTEOF

plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "gui/$UID_" "$PLIST"
echo "scheduled $LABEL (runs once, KeepAlive false)"
echo "follow it with: tail -f \$HOME/asb-artifacts/install.log"
