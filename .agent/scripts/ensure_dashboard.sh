#!/bin/bash

# Dashboard Server Service Script
# Ensures a HEALTHY local dashboard server is running on port 3737.
#
# "Healthy" is not the same as "the port is bound". On 9 Aug 2026 the macOS
# server was left running with its cwd inside a directory that was later
# deleted, so PUBLIC_DIR and BASE_DIR (both derived from the process's resolved
# path) pointed at nothing: every page returned 404 and every ledger lookup
# returned zero results. This script kept reporting "already running" for three
# days because the port was still listening. So the check is now:
#
#   1. does /index.html actually serve (proves PUBLIC_DIR resolves), and
#   2. is the listener's cwd this repo (proves it reads THIS checkout's ledgers)
#
# A listener that fails either check is killed and replaced -- but only when it
# is recognisably our own dashboard/server.py, never some unrelated process that
# happens to hold the port.

set -u

PORT=3737
SERVER_SCRIPT="dashboard/server.py"
LOG_DIR="dashboard/logs"
STDOUT_LOG="$LOG_DIR/server_stdout.log"
STDERR_LOG="$LOG_DIR/server_stderr.log"

# Anchor to the repo root from this script's own location, so the server we
# start always inherits the right cwd no matter where the caller invoked from.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || { echo "❌ Cannot cd to repo root $REPO_ROOT"; exit 1; }

mkdir -p "$LOG_DIR"

# ── helpers ─────────────────────────────────────────────────────────────────

listener_pids() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null
    elif command -v ss >/dev/null 2>&1; then
        ss -lptnH "sport = :$PORT" 2>/dev/null |
            grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u
    fi
}

proc_cwd() {
    local pid="$1"
    if [ -r "/proc/$pid/cwd" ]; then
        readlink -f "/proc/$pid/cwd" 2>/dev/null
    elif command -v lsof >/dev/null 2>&1; then
        lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | grep '^n' | head -1 | cut -c2-
    fi
}

is_our_server() {
    ps -o command= -p "$1" 2>/dev/null | grep -q "$SERVER_SCRIPT"
}

serves_ui() {
    curl -fsS -o /dev/null --max-time 4 "http://localhost:$PORT/index.html" 2>/dev/null
}

wait_until_up() {
    for _ in $(seq 1 12); do
        sleep 1
        serves_ui && return 0
    done
    return 1
}

# ── 1. is something already on the port, and is it actually healthy? ────────

PIDS="$(listener_pids)"

if [ -n "$PIDS" ]; then
    UNHEALTHY_REASON=""

    if ! serves_ui; then
        UNHEALTHY_REASON="/index.html does not serve (broken PUBLIC_DIR)"
    else
        for pid in $PIDS; do
            CWD="$(proc_cwd "$pid")"
            if [ -n "$CWD" ] && [ "$CWD" != "$REPO_ROOT" ]; then
                UNHEALTHY_REASON="listener pid $pid runs from '$CWD', not '$REPO_ROOT'"
                break
            fi
        done
    fi

    if [ -z "$UNHEALTHY_REASON" ]; then
        echo "✅ Dashboard server healthy on port $PORT ($REPO_ROOT)"
        exit 0
    fi

    echo "⚠️  Dashboard on port $PORT is unhealthy: $UNHEALTHY_REASON"

    KILLED=0
    for pid in $PIDS; do
        if is_our_server "$pid"; then
            kill "$pid" 2>/dev/null && KILLED=1
        else
            echo "❌ pid $pid holds port $PORT but is not $SERVER_SCRIPT -- refusing to kill it."
            exit 1
        fi
    done

    if [ "$KILLED" = "1" ]; then
        for _ in 1 2 3 4 5; do
            sleep 1
            [ -z "$(listener_pids)" ] && break
        done
        # still holding on? escalate once, then give the socket a moment
        for pid in $(listener_pids); do
            is_our_server "$pid" && kill -9 "$pid" 2>/dev/null
        done
        sleep 1
        echo "🔁 Replaced the stale dashboard process."
    fi
fi

# ── 2. start a fresh one from the repo root ─────────────────────────────────

echo "🔄 Starting Dashboard server..."

if [ ! -f "$SERVER_SCRIPT" ]; then
    echo "❌ Error: $SERVER_SCRIPT not found under $REPO_ROOT."
    exit 1
fi

# -u: unbuffered. Python block-buffers stdout when it is redirected to a file, so
# the request log stayed empty for the whole of an incident and only flushed at
# 8KB or exit -- exactly when it was needed, it showed nothing.
nohup python3 -u "$SERVER_SCRIPT" > "$STDOUT_LOG" 2> "$STDERR_LOG" &

if wait_until_up; then
    echo "✅ Dashboard server started at http://localhost:$PORT ($REPO_ROOT)"
    exit 0
fi

echo "❌ Error: Dashboard server did not serve /index.html within 12 seconds."
echo "Check logs at $STDERR_LOG"
exit 1
