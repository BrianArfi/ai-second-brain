#!/usr/bin/env python3
"""Report whether one day's auto reply drafts all sit under ONE day container.

The ASB app decides where a branch session lands: it writes `parentSessionId` on
every branch in `branches.json`. Until 14:26 WIB on 16 Sep 2026 it parented a
draft to whatever chat session happened to be live, so a single day's drafts got
split across a real conversation and the day container. After that time the app
started creating `auto-drafts-day` containers instead.

Nothing in this repo controls that parenting, so this script does not fix it. It
only answers the one question worth asking each morning: did today's drafts land
in one place, or did they scatter again?

Usage:
    python3 .agent/scripts/check_autodraft_grouping.py            # today (WIB)
    python3 .agent/scripts/check_autodraft_grouping.py 2026-09-16
    python3 .agent/scripts/check_autodraft_grouping.py --exit-zero

Exit code 0 = one container, 1 = scattered (or nothing found). Pass --exit-zero
to always exit 0: the morning runner reads the verdict from the text, and a
non-zero exit there reads as "the step crashed" instead of "the drafts
scattered", which files a phantom error against a step that worked.
"""
import collections
import datetime
import json
import os
import sys

WIB = datetime.timezone(datetime.timedelta(hours=7))

def app_dir():
    for base in (os.environ.get("APPDATA"), os.path.expanduser("~/AppData/Roaming")):
        if not base:
            continue
        d = os.path.join(base, "com.aisecondbrain.desktop")
        if os.path.isdir(d):
            return d
    return None

def load(base, name):
    with open(os.path.join(base, name), encoding="utf-8") as fh:
        return json.load(fh)

def main():
    argv = [a for a in sys.argv[1:] if a != "--exit-zero"]
    exit_zero = "--exit-zero" in sys.argv[1:]
    day = argv[0] if argv else datetime.datetime.now(WIB).strftime("%Y-%m-%d")
    base = app_dir()
    if base is None:
        # The automation host is WSL and the app state lives on Windows. Not an
        # error: the check simply has nothing to read here.
        print("Not applicable on this machine (ASB app state is on Windows).")
        return 0

    sessions = {s["sessionId"]: s for s in load(base, "sessions.json")}
    branches = [
        b for b in load(base, "branches.json")
        if b.get("title", "").startswith("Reply:")
        and datetime.datetime.fromtimestamp(b["createdAtMs"] / 1000, WIB).strftime("%Y-%m-%d") == day
    ]

    if not branches:
        print("No auto reply drafts on {}.".format(day))
        return 0 if exit_zero else 1

    counts = collections.Counter(b.get("parentSessionId") for b in branches)
    print("{}: {} draft(s) across {} parent session(s)".format(day, len(branches), len(counts)))
    for pid, n in counts.most_common():
        s = sessions.get(pid, {})
        kind = s.get("containerKind") or "chat session (NOT a day container)"
        print("  {:3d}  {}  {!r}  [{}]".format(n, (pid or "?")[:8], s.get("title"), kind))

    scattered = len(counts) > 1 or any(
        not sessions.get(pid, {}).get("containerKind") for pid in counts
    )
    print("\n" + ("SCATTERED - drafts did not all land in one day container."
                  if scattered else "OK - all drafts in one day container."))
    return 0 if (exit_zero or not scattered) else 1

if __name__ == "__main__":
    sys.exit(main())
