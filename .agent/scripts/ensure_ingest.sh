#!/bin/bash

# Phone Ingest Server Service Script
# Ensures a HEALTHY meeting ingest server is running on port 8787.
#
# Same health definition as ensure_dashboard.sh, and for the same reason: a bound
# port proves nothing. The checks are
#
#   1. does /health actually answer, and
#   2. is the listener's cwd this repo (it resolves recordings_dir and the
#      pairing token from its own checkout, so the wrong cwd silently files
#      recordings into another clone)
#
# A listener that fails either check is killed and replaced -- but only when it
# is recognisably our own ingest_server.py, never some unrelated process that
# happens to hold the port.

set -u

PORT="${ASB_INGEST_PORT:-8787}"
SERVER_SCRIPT="meeting-recorder/ingest_server.py"
LOG_DIR="meeting-recorder/logs"
STDOUT_LOG="$LOG_DIR/ingest_stdout.log"
STDERR_LOG="$LOG_DIR/ingest_stderr.log"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || { echo "❌ Cannot cd to repo root $REPO_ROOT"; exit 1; }

mkdir -p "$LOG_DIR"

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

serves_health() {
    curl -fsS -o /dev/null --max-time 4 "http://127.0.0.1:$PORT/health" 2>/dev/null
}

wait_until_up() {
    for _ in $(seq 1 10); do
        sleep 1
        serves_health && return 0
    done
    return 1
}

# ── 1. already up and healthy? ──────────────────────────────────────────────

PIDS="$(listener_pids)"

if [ -n "$PIDS" ]; then
    UNHEALTHY_REASON=""

    if ! serves_health; then
        UNHEALTHY_REASON="/health does not answer"
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
        echo "✅ Ingest server healthy on port $PORT ($REPO_ROOT)"
        exit 0
    fi

    echo "⚠️  Ingest server on port $PORT is unhealthy: $UNHEALTHY_REASON"

    # An in-flight recording lives in ingest_sessions/ and is recovered on the
    # next start, so replacing the process does not lose a meeting.
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
        for pid in $(listener_pids); do
            is_our_server "$pid" && kill -9 "$pid" 2>/dev/null
        done
        sleep 1
        echo "🔁 Replaced the stale ingest process."
    fi
fi

# ── 2. start a fresh one from the repo root ─────────────────────────────────

if [ ! -f "$SERVER_SCRIPT" ]; then
    echo "❌ Error: $SERVER_SCRIPT not found under $REPO_ROOT."
    exit 1
fi

echo "🔄 Starting phone ingest server..."

# -u for the same reason as the dashboard: block-buffered stdout keeps the log
# empty for exactly as long as someone needs to read it.
nohup python3 -u "$SERVER_SCRIPT" > "$STDOUT_LOG" 2> "$STDERR_LOG" &

if wait_until_up; then
    echo "✅ Ingest server started on http://127.0.0.1:$PORT ($REPO_ROOT)"
    exit 0
fi

echo "❌ Error: Ingest server did not answer /health within 10 seconds."
echo "Check logs at $STDERR_LOG"
exit 1
