#!/usr/bin/env python3
"""Shared contract for automation limits: what is held, why, and when it clears.

An automation that is switched off looks exactly like an automation with nothing
to do, and an automation that is throttling looks exactly like one that decided
the work was not worth doing. Both silences cost the same thing: the user stops
being able to tell a working feature from a broken one. The reply router sat off
for eleven days in September 2026 for precisely that reason.

So every automation writes the same `held` block into its own state file, and
every reader (the morning update today, the app's Settings pane later) reads that
one shape instead of learning each automation's private vocabulary.

    state["held"] = [
      {"reason": "cap_hour", "items": 30, "unit": "conversation",
       "disposition": "held", "clears_at": 1789000000,
       "setting": "max_per_hour", "since": 1788990000},
    ]

Two fields carry the weight.

`disposition` says what happened to the work: "held" means it is waiting and will
be picked up, "dropped" means it is gone and no later run will reconsider it. A
cap holds. A mute drops. Today those are indistinguishable from the outside,
which is how a muted channel reads as a quiet one.

`clears_at` says when the user gets their work back. A limit without it is the
difference between "wait twenty minutes" and "this is broken"; that is the whole
question the user is actually asking.

Write `held` on EVERY run, including the runs with nothing held. A field written
only when something is wrong goes stale the moment it is fixed, and then it
reports a problem that ended days ago.

Escalation. Self-clearing limits are normal and fire constantly, so surfacing
each one would train the user to ignore the surface, which costs more than it
buys. But a cap that has been holding the same backlog for a day is not smoothing
traffic, it is a backlog that never drains, and that is worth interrupting for.
So a self-clearing reason escalates once it has been continuously held past
PERSIST_ESCALATE_HOURS.

CLI:
    automation_settings.py list                # automations that declare a schema
    automation_settings.py show <id>           # settings, current values, usage, held
    automation_settings.py held                # every held block; exit 2 = needs attention
    automation_settings.py validate            # schema drift
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKILLS_DIR = os.path.join(BASE_DIR, ".agent", "skills")
SCHEMA_NAME = "settings_schema.json"

WIB = timezone(timedelta(hours=7))

# A self-clearing limit that has held the same work this long is no longer
# smoothing bursts; it is a backlog with no way out.
PERSIST_ESCALATE_HOURS = 24

# A routine declared enabled but silent for this many of its own intervals is
# not between ticks, it is not being ticked.
SILENT_INTERVALS = 3
SILENT_FLOOR_SECONDS = 3600

# reason -> does it clear on its own, and does the work survive it?
REASONS = {
    "switch_off":  {"self_clears": False, "disposition": "held"},
    "parent_off":  {"self_clears": False, "disposition": "held"},
    "needs_auth":  {"self_clears": False, "disposition": "held"},
    "cap_hour":    {"self_clears": True,  "disposition": "held"},
    "cap_day":     {"self_clears": True,  "disposition": "held"},
    "quiet_hours": {"self_clears": True,  "disposition": "held"},
    "debounce":    {"self_clears": True,  "disposition": "held"},
    "muted":       {"self_clears": False, "disposition": "dropped"},
}

SETTING_TYPES = {"int", "bool", "hour_range", "string_list", "enum"}

# ----------------------------------------------------------------- helpers --

def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def wib_now(now=None):
    return datetime.fromtimestamp(now or time.time(), WIB)

def next_hour_boundary(now=None):
    """Epoch of the next wall-clock hour: when an hourly cap frees up."""
    now = now or time.time()
    return (int(now) // 3600 + 1) * 3600

def next_wib_midnight(now=None):
    """Epoch of the next WIB midnight: when a daily cap frees up."""
    d = wib_now(now)
    nxt = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()

def quiet_hours_clear_at(quiet_hours, now=None):
    """Epoch when the quiet window ends. None when the window is not configured."""
    if not quiet_hours or len(quiet_hours) != 2:
        return None
    d = wib_now(now)
    end = d.replace(hour=quiet_hours[1] % 24, minute=0, second=0, microsecond=0)
    if end <= d:
        end += timedelta(days=1)
    return end.timestamp()

def fmt_clears(clears_at, now=None):
    """Human phrasing for when work comes back, in WIB."""
    if clears_at is None:
        return "does not clear on its own"
    now = now or time.time()
    mins = max(0, int((clears_at - now) // 60))
    stamp = datetime.fromtimestamp(clears_at, WIB).strftime("%H:%M WIB")
    if mins < 60:
        return "clears {} (in {}m)".format(stamp, mins)
    return "clears {} (in {}h{}m)".format(stamp, mins // 60, mins % 60)

# -------------------------------------------------------------- held block --

def held_record(reason, items, unit, setting=None, clears_at=None):
    """One held entry. Disposition comes from the reason, never from the caller.

    Letting a caller declare its own disposition is how "dropped" quietly starts
    meaning "held" in one automation and not another, which defeats the point of
    having one shape.
    """
    if reason not in REASONS:
        raise ValueError("unknown held reason: {}".format(reason))
    return {
        "reason": reason,
        "items": int(items),
        "unit": unit,
        "disposition": REASONS[reason]["disposition"],
        "clears_at": clears_at,
        "setting": setting,
        "since": None,   # filled by merge_held
    }

def merge_held(previous, current, now=None):
    """Carry `since` across runs so escalation measures a real duration.

    Stamping `since` fresh every run would make every limit look like it started
    a moment ago, and nothing would ever escalate.
    """
    now = now or time.time()
    prev_by_key = {(r.get("reason"), r.get("setting")): r for r in (previous or [])}
    out = []
    for rec in current:
        rec = dict(rec)
        old = prev_by_key.get((rec["reason"], rec["setting"]))
        rec["since"] = (old or {}).get("since") or now
        out.append(rec)
    return out

def is_escalated(rec, now=None):
    """True when this held block deserves to interrupt the user."""
    now = now or time.time()
    if not REASONS.get(rec.get("reason"), {}).get("self_clears", False):
        return rec.get("items", 0) > 0
    age_h = (now - (rec.get("since") or now)) / 3600
    return rec.get("items", 0) > 0 and age_h >= PERSIST_ESCALATE_HOURS

def describe_held(rec, label=None, now=None):
    """One line a human can act on: what, how much, why, and when it comes back."""
    who = "{}: ".format(label) if label else ""
    verb = "dropped" if rec["disposition"] == "dropped" else "held"
    tail = ("" if rec["disposition"] == "dropped"
            else ", {}".format(fmt_clears(rec.get("clears_at"), now)))
    detail = " ({})".format(rec["detail"]) if rec.get("detail") else ""
    return "{}{} {}(s) {} by {}{}{}".format(
        who, rec.get("items", 0), rec.get("unit", "item"), verb,
        rec.get("setting") or rec["reason"], tail, detail)

def parent_held(schema, now=None):
    """A `parent_off` block for a routine whose scheduler is not running it.

    This is computed by the READER, not written by the automation itself, and it
    has to be. An automation whose scheduler is stopped never runs, so it never
    gets the chance to report that it is not running. A child cannot witness its
    own absence, which is exactly why the app showed "Auto reply drafts: ON"
    under a "Scheduled runs: OFF" that made it inert, and nothing joined them.
    """
    now = now or time.time()
    job = schema.get("job")
    if not job:
        return None
    silent, detail = scheduler_silent(job, now)
    if not silent:
        return None
    dep = schema.get("depends_on") or {}
    rec = held_record("parent_off", 1, "routine",
                      setting=dep.get("label") or "the scheduler")
    rec["since"] = now
    rec["detail"] = "{}{}".format(
        detail, ". Turn on {} in {}".format(dep["label"], dep["where"])
        if dep.get("label") and dep.get("where") else "")
    return rec

# ------------------------------------------------------------ parent state --

ROUTINES_PATH = os.path.join(BASE_DIR, "journal", "state", "routines.json")

# The app's scheduler writes runs.jsonl into the workspace it has open, which is
# not necessarily this checkout. Reading only this one reported "never ran" about
# a routine that had just run, in the app, minutes earlier. A false alarm about
# silence is worse than no alarm: it teaches the reader to discount the signal
# that this whole contract exists to produce. So look in every checkout.
RUNS_ROOTS = (
    BASE_DIR,
    ".",
    ".",
    "C:/Users/you/.gemini/antigravity/scratch/product-second-brain",
    # The same Windows checkout as seen from WSL. Both spellings are needed:
    # a WSL process cannot open "C:/...", and a Windows one cannot open "/mnt/c".
    "/mnt/c/Users/you/.gemini/antigravity/scratch/product-second-brain",
    "//wsl.localhost/Ubuntu.",
)

def cron_interval_seconds(cron):
    """Roughly how often a cron line fires. Rough is enough: this only sets the
    window after which silence stops being normal."""
    if not cron:
        return None
    parts = cron.split()
    if len(parts) < 5:
        return None
    minute, hour = parts[0], parts[1]
    if minute.startswith("*/"):
        try:
            return int(minute[2:]) * 60
        except ValueError:
            return None
    if hour.startswith("*/"):
        try:
            return int(hour[2:]) * 3600
        except ValueError:
            return None
    if hour == "*":
        return 3600
    return 86400

def routine_row(job):
    data = _load_json(ROUTINES_PATH, {})
    for row in data.get("routines", []):
        if row.get("job") == job:
            return row
    return None

def last_run_at(job):
    """Epoch of the last recorded run of an app routine across every checkout.

    A `missed` event is not a run: it is the scheduler recording that it could
    not fire. Counting it would report a healthy routine every time the app
    stayed shut, which is the one case worth reporting.
    """
    newest = None
    seen = set()
    for root in RUNS_ROOTS:
        path = os.path.join(root, "journal", "state", "runs.jsonl")
        real = os.path.normpath(path)
        if real in seen:
            continue
        seen.add(real)
        try:
            with open(path) as f:
                for line in f:
                    if job not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("job") != job or rec.get("event") == "missed":
                        continue
                    at = rec.get("at_ms")
                    if at and (newest is None or at > newest):
                        newest = at
        except (FileNotFoundError, NotADirectoryError, OSError):
            continue
    return newest / 1000.0 if newest else None

def scheduler_silent(job, now=None):
    """Is this routine declared on, but not actually being run by anything?

    The repo cannot read the app's own master switch for scheduled runs, so
    asking it directly is not an option. But a routine that says it fires every
    fifteen minutes and has no recorded run for hours is evidence either way,
    and it catches every reason the scheduler might be stopped rather than only
    the one switch. Returns (silent, detail) where detail is human-facing.
    """
    now = now or time.time()
    row = routine_row(job)
    if not row or row.get("runner") != "app" or not row.get("enabled", True):
        return False, None
    interval = cron_interval_seconds(row.get("cron")) or 86400
    tolerance = max(SILENT_INTERVALS * interval, SILENT_FLOOR_SECONDS)
    last = last_run_at(job)
    if last is None:
        return True, ("declared to run {}, but no run has ever been recorded"
                      .format(row.get("schedule") or row.get("cron")))
    age_h = (now - last) / 3600
    if now - last > tolerance:
        return True, ("declared to run {}, but the last recorded run was {:.0f}h ago"
                      .format(row.get("schedule") or row.get("cron"), age_h))
    return False, None

# ----------------------------------------------------------------- schemas --

def load_schemas():
    """Every skill that declares its settings, keyed by automation id."""
    out = {}
    for path in sorted(glob.glob(os.path.join(SKILLS_DIR, "*", SCHEMA_NAME))):
        data = _load_json(path, None)
        if not data or not data.get("automation_id"):
            continue
        data["_schema_path"] = path
        out[data["automation_id"]] = data
    return out

def validate_schema(schema):
    """Problems that would make the Settings pane render a control wrong."""
    problems = []
    aid = schema.get("automation_id", "?")
    for field in ("label", "config_path", "config_key", "state_path", "settings"):
        if not schema.get(field):
            problems.append("{}: missing '{}'".format(aid, field))
    for p in ("config_path", "state_path"):
        rel = schema.get(p)
        if rel and not os.path.exists(os.path.join(BASE_DIR, rel)):
            problems.append("{}: {} points at a file that does not exist ({})".format(aid, p, rel))
    # A routine that the app schedules MUST name its parent. Without it the pane
    # can render a child as ON while the switch above it makes it inert, which is
    # the failure this whole contract exists to make impossible.
    if schema.get("job") and routine_row(schema["job"]):
        row = routine_row(schema["job"])
        dep = schema.get("depends_on") or {}
        if row.get("runner") == "app":
            for f in ("label", "where"):
                if not dep.get(f):
                    problems.append("{}: runner is 'app' but depends_on.{} is missing, so "
                                    "the UI cannot name what gates it".format(aid, f))
    elif schema.get("job"):
        problems.append("{}: job '{}' is not declared in routines.json".format(
            aid, schema["job"]))
    for s in schema.get("settings", []):
        key = s.get("key", "?")
        if s.get("type") not in SETTING_TYPES:
            problems.append("{}.{}: type '{}' is not one of {}".format(
                aid, key, s.get("type"), sorted(SETTING_TYPES)))
        # help is mandatory, and it has to answer the question the user has:
        # does my work wait, or is it gone?
        help_text = (s.get("help") or "").lower()
        if not help_text:
            problems.append("{}.{}: no help text".format(aid, key))
        elif not any(w in help_text for w in ("wait", "held", "hold", "drop", "skip",
                                              "tunggu", "ditahan", "dibuang", "dilewati")):
            problems.append("{}.{}: help does not say what happens to work that hits "
                            "this limit (held or dropped)".format(aid, key))
    return problems

def read_config(schema):
    cfg = _load_json(os.path.join(BASE_DIR, schema["config_path"]), {})
    return cfg.get(schema["config_key"], {})

def read_held(schema, now=None):
    """The automation's own held blocks, plus the one it cannot write itself."""
    state = _load_json(os.path.join(BASE_DIR, schema["state_path"]), {})
    out = list(state.get("held") or [])
    parent = parent_held(schema, now)
    if parent:
        # First: a stopped scheduler makes every limit below it moot, and
        # reading about a cap before reading that nothing runs is misleading.
        out.insert(0, parent)
    return out

