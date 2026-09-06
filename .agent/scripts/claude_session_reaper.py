#!/usr/bin/env python3
"""Reap idle Claude CLI sessions to stop them accumulating in WSL memory.

Why this exists: VS Code Claude panels spawn a long-lived `claude` process each.
Nothing reaps them, so a working day leaves 13+ resident at ~170 MB apiece --
roughly 2.3 GB, the single largest growth driver in vmmemWSL (diagnosed 6 Aug 2026).

Safety rests on one verified fact: every session streams its transcript to
~/.claude/projects/<slug>/<session-id>.jsonl continuously, and a killed session
is restored in full with `claude --resume <session-id>`. Killing an idle session
therefore loses no conversation. What WOULD lose work is killing a session
mid-turn, so idleness is measured by CPU time and must hold across several
consecutive samples before anything is signalled.

Default mode is --dry-run. Nothing dies unless you pass --apply.

Usage:
    claude_session_reaper.py                    # report only (safe)
    claude_session_reaper.py --apply            # actually reap
    claude_session_reaper.py --idle-hours 4     # be lazier about it
    claude_session_reaper.py --list             # just show what is running
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

STATE_PATH = Path.home() / ".cache" / "claude_session_reaper.json"
RESUME_HINTS = Path.home() / ".cache" / "claude_reaped_sessions.md"

# WSL: VS Code panels spawn native-binary/claude. macOS: the ASB desktop app and
# terminals spawn ~/.local/bin/claude; the same idle logic applies, but /proc does
# not exist there, so every read falls back to ps/lsof on darwin.
IS_DARWIN = sys.platform == "darwin"
PROC_PATTERN = ".local/bin/claude" if IS_DARWIN else "native-binary/claude"

# CPU seconds a process may burn between two samples and still count as "quiet".
#
# Measured on 7 Aug 2026 over a 5-minute sample of 10 live sessions, normalised
# to the 900s cron interval:
#
#   idle at a prompt   5.6 - 7.4 s / 900s   (file watchers, telemetry, IPC)
#   mid-turn          30.8 - 80.6 s / 900s
#
# The original 2.0 sat below the idle floor, so every session looked busy on
# every run and the idle counter reset to zero each time -- the reaper reported
# "0 idle" indefinitely and could never have fired. 15.0 sits in the empty gap:
# 2x the idle ceiling, half the slowest active session.
QUIET_CPU_SECONDS = 15.0

CLK_TCK = os.sysconf("SC_CLK_TCK")

def _parse_clock(text):
    """Parse ps [[dd-]hh:]mm:ss[.ff] into seconds, or None."""
    text = text.strip()
    if not text:
        return None
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        try:
            days = int(d)
        except ValueError:
            return None
    parts = text.split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return None
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return days * 86400 + secs

def _ps_field(pid, field):
    try:
        out = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip()
    except subprocess.SubprocessError:
        return ""

def read_proc_cpu(pid):
    """Return (utime + stime) in seconds, or None if the process is gone."""
    if IS_DARWIN:
        val = _parse_clock(_ps_field(pid, "cputime"))
        return val
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    # comm may contain spaces/parens, so split after the closing paren.
    try:
        tail = stat[stat.rindex(")") + 2:].split()
        utime, stime = int(tail[11]), int(tail[12])
    except (ValueError, IndexError):
        return None
    return (utime + stime) / CLK_TCK

def read_proc_age(pid):
    if IS_DARWIN:
        val = _parse_clock(_ps_field(pid, "etime"))
        return int(val) if val is not None else 0
    try:
        out = subprocess.run(
            ["ps", "-o", "etimes=", "-p", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
        return int(out.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return 0

def read_proc_rss_mb(pid):
    if IS_DARWIN:
        try:
            return int(_ps_field(pid, "rss")) // 1024
        except ValueError:
            return 0
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass
    return 0

def read_cmdline(pid):
    if IS_DARWIN:
        # Whitespace-split loses quoting, but the fields we match on
        # (the binary path and --resume=<uuid>) never contain spaces.
        return _ps_field(pid, "command").split()
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return []
    return raw.decode("utf-8", "replace").split("\0")

def read_cwd(pid):
    if IS_DARWIN:
        try:
            out = subprocess.run(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                capture_output=True, text=True, timeout=10,
            )
            for line in out.stdout.splitlines():
                if line.startswith("n"):
                    return line[1:]
        except subprocess.SubprocessError:
            pass
        return "?"
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return "?"

def find_sessions():
    """Every live Claude CLI process, with the detail needed to judge and restore it."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", PROC_PATTERN], capture_output=True, text=True, timeout=15
        )
    except subprocess.SubprocessError:
        return []

    sessions = []
    for line in out.stdout.split():
        try:
            pid = int(line)
        except ValueError:
            continue

        argv = read_cmdline(pid)
        if not any(PROC_PATTERN in a for a in argv):
            continue

        session_id = None
        for arg in argv:
            m = re.match(r"^--resume=([0-9a-f-]{36})$", arg)
            if m:
                session_id = m.group(1)
                break

        cpu = read_proc_cpu(pid)
        if cpu is None:
            continue

        cwd = read_cwd(pid)

        sessions.append({
            "pid": pid,
            "session_id": session_id,
            "cwd": cwd,
            "cpu": cpu,
            "age_s": read_proc_age(pid),
            "rss_mb": read_proc_rss_mb(pid),
        })
    return sessions

def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE_PATH)

