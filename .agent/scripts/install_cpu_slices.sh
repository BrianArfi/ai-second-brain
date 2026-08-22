#!/usr/bin/env bash
# Hard-cap background CPU so a cron burst can never starve interactive work.
#
# Layer 2 of the WSL CPU throttle (layer 1 is cron_throttle.py, which renices
# the cron fleet). Renicing fixes who wins a contest; this fixes how much of the
# machine the losers may occupy at all, which is what keeps the VS Code server's
# heartbeat alive through a burst instead of letting the connection drop.
#
# The split follows where things actually live in the cgroup tree, verified on
# 7 Aug 2026: cron and docker sit in system.slice, while VS Code, the Claude
# panels, and the app.slice services (meetbot, dashboard, headless chrome) sit
# under user.slice.
#
#   system.slice  CPUQuota=500%   -- cron + docker may use at most 5 of 12 vCPUs
#                 CPUWeight=20    -- and lose every contest against interactive
#   user.slice    CPUWeight=1000  -- VS Code and Claude win by ~50:1
#
# Quota is expressed against 100% = one vCPU, so 500% of 12 vCPUs leaves 7 free
# for interactive work even while every cron job in the fleet is running.
#
# Fully reversible: run with --remove, or delete the two drop-in files and
# reload. Nothing here survives a `wsl --shutdown` differently than any other
# systemd config -- it is persistent and applies again on next boot.
#
# Usage:
#   sudo bash install_cpu_slices.sh            # install
#   sudo bash install_cpu_slices.sh --remove   # revert
#   bash install_cpu_slices.sh --show          # print current state, no sudo

set -euo pipefail

SYS_DIR=/etc/systemd/system/system.slice.d
USR_DIR=/etc/systemd/system/user.slice.d
DROPIN=90-cpu-throttle.conf

show() {
  echo "=== current effective limits ==="
  systemctl show system.slice -p CPUQuotaPerSecUSec -p CPUWeight 2>/dev/null
  systemctl show user.slice   -p CPUQuotaPerSecUSec -p CPUWeight 2>/dev/null
  echo
  echo "=== drop-ins present ==="
  ls -1 "$SYS_DIR/$DROPIN" "$USR_DIR/$DROPIN" 2>/dev/null || echo "(none installed)"
}

case "${1:-install}" in
  --show)
    show
    exit 0
    ;;
  --remove)
    rm -f "$SYS_DIR/$DROPIN" "$USR_DIR/$DROPIN"
    systemctl daemon-reload
    echo "removed. current state:"
    show
    exit 0
    ;;
esac

mkdir -p "$SYS_DIR" "$USR_DIR"

cat > "$SYS_DIR/$DROPIN" <<'EOF'
# Background work: cron fleet (128 jobs, bursting to ~78 at :00) plus docker.
# Capped so a burst cannot occupy the whole VM.
[Slice]
CPUQuota=500%
CPUWeight=20
EOF

cat > "$USR_DIR/$DROPIN" <<'EOF'
# Interactive: VS Code server, Claude CLI panels, browsers.
# No quota -- this side is allowed the whole machine when it needs it.
[Slice]
CPUWeight=1000
EOF

systemctl daemon-reload
echo "installed. current state:"
show
