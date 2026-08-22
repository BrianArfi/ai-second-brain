#!/usr/bin/env python3
"""Throttle the cron fleet so it cannot starve interactive work in WSL.

Why this exists: the crontab holds 128 jobs, and the schedules cluster hard on
round minutes. At :00 up to 78 job instances fire in the same minute, at :30 up
to 53. Each one is a fresh python3 interpreter, most of them doing network or
LLM calls. That burst saturates all 12 vCPUs, and while it lasts the VS Code
server and the Claude panels get no scheduler time, which is what makes WSL look
like it died (diagnosed 7 Aug 2026).

Two independent levers, applied together:

  nice   -- every cron job runs at nice 19 / ionice idle. Total CPU used does not
            drop, but interactive processes now win every scheduling contest, so
            a burst costs throughput instead of responsiveness.
  spread -- jobs on a */N schedule get a deterministic per-job offset so a */30
            fleet lands across :00-:29 instead of all on :00. Same frequency,
            same number of runs per hour, just not simultaneous.

Both rewrites are idempotent: running twice changes nothing the second time.
The time fields and the command are never reinterpreted beyond what is described
above, and comments, blank lines, and VAR=value lines pass through untouched.

Default mode is --dry-run. Nothing is installed unless you pass --apply, and
--apply always writes a timestamped backup first.

Usage:
    cron_throttle.py                          # show the diff, change nothing
    cron_throttle.py --apply                  # install (backup written first)
    cron_throttle.py --nice-only              # skip the schedule spreading
    cron_throttle.py --restore <backup-file>  # put a backup back
"""

import argparse
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path

BACKUP_DIR = Path.home() / ".cache" / "cron_throttle_backups"

# Prefix inserted between the schedule fields and the command.
#
# It renices the shell cron already spawned rather than wrapping the command in
# `nice`. Two reasons that matters here: a third of these lines start with `cd`,
# which is a shell builtin that `nice` cannot execute, and many chain several
# commands with && where a leading `nice` would only cover the first one.
# Renicing $$ sidesteps both -- nice value and ionice class are inherited by
# every child the job goes on to spawn, whatever its shape, and nothing about
# the original command text has to be parsed or requoted.
#
#   renice -n 19   lowest CPU priority; any interactive process preempts it
#   ionice -c3     idle I/O class; disk access yields to everything else
NICE_PREFIX = ("renice -n 19 -p $$ >/dev/null 2>&1; "
               "ionice -c3 -p $$ >/dev/null 2>&1; ")

# Schedules we are willing to spread. A bare */N starting at 0 is the pattern
# that stacks; anything already offset (8-59/15) is left alone.
STEP_RE = re.compile(r"^\*/(\d+)$")

# Only spread when the step is big enough that an offset is meaningful.
MIN_STEP_TO_SPREAD = 10

CRON_VAR_RE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=")

def read_crontab() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode != 0 and not r.stdout:
        sys.exit(f"could not read crontab: {r.stderr.strip()}")
    return r.stdout

def split_schedule(line: str):
    """Return (fields, command) for a job line, or None if it is not a job.

    Handles the 5-field form and the @reboot/@daily shorthand.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or CRON_VAR_RE.match(line):
        return None
    if stripped.startswith("@"):
        parts = stripped.split(None, 1)
        if len(parts) < 2:
            return None
        return [parts[0]], parts[1]
    parts = stripped.split(None, 5)
    if len(parts) < 6:
        return None
    return parts[:5], parts[5]

def job_offset(command: str, step: int) -> int:
    """Deterministic offset in [0, step) derived from the command itself.

    Deterministic matters: rerunning the tool must not reshuffle the fleet, and
    a job keeps its slot across edits to unrelated lines.
    """
    return zlib.crc32(command.encode()) % step

def spread_minute(field: str, command: str) -> str:
    m = STEP_RE.match(field)
    if not m:
        return field
    step = int(m.group(1))
    if step < MIN_STEP_TO_SPREAD or step > 59:
        return field
    off = job_offset(command, step)
    if off == 0:
        return field
    # "7-59/30" fires at :07 and :37 -- same cadence as */30, shifted by 7.
    return f"{off}-59/{step}"

def transform(text: str, do_nice: bool, do_spread: bool):
    out, changed = [], 0
    for line in text.splitlines():
        parsed = split_schedule(line)
        if parsed is None:
            out.append(line)
            continue
        fields, command = parsed
        new_command = command
        if do_nice and not command.startswith(NICE_PREFIX.strip()):
            new_command = NICE_PREFIX + command
        new_fields = list(fields)
        if do_spread and len(fields) == 5:
            new_fields[0] = spread_minute(fields[0], command)
        new_line = " ".join(new_fields) + " " + new_command
        if new_line != line.strip():
            changed += 1
        out.append(new_line)
    return "\n".join(out) + "\n", changed

def install(text: str) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"crontab.{stamp}.txt"
    backup.write_text(read_crontab())
    print(f"backup written: {backup}")

    r = subprocess.run(["crontab", "-"], input=text, text=True,
                       capture_output=True)
    if r.returncode != 0:
        sys.exit(f"crontab install FAILED (backup intact at {backup}):\n{r.stderr}")

    # Verify by reading back, not by trusting the exit code.
    after = read_crontab()
    before_jobs = sum(1 for l in text.splitlines() if split_schedule(l))
    after_jobs = sum(1 for l in after.splitlines() if split_schedule(l))
    if before_jobs != after_jobs:
        sys.exit(f"VERIFY FAILED: wrote {before_jobs} jobs, read back {after_jobs}. "
                 f"Restore with: crontab {backup}")
    print(f"verified: {after_jobs} jobs installed")

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="install (default is dry-run)")
    ap.add_argument("--nice-only", action="store_true", help="do not spread schedules")
    ap.add_argument("--spread-only", action="store_true", help="do not add nice/ionice")
    ap.add_argument("--restore", metavar="FILE", help="reinstall a backup file")
    args = ap.parse_args()

    if args.restore:
        path = Path(args.restore)
        subprocess.run(["crontab", str(path)], check=True)
        print(f"restored from {path}")
        return

    do_nice = not args.spread_only
    do_spread = not args.nice_only

    current = read_crontab()
    new, changed = transform(current, do_nice, do_spread)

    if not changed:
        print("nothing to change -- already throttled")
        return

    print(f"{changed} job line(s) would change "
          f"(nice={do_nice}, spread={do_spread})\n")
    for a, b in zip(current.splitlines(), new.splitlines()):
        if a.strip() != b.strip():
            print(f"  - {a.strip()[:150]}")
            print(f"  + {b.strip()[:150]}\n")

    if args.apply:
        install(new)
    else:
        print("dry-run only. Re-run with --apply to install.")

if __name__ == "__main__":
    main()