# -------------------------------------------------------------- commands --

def cmd_list(args):
    schemas = load_schemas()
    if not schemas:
        print("no automation declares {} yet".format(SCHEMA_NAME))
        return 0
    for aid, s in schemas.items():
        print("{:<16} {:<24} {} setting(s)".format(aid, s["label"], len(s["settings"])))
    return 0

def cmd_show(args):
    schemas = load_schemas()
    s = schemas.get(args.automation_id)
    if not s:
        print("unknown automation '{}'. Known: {}".format(
            args.automation_id, ", ".join(sorted(schemas)) or "(none)"))
        return 1
    cfg = read_config(s)
    state = _load_json(os.path.join(BASE_DIR, s["state_path"]), {})
    usage = state.get("usage") or {}
    now = time.time()
    print("{} ({})".format(s["label"], s["automation_id"]))
    for setting in s["settings"]:
        key = setting["key"]
        line = "  {:<22} {}".format(key, cfg.get(key, setting.get("default")))
        if setting.get("usage_key") and setting["usage_key"] in usage:
            line += "   (used {} of {})".format(usage[setting["usage_key"]], cfg.get(key))
        print(line)
        print("      {}".format(setting.get("help", "")))
    held = read_held(s)
    print("  held: {}".format("(nothing)" if not held else ""))
    for rec in held:
        print("    {}{}".format(describe_held(rec, now=now),
                                "   [NEEDS ATTENTION]" if is_escalated(rec, now) else ""))
    return 0

