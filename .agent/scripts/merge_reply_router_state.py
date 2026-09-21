#!/usr/bin/env python3
"""Resolve a merge/rebase conflict in journal/state/reply_router_state.json.

Both sides are full snapshots of the router's state, so git cannot merge them
and picking a side loses work: dropping a `processed` entry makes the router
re-dispatch a message it already handled, which opens a duplicate draft session.

The merge is a union instead:
  conversations -> per key, keep the record with the later `last_activity_at`
  processed     -> union (an entry only ever means "already handled")
  usage         -> max per counter, so a cap is never under-counted
  held          -> whichever side still has a pending block

Run with no arguments while the conflict is staged:

    python3 .agent/scripts/merge_reply_router_state.py
    git add journal/state/reply_router_state.json
    git rebase --continue
"""

import json
import os
import subprocess
import sys

PATH = "journal/state/reply_router_state.json"

def stage(n):
    out = subprocess.run(
        ["git", "show", ":{}:{}".format(n, PATH)],
        capture_output=True,
    )
    if out.returncode != 0:
        sys.exit("no stage {} for {} -- is the conflict staged?".format(n, PATH))
    return json.loads(out.stdout.decode("utf-8"))

def merge(a, b):
    out = dict(a)

    conv = dict(a.get("conversations", {}))
    for key, rec in b.get("conversations", {}).items():
        cur = conv.get(key)
        if cur is None or rec.get("last_activity_at", 0) > cur.get("last_activity_at", 0):
            conv[key] = rec
    out["conversations"] = conv

    processed = dict(a.get("processed", {}))
    processed.update(b.get("processed", {}))
    out["processed"] = processed

    ua, ub = a.get("usage", {}), b.get("usage", {})
    out["usage"] = {k: max(ua.get(k, 0), ub.get(k, 0)) for k in set(ua) | set(ub)}

    out["held"] = a.get("held") or b.get("held") or []
    return out

def main():
    ours, theirs = stage(2), stage(3)
    merged = merge(ours, theirs)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(
        "[merge-rr] conversations {} processed {} usage {} held {}".format(
            len(merged["conversations"]),
            len(merged["processed"]),
            merged["usage"],
            len(merged["held"]),
        )
    )
    return 0

if __name__ == "__main__":
    os.chdir(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
        ).stdout.strip()
    )
    sys.exit(main())
