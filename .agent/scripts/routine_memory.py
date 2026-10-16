#!/usr/bin/env python3
"""Routine memory: a routine reads its last note at the start and writes one at the end.

Each cron-style routine (daily-update, sweep, chase queue) repeats the same
searches every run because nothing carries over. This keeps one short "retain"
note per routine so the next run starts from what the last one learned.

    python3 .agent/scripts/routine_memory.py recall <routine>
    python3 .agent/scripts/routine_memory.py retain <routine> --text "what to remember"
    echo "text" | python3 .agent/scripts/routine_memory.py retain <routine> --text -

Storage: journal/state/routine_memory/<routine>.jsonl, one JSON object per line
({"ts", "routine", "text"}), newest last, only the last KEEP (20) lines kept.
Writes are atomic (tmp + os.replace) and run under the ledger lock when it is
importable. Override the directory with ROUTINE_MEMORY_DIR (tests).

recall prints the last note, or a one-line "none yet" message, and exits 0 so a
routine never fails because it has no memory.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

KEEP = 20
MAX_TEXT = 1500

HERE = Path(__file__).resolve().parent

def mem_dir():
    env = os.environ.get("ROUTINE_MEMORY_DIR")
    if env:
        return Path(env)
    return HERE.parent.parent / "journal" / "state" / "routine_memory"

def safe_routine(name):
    clean = re.sub(r"[^a-z0-9_-]+", "-", (name or "").strip().lower()).strip("-")
    if not clean:
        raise ValueError("routine name is empty")
    return clean

def mem_path(routine):
    return mem_dir() / (safe_routine(routine) + ".jsonl")

def read_notes(routine):
    path = mem_path(routine)
    notes = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("text"):
                    notes.append(obj)
    except OSError:
        pass
    return notes

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def write_notes(routine, notes):
    path = mem_path(routine)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for n in notes:
                f.write(json.dumps(n, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def retain(routine, text):
    text = (text or "").strip()
    if not text:
        raise ValueError("nothing to retain: --text is empty")
    if len(text) > MAX_TEXT:
        text = text[: MAX_TEXT - 3].rstrip() + "..."
    name = safe_routine(routine)
    lock = None
    try:
        sys.path.insert(0, str(HERE))
        from ledger_lock import ledger_lock  # type: ignore
        lock = ledger_lock("routine_memory_" + name)
    except Exception:
        lock = None
    entry = {"ts": now_iso(), "routine": name, "text": text}
    if lock is not None:
        with lock:
            notes = read_notes(name) + [entry]
            write_notes(name, notes[-KEEP:])
    else:
        notes = read_notes(name) + [entry]
        write_notes(name, notes[-KEEP:])
    return entry

def recall(routine, n=1):
    notes = read_notes(routine)
    if not notes:
        return "No retained note for %s yet." % safe_routine(routine)
    out = []
    for note in notes[-n:]:
        out.append("Last retain for %s (%s): %s" % (note.get("routine", routine), note.get("ts", "?"), note["text"]))
    return "\n".join(out)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("recall", help="print the last retain note for a routine")
    r.add_argument("routine")
    r.add_argument("-n", type=int, default=1, help="how many recent notes to print (default 1)")
    t = sub.add_parser("retain", help="store a note for a routine (keeps the last %d)" % KEEP)
    t.add_argument("routine")
    t.add_argument("--text", required=True, help="the note; '-' reads stdin")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        if args.cmd == "recall":
            print(recall(args.routine, max(1, args.n)))
            return 0
        text = sys.stdin.read() if args.text == "-" else args.text
        entry = retain(args.routine, text)
        print("Retained for %s at %s." % (entry["routine"], entry["ts"]))
        return 0
    except ValueError as e:
        print("routine_memory: %s" % e, file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