def cmd_held(args):
    """Every held block across every automation. Exit 2 = something needs a human."""
    schemas = load_schemas()
    now = time.time()
    lines, escalated = [], []
    for aid, s in sorted(schemas.items()):
        for rec in read_held(s, now):
            if not rec.get("items"):
                continue
            line = describe_held(rec, s["label"], now)
            lines.append(line)
            if is_escalated(rec, now):
                escalated.append(line)
    if not lines:
        print("nothing held across {} automation(s)".format(len(schemas)))
        return 0
    for line in lines:
        print(("! " if line in escalated else "  ") + line)
    return 2 if escalated else 0

def cmd_validate(args):
    schemas = load_schemas()
    problems = []
    for s in schemas.values():
        problems.extend(validate_schema(s))
    if problems:
        for p in problems:
            print("- " + p)
        return 1
    print("{} schema(s) valid".format(len(schemas)))
    return 0

def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="automations that declare settings")
    sh = sub.add_parser("show", help="settings, values, usage, held")
    sh.add_argument("automation_id")
    sub.add_parser("held", help="every held block; exit 2 = needs attention")
    sub.add_parser("validate", help="schema drift")
    args = p.parse_args()
    return {"list": cmd_list, "show": cmd_show,
            "held": cmd_held, "validate": cmd_validate}[args.cmd](args)

if __name__ == "__main__":
    sys.exit(main())