def confirm_still_quiet(pid, settle_seconds=20):
    """Re-sample right before signalling, so a session that just woke up survives.

    The cron sample can be up to an interval old. This closes that window.
    """
    before = read_proc_cpu(pid)
    if before is None:
        return False
    time.sleep(settle_seconds)
    after = read_proc_cpu(pid)
    if after is None:
        return False
    return (after - before) < 0.5

def write_resume_hints(reaped):
    if not reaped:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## Reaped {stamp}\n"]
    for s in reaped:
        if s["session_id"]:
            lines.append(
                f"- `cd {s['cwd']} && claude --resume {s['session_id']}`  "
                f"(was {s['rss_mb']} MB, idle {s['idle_h']:.1f}h)"
            )
        else:
            lines.append(
                f"- {s['cwd']} -- no --resume id in argv; "
                f"recover via `claude --resume` and pick from the list "
                f"(was {s['rss_mb']} MB, idle {s['idle_h']:.1f}h)"
            )
    RESUME_HINTS.parent.mkdir(parents=True, exist_ok=True)
    with RESUME_HINTS.open("a") as fh:
        fh.write("\n".join(lines) + "\n")

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually signal the idle sessions (default is dry-run)")
    ap.add_argument("--idle-hours", type=float, default=3.0,
                    help="consecutive idle time before a session is reapable (default 3)")
    ap.add_argument("--min-rss", type=int, default=0,
                    help="only reap sessions above this RSS in MB")
    ap.add_argument("--keep", type=int, default=0,
                    help="always spare the N most recently active sessions")
    ap.add_argument("--list", action="store_true",
                    help="show every live session and exit")
    args = ap.parse_args()

    now = time.time()
    sessions = find_sessions()

    if args.list:
        total = sum(s["rss_mb"] for s in sessions)
        print(f"{len(sessions)} live Claude sessions, {total} MB total\n")
        for s in sorted(sessions, key=lambda x: -x["age_s"]):
            sid = s["session_id"] or "(no resume id)"
            print(f"  pid={s['pid']:<8} age={s['age_s']/3600:5.1f}h  "
                  f"rss={s['rss_mb']:4d}MB  {sid}  {s['cwd']}")
        return 0

    state = load_state()
    prev = state.get("procs", {})
    last_run = state.get("last_run", now)
    interval = max(now - last_run, 1.0)

    new_procs = {}
    candidates = []

    for s in sessions:
        key = str(s["pid"])
        old = prev.get(key)

        if old is None:
            # First time we have seen this pid; start its clock now.
            idle_seconds = 0.0
        else:
            cpu_delta = s["cpu"] - old.get("cpu", s["cpu"])
            # Scale the allowance to how long the gap actually was, so a late
            # cron run does not misread a busy session as quiet.
            allowance = QUIET_CPU_SECONDS * (interval / 900.0)
            if cpu_delta <= max(allowance, QUIET_CPU_SECONDS):
                idle_seconds = old.get("idle_seconds", 0.0) + interval
            else:
                idle_seconds = 0.0

        new_procs[key] = {
            "cpu": s["cpu"],
            "idle_seconds": idle_seconds,
            "session_id": s["session_id"],
            "cwd": s["cwd"],
        }
        s["idle_h"] = idle_seconds / 3600.0

        if s["idle_h"] >= args.idle_hours and s["rss_mb"] >= args.min_rss:
            candidates.append(s)

    # Spare the N most recently active, regardless of threshold.
    candidates.sort(key=lambda x: -x["idle_h"])
    if args.keep:
        spare = {c["pid"] for c in sorted(sessions, key=lambda x: x.get("idle_h", 0))[:args.keep]}
        candidates = [c for c in candidates if c["pid"] not in spare]

    total_mb = sum(s["rss_mb"] for s in sessions)
    print(f"{len(sessions)} live sessions, {total_mb} MB total. "
          f"{len(candidates)} idle >= {args.idle_hours}h.")

    if not candidates:
        state["procs"] = new_procs
        state["last_run"] = now
        save_state(state)
        return 0

    reaped = []
    for s in candidates:
        sid = s["session_id"] or "(no resume id)"
        idle = (f"{s['idle_h']:.1f}h" if s["idle_h"] >= 1
                else f"{s['idle_h'] * 60:.0f}m")
        label = f"pid={s['pid']} idle={idle} rss={s['rss_mb']}MB {sid}"

        if not args.apply:
            print(f"  WOULD REAP  {label}")
            continue

        if not confirm_still_quiet(s["pid"]):
            print(f"  SKIP (woke up)  {label}")
            new_procs[str(s["pid"])]["idle_seconds"] = 0.0
            continue

        try:
            os.kill(s["pid"], signal.SIGTERM)
        except ProcessLookupError:
            print(f"  GONE  {label}")
            continue
        except PermissionError:
            print(f"  DENIED  {label}")
            continue

        print(f"  REAPED  {label}")
        reaped.append(s)
        new_procs.pop(str(s["pid"]), None)

    if args.apply:
        write_resume_hints(reaped)
        freed = sum(s["rss_mb"] for s in reaped)
        if reaped:
            print(f"\nFreed ~{freed} MB. Resume commands: {RESUME_HINTS}")
    else:
        would = sum(s["rss_mb"] for s in candidates)
        print(f"\nDry run. Would free ~{would} MB. Re-run with --apply.")

    state["procs"] = new_procs
    state["last_run"] = now
    save_state(state)
    return 0

if __name__ == "__main__":
    sys.exit(main())
