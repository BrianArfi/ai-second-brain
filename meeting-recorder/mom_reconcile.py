#!/usr/bin/env python3
"""Reconcile Fathom recordings against MOM files, and alarm on the gap.

Why this exists
---------------
Every other part of the meeting pipeline is capture-artifact-driven: the
watcher loops over .wav files that appeared, and the Vexa dispatcher fires a
bot and never looks back. Both are structurally blind to the meetings they
missed, because an empty recordings dir is indistinguishable from a day with
no meetings. On 16 Jul 2026 that dropped YourManager's mandatory Product/Growth/PMO
weekly (62 min, 12 decisions) while vexa-auto logged 19 "ok" heartbeats.

This script inverts the question. Instead of "an artifact appeared, what do I
do with it?" it asks "a meeting happened, where is its MOM?" and it asks it
against Fathom, the only record of what happened that this harness does not
produce itself. It fires on ABSENCE, which nothing else here can do.

The day is enumerated from LIVE Fathom, not from the local registry. The
registry (journal/fathom_registry.json) is a downstream artifact written only
by the evening sync, so mid-day it holds zero rows for today and the checker
went blind. We now query Fathom directly for the day and keep the registry
purely as an enrichment cache (matched_meeting title, related_recordings,
mom_path backlinks). If live Fathom is unreachable we mark the run
non-authoritative and exit 2 rather than reporting a false clean day.

Coverage for a Fathom recording resolves in this order:
  1. its own mom_path exists on disk
  2. any related_recordings entry has a mom_path on disk
  3. any registry row sharing date_wib + matched_meeting has a mom_path
     (Fathom / Vexa / local all land in the registry, so this is the dedupe key)
  4. a MOM_<Title>_<date>.md filed under Clients/Work/**/meetings/ fuzzy-matches

A MOM that exists but is tiny, or is filed under a non-meeting calendar block,
counts as SUSPECT rather than covered: a false-positive MOM reads as done and
hides the miss, which is worse than an outright gap. Size is also not proof
of substance: a MOM that clears the byte threshold but contains zero
decisions and zero action items is downgraded to SUSPECT too (an 11-minute
Vexa draft cleared MIN_MOM_BYTES by 156 bytes on 30 Jul 2026 while capturing
nothing). A recording that ended less than the grace window ago has
legitimately not been minuted yet and is bucketed as PENDING, which does NOT
trip the gap alarm.

Fathom enumeration is still blind to a meeting nobody ever hit record on --
it produces zero rows, which looks identical to no meeting having happened.
So the day is enumerated a second time from Google Calendar (best-effort: a
calendar-fetch failure does not affect authoritativeness, only Fathom does),
and any substantial calendar event that matches no Fathom row lands in
UNCOUNTED, which also trips the gap alarm. Root-caused on 30 Jul 2026: 7
calendar meetings, only 5 had notes, and 2 (ExampleCo daily standup, Product x
Ops Weekly) were never recorded and never appeared as a gap.

Usage:
  python3 meeting-recorder/mom_reconcile.py [--date YYYY-MM-DD] [--quiet]
Exit codes: 0 = fully covered, 1 = gaps found, 2 = could not run / verify.
"""
import argparse
import datetime
import difflib
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(MODULE_DIR)
REGISTRY_PATH = os.path.join(REPO_ROOT, "journal", "fathom_registry.json")
# Real minutes are filed per project, e.g. Clients/Work/<Project>/meetings/,
# not just the top-level meetings dir. The recursive glob matches both.
MEETINGS_GLOB = os.path.join(REPO_ROOT, "Clients", "Work", "**", "meetings", "*.md")
SYNC_SCRIPT = os.path.join(REPO_ROOT, "scripts", "fathom_registry_sync.py")
OUT_PATH = os.path.join(REPO_ROOT, "journal", "state", "mom_coverage.json")
HEARTBEAT = os.path.join(REPO_ROOT, ".agent", "scripts", "heartbeat.py")
GCAL_SCRIPT = os.path.join(REPO_ROOT, ".agent", "skills", "google-calendar-connector", "gcal_manager.py")

WIB = datetime.timezone(datetime.timedelta(hours=7))
JOB = "mom-reconcile"

# A MOM smaller than this is narration, a stub, or a prayer transcript, not
# minutes. The real 16 Jul files run 10-31 KB; the prayer false-positive was 768 B.
MIN_MOM_BYTES = 2048

# A recording that ended less than this many minutes ago has legitimately not
# been minuted yet: it is PENDING, not MISSING, and must not trip the alarm.
GRACE_MINUTES = 90

# Safety cap on live Fathom pagination when enumerating a single day.
LIVE_MAX_PAGES = 20

# Calendar blocks shorter than this clear the title filter but are not
# substantial enough to need a MOM of their own (a 10-minute placeholder hold).
MIN_EVENT_MINUTES = 15

# Fathom bots typically join a few minutes after the calendar start and the
# recording can end a few minutes either side of the invite; a Fathom row is
# treated as covering a calendar event if their time ranges land within this
# many minutes of overlap.
TIME_OVERLAP_BUFFER_MIN = 20

NON_MEETING_TITLES = {
    "prayer", "focus time", "home", "lunch", "break", "ooo",
    "out of office", "leave", "holiday", "travel",
}

# This checker only guards Clients/Work/**/meetings/, but live Fathom
# enumerates ALL of the owner's recordings. Personal/other-client meetings (their
# MOMs live in the You repo or ClientB flow) must not trip the Work gap alarm
# every day. A registry `client` other than Work is authoritative; for rows
# the registry has not seen yet, these title tokens mark known personal work.
PERSONAL_TITLE_TOKENS = {
    "tln", "you", "goakal", "hsi", "podcast", "qawwam", "skripsi",
}
PERSONAL_TITLE_PHRASES = ("ai circle", "suami qawwam", "bimbingan skripsi")

# Narration the agentic draft CLI prints to stdout when it writes the real
# minutes into its own sandbox workspace instead of the repo.
STUB_MARKERS = (
    "/.gemini/antigravity-cli/brain/",
    "I have created the meeting minutes",
    "I have created the Meeting Minutes",
    "Summary of Work:",
)

def heartbeat(status, summary):
    try:
        subprocess.run([sys.executable, HEARTBEAT, "--job", JOB,
                        "--status", status, "--summary", summary[:500]],
                       capture_output=True, timeout=30)
    except Exception:
        pass

def today_wib():
    return datetime.datetime.now(WIB).strftime("%Y-%m-%d")

def load_registry():
    """The registry is now an enrichment cache only, never the enumerator, so a
    missing or malformed file degrades to empty rather than aborting the run."""
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def _parse_utc(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None

def _wib_date(s):
    dt = _parse_utc(s)
    return dt.astimezone(WIB).strftime("%Y-%m-%d") if dt else ""

def _wib_hhmm(s):
    dt = _parse_utc(s)
    return dt.astimezone(WIB).strftime("%H:%M") if dt else ""

def _load_sync_module():
    """Load scripts/fathom_registry_sync.py to reuse its token loader and paged
    Fathom fetch. That module owns the API contract; we do not duplicate it."""
    spec = importlib.util.spec_from_file_location("fathom_registry_sync", SYNC_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _real_title(meeting):
    """Live Fathom titles impromptu meets as "Impromptu Google Meet Meeting",
    which is a placeholder, not a real title. Registry enrichment supplies the
    calendar-resolved name; fall back to the raw live title otherwise."""
    return meeting.get("title") or meeting.get("meeting_title")

def _entry_from_live(meeting, registry):
    """Build a reconcile entry from a live Fathom recording, enriched from the
    registry cache (matched_meeting title, related_recordings, mom_path)."""
    rid = str(meeting.get("recording_id") or meeting.get("id"))
    start = meeting.get("recording_start_time") or meeting.get("scheduled_start_time")
    end = meeting.get("recording_end_time") or meeting.get("scheduled_end_time")
    reg = registry.get(rid) or registry.get(meeting.get("recording_id")) or {}
    entry = {
        "recording_id": rid,
        "fathom_url": meeting.get("url") or meeting.get("share_url") or reg.get("fathom_url"),
        "date_wib": _wib_date(start),
        "time_wib": _wib_hhmm(start),
        "recording_end_utc": end,
        # Prefer the registry's calendar-resolved title over the live placeholder.
        "matched_meeting": reg.get("matched_meeting") or _real_title(meeting),
        "raw_title": _real_title(meeting),
        "related_recordings": reg.get("related_recordings"),
        "mom_path": reg.get("mom_path"),
        "client": reg.get("client"),
    }
    return rid, entry

def fetch_live_recordings(date, registry):
    """Enumerate the day directly from live Fathom.

    Returns (rows, error). rows is a list of (rec_id, entry) for recordings
    whose WIB date == date. error is None on success, or a short string when
    Fathom was unreachable (no token, HTTP error, timeout) so the caller can
    mark the run non-authoritative instead of reporting a false clean day.
    """
    try:
        sync = _load_sync_module()
    except Exception as e:
        return [], f"cannot load fathom sync module: {e}"
    try:
        token = sync.load_fathom_token()
    except Exception as e:
        return [], f"cannot load fathom token: {e}"
    if not token:
        return [], "no FATHOM_API_KEY / token.env"

    rows, cursor, pages, seen_older, exhausted = [], None, 0, False, False
    try:
        while pages < LIVE_MAX_PAGES:
            data = sync.fathom_get(token, cursor)
            items = data.get("items", [])
            pages += 1
            for m in items:
                d = _wib_date(m.get("recording_start_time") or m.get("scheduled_start_time"))
                if d == date:
                    rows.append(_entry_from_live(m, registry))
                elif d and d < date:
                    # Fathom returns newest-first, so once a page reaches a day
                    # older than the target, no later page can hold the target.
                    seen_older = True
            cursor = data.get("next_cursor")
            if not cursor:
                exhausted = True
                break
            if seen_older:
                break
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as e:
        return [], f"fathom fetch failed: {e}"

    # We only know we saw the WHOLE target day if we paged past it (seen_older)
    # or ran out of recordings entirely (exhausted). Hitting the page cap first,
    # with no target rows found, means a deep --date backfill outran the walk:
    # that is "cannot verify", not an authoritative empty day.
    if not rows and not seen_older and not exhausted:
        return [], (f"paged {LIVE_MAX_PAGES} times without reaching {date}; "
                    "raise LIVE_MAX_PAGES or narrow the date")
    return rows, None

def _compute_gcal_window(date):
    """gcal_manager's --days-back/--days-forward is relative to `now`, not an
    absolute date, so a --date backfill needs the offset pair translated to
    bracket the target day rather than today."""
    today = datetime.datetime.strptime(today_wib(), "%Y-%m-%d").date()
    target = datetime.datetime.strptime(date, "%Y-%m-%d").date()
    diff = (today - target).days
    if diff >= 0:
        return diff + 1, 0
    return 0, (-diff) + 1

def fetch_calendar_events(date):
    """Second enumeration source, alongside live Fathom (see module docstring).
    Returns (events, error); error is None on success, or a short string when
    the calendar could not be reached."""
    days_back, days_forward = _compute_gcal_window(date)
    try:
        proc = subprocess.run(
            [sys.executable, GCAL_SCRIPT, "list",
             "--days-back", str(days_back), "--days-forward", str(days_forward),
             "--profile", "work", "--json"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        return [], f"cannot run gcal_manager: {e}"
    if proc.returncode != 0:
        return [], f"gcal_manager exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
    try:
        events = json.loads(proc.stdout)
    except ValueError as e:
        return [], f"gcal_manager returned non-JSON: {e}"

    return [e for e in events
            if (e.get("status") or "").lower() != "cancelled"
            and _wib_date(e.get("start")) == date], None

def _is_non_substantial_calendar_title(title):
    """Personal, prayer, and focus-time blocks are not meetings needing a MOM.
    Reuses the same title heuristics reconcile() already applies to Fathom
    rows (NON_MEETING_TITLES, PERSONAL_TITLE_PHRASES/TOKENS) so calendar
    enumeration does not invent gaps for non-meeting calendar time."""
    t = (title or "").strip().lower()
    if not t or t in NON_MEETING_TITLES:
        return True
    if any(p in t for p in PERSONAL_TITLE_PHRASES):
        return True
    tokens = set(re.split(r"[^a-z0-9]+", t))
    return bool(tokens & PERSONAL_TITLE_TOKENS)

def _event_duration_minutes(start, end):
    s, e = _parse_utc(start), _parse_utc(end)
    if not s or not e:
        return None
    return (e - s).total_seconds() / 60.0

def _substantial_calendar_events(events):
    """Drop non-meeting titles and sub-MIN_EVENT_MINUTES blocks a title match
    alone would miss. All-day / dateless entries have no reliable duration and
    are dropped as a side effect of requiring a time-of-day component."""
    kept = []
    for e in events:
        if _is_non_substantial_calendar_title(e.get("summary")):
            continue
        start, end = e.get("start") or "", e.get("end") or ""
        if "T" not in start or "T" not in end:
            continue
        duration = _event_duration_minutes(start, end)
        if duration is not None and duration < MIN_EVENT_MINUTES:
            continue
        kept.append(e)
    return kept

def _minutes_since_midnight(hhmm):
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (AttributeError, ValueError):
        return None

def _fathom_matches_calendar_event(event, rows):
    """True if some Fathom row this day is plausibly the same meeting: a high
    title-similarity score (the same Jaccard/ratio blend and MATCH_THRESHOLD
    bar used for filename matching) AND overlapping wall-clock time -- title
    alone would let two same-day meetings sharing a generic name ("Weekly
    Sync") cross-match."""
    want = set(slug_tokens(event.get("summary") or ""))
    ev_start = _minutes_since_midnight(_wib_hhmm(event.get("start")))
    ev_end = _minutes_since_midnight(_wib_hhmm(event.get("end"))) or ev_start
    for _rec_id, entry in rows:
        have = set(slug_tokens(title_of(entry)))
        if not want or not have:
            continue
        jaccard = len(want & have) / len(want | have)
        ratio = difflib.SequenceMatcher(None, " ".join(sorted(want)),
                                        " ".join(sorted(have))).ratio()
        if max(jaccard, ratio) < MATCH_THRESHOLD:
            continue
        rec_start = _minutes_since_midnight(entry.get("time_wib"))
        if rec_start is None or ev_start is None:
            continue
        rec_end = _minutes_since_midnight(_wib_hhmm(entry.get("recording_end_utc"))) or rec_start
        if (rec_start - TIME_OVERLAP_BUFFER_MIN) <= ev_end and \
           (ev_start - TIME_OVERLAP_BUFFER_MIN) <= rec_end:
            return True
    return False

def find_uncounted_meetings(date, rows):
    """Calendar events with no matching Fathom row for the day: meetings
    nobody hit record on, invisible to fetch_live_recordings because it only
    ever sees what Fathom captured. Calendar enumeration is best-effort and
    additive -- a failure here is logged and swallowed rather than turning an
    otherwise-verified Fathom day into "cannot verify"."""
    events, cal_err = fetch_calendar_events(date)
    if cal_err:
        print(f"[mom-reconcile] calendar enumeration skipped: {cal_err}", file=sys.stderr)
        return []
    uncounted = []
    for e in _substantial_calendar_events(events):
        if _fathom_matches_calendar_event(e, rows):
            continue
        uncounted.append({
            "title": e.get("summary") or "untitled",
            "start": e.get("start"),
            "end": e.get("end"),
            "reason": "on calendar, no Fathom recording found for this day",
        })
    return uncounted

def within_grace(end_utc):
    """True if the recording ended less than GRACE_MINUTES ago (still fair to
    be un-minuted). Unknown end time is treated as outside grace."""
    dt = _parse_utc(end_utc)
    if not dt:
        return False
    age = datetime.datetime.now(datetime.timezone.utc) - dt
    return age < datetime.timedelta(minutes=GRACE_MINUTES)

def is_personal_recording(entry):
    """True when the recording is not Work's to minute here (You/ClientB/TLN
    class). Registry client wins; title tokens cover not-yet-registered rows."""
    client = (entry.get("client") or "").strip()
    if client and client.lower() != "work":
        return True
    if client:
        return False
    title = title_of(entry).lower()
    if any(p in title for p in PERSONAL_TITLE_PHRASES):
        return True
    tokens = set(re.split(r"[^a-z0-9]+", title))
    return bool(tokens & PERSONAL_TITLE_TOKENS)

def mom_on_disk(mom_path):
    if not mom_path:
        return None
    p = mom_path if os.path.isabs(mom_path) else os.path.join(REPO_ROOT, mom_path)
    return p if os.path.isfile(p) else None

# Byte size alone let an 11-minute Vexa draft with zero decisions clear
# MIN_MOM_BYTES by 156 bytes on 30 Jul 2026 and read as covered. These scan
# the full body for the two things minutes actually exist to record.
DECISION_HEADING_RE = re.compile(r"^#{2,3}\s*decisions?\b", re.IGNORECASE | re.MULTILINE)
DECISION_TICKET_RE = re.compile(r"\bDEC-\d+\b")
ACTION_TASK_RE = re.compile(r"^- \[[ xX]\]", re.MULTILINE)
ACTION_HEADING_RE = re.compile(r"^#{2,3}\s*action items?\b", re.IGNORECASE | re.MULTILINE)
BULLET_LINE_RE = re.compile(r"^\s*[-*]\s+\S")

def _has_decisions(text):
    return bool(DECISION_HEADING_RE.search(text) or DECISION_TICKET_RE.search(text))

def _has_action_items(text):
    if ACTION_TASK_RE.search(text):
        return True
    m = ACTION_HEADING_RE.search(text)
    if not m:
        return False
    # A bare heading with nothing under it is not an action item. Stop at the
    # next heading (same level or higher) so a bullet under a later, unrelated
    # section cannot count.
    for line in text[m.end():].splitlines():
        if re.match(r"^#{1,3}\s", line):
            break
        if BULLET_LINE_RE.match(line):
            return True
    return False

def _mom_has_substance(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return True  # unreadable is already caught by inspect_mom's earlier size check
    return _has_decisions(text) or _has_action_items(text)

def inspect_mom(path):
    """Return (ok, reason). ok=False means it exists but should not count."""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return False, f"unreadable: {e}"
    if size < MIN_MOM_BYTES:
        return False, f"only {size}B, below the {MIN_MOM_BYTES}B minutes threshold"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4000)
    except OSError as e:
        return False, f"unreadable: {e}"
    for marker in STUB_MARKERS:
        if marker in head:
            return False, f"stub: contains {marker!r}, real minutes live elsewhere"
    if not _mom_has_substance(path):
        return False, "0 decisions and 0 action items despite passing size threshold"
    return True, ""

def resolve_coverage(rec_id, entry, registry, claimed=None):
    """Find a MOM for this recording, directly or via a sibling capture.

    `claimed` is the set of MOM paths already assigned to earlier recordings in
    this run, so one MOM can never be counted as covering two meetings."""
    direct = mom_on_disk(entry.get("mom_path"))
    if direct:
        return direct, "own"

    # Authoritative content backlink: the recording_id / call_id that every MOM
    # writer stamps into the file header. The registry mom_path backlink and the
    # calendar-resolved title are BOTH routinely absent for impromptu meetings
    # (matched_meeting=None, no mom_path), so those fall through to filename
    # fuzzy matching, which cannot work when the placeholder title "Impromptu
    # Google Meet Meeting" shares no tokens with the real MOM_<Title> filename.
    # The source recording is always in the file, so match on it directly.
    id_index = _mom_id_index_for_date(entry.get("date_wib") or "")
    call_id = ""
    m = re.search(r"/calls/(\d+)", entry.get("fathom_url") or "")
    if m:
        call_id = m.group(1)
    for key in (str(rec_id), call_id):
        if key and key in id_index:
            p = id_index[key]
            if not (claimed and p in claimed):
                return p, "content-id"

    for rid in entry.get("related_recordings") or []:
        rel = registry.get(str(rid)) or registry.get(rid)
        if rel:
            p = mom_on_disk(rel.get("mom_path"))
            if p:
                return p, f"related:{rid}"

    matched = (entry.get("matched_meeting") or "").strip().lower()
    if matched:
        for rid, e in registry.items():
            if rid == rec_id or (e.get("date_wib") != entry.get("date_wib")):
                continue
            if (e.get("matched_meeting") or "").strip().lower() != matched:
                continue
            p = mom_on_disk(e.get("mom_path"))
            if p:
                return p, f"same-meeting:{rid}"

    p = find_mom_by_filename(title_of(entry), entry.get("date_wib") or "",
                             entry.get("time_wib") or "", claimed=claimed)
    if p:
        return p, "filename"
    return None, ""

def _mom_id_index_for_date(date, _cache={}):
    """Map every Fathom recording_id and call_id stamped inside a MOM header to
    that MOM's path, for `date`. Both id forms appear across writers: the auto
    pipeline writes `Source: ... /calls/<call_id> (id <recording_id>)`, hand or
    /mom-written minutes may carry only the `/calls/<call_id>` link. Indexing
    both makes the content backlink authoritative regardless of which writer
    produced the file. Only the header (first 4000 bytes) is scanned so a call
    id quoted deep in the body cannot create a spurious mapping."""
    if date in _cache:
        return _cache[date]
    index = {}
    for p in _mom_files_for_date(date):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                head = f.read(4000)
        except OSError:
            continue
        ids = set(re.findall(r"/calls/(\d+)", head))
        ids |= set(re.findall(r"\bid[:\s]+(\d+)", head))
        for i in ids:
            index.setdefault(i, p)
    _cache[date] = index
    return index

def slug_tokens(title):
    return [t for t in re.split(r"[^a-z0-9]+", (title or "").lower()) if len(t) > 2]

def _mom_files_for_date(date, _cache={}):
    """All meeting .md files for `date` across Clients/Work/**/meetings/.

    The recursive glob covers both the top-level meetings dir and per-project
    dirs (Clients/Work/<Project>/meetings/), where the real MOM_<Title>_<date>
    minutes actually live."""
    if date not in _cache:
        try:
            _cache[date] = [p for p in glob.glob(MEETINGS_GLOB, recursive=True)
                            if date in os.path.basename(p)]
        except OSError:
            _cache[date] = []
    return _cache[date]

# A MOM covers at most ONE recording. A false-positive here is worse than a
# false miss: it marks a genuinely un-minuted meeting as covered and suppresses
# the alarm, which is the exact "grade its own homework" failure this script
# exists to catch. So the token score is symmetric (Jaccard over the UNION, not
# containment over the query, which let a short query title score 1.0 as a
# subset of a different, longer same-day MOM), the bar is high, and a file
# already claimed by another recording is off the table.
MATCH_THRESHOLD = 0.72

def _best_title_match(title, date, files, claimed=None):
    want = set(slug_tokens(title))
    if not want:
        return None
    best, best_score = None, 0.0
    for p in files:
        if claimed and p in claimed:
            continue
        # Strip the date and the MOM_ prefix so they do not skew the token set.
        stem = os.path.basename(p).replace(date, "").replace("MOM_", "")
        have = set(slug_tokens(stem))
        if not have:
            continue
        jaccard = len(want & have) / len(want | have)
        ratio = difflib.SequenceMatcher(None, " ".join(sorted(want)),
                                        " ".join(sorted(have))).ratio()
        score = max(jaccard, ratio)
        if score > best_score:
            best, best_score = p, score
    return best if best and best_score >= MATCH_THRESHOLD else None

def find_mom_by_filename(title, date, time_wib, claimed=None):
    """Last-resort match against the meeting files on disk.

    Registry mom_path linkage is unreliable (only a fraction of rows carry one,
    and a hand-written or rebuilt MOM never sets it at all). Without this, every
    correctly-minuted meeting whose registry row lacks the backlink reports as
    MISSING, and a checker that false-alarms daily is a checker nobody reads.

    The real minutes are MOM_<Title>_<date>.md, so those are matched first. The
    time-stamped auto-sync note (<date>_<HHMM>_<Title>.md) is a fallback for
    impromptu meetings that Fathom never gave a real title. A file already in
    `claimed` (matched to an earlier recording this run) is skipped."""
    files = _mom_files_for_date(date)
    if not files:
        return None
    mom_files = [p for p in files if os.path.basename(p).startswith("MOM_")]
    other_files = [p for p in files if not os.path.basename(p).startswith("MOM_")]

    # 1. Fuzzy title match against the real minutes.
    hit = _best_title_match(title, date, mom_files, claimed=claimed)
    if hit:
        return hit

    # 2. Time-stamped auto-sync note (still real minutes when there was no title).
    if time_wib:
        hhmm = time_wib.replace(":", "")
        for p in other_files:
            if claimed and p in claimed:
                continue
            if f"{date}_{hhmm}" in os.path.basename(p):
                return p

    # 3. Last resort: fuzzy title against the auto-sync notes too.
    return _best_title_match(title, date, other_files, claimed=claimed)

def title_of(entry):
    return (entry.get("matched_meeting") or entry.get("raw_title") or "untitled").strip()

def _empty_result(date, authoritative, reason=""):
    return {
        "date": date,
        "generated_at": datetime.datetime.now(WIB).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "authoritative": authoritative,
        "unverifiable_reason": reason,
        "total": 0,
        "covered": 0,
        "missing": [],
        "suspect": [],
        "pending": [],
        "out_of_scope": [],
        "covered_detail": [],
        "uncounted": [],
    }

def reconcile(date):
    registry = load_registry()

    # Enumerate the day from LIVE Fathom. The registry is enrichment only.
    rows, live_err = fetch_live_recordings(date, registry)
    if live_err is not None:
        # Live Fathom is the enumerator; if it is unreachable we genuinely
        # cannot say what happened today. Never fall through to a clean pass.
        return _empty_result(date, authoritative=False,
                              reason=f"live fathom unreachable: {live_err}")

    rows.sort(key=lambda r: r[1].get("time_wib") or "")

    # Calendar cross-check happens against the Fathom rows for this day, so it
    # runs once here rather than inside the per-recording loop below.
    uncounted = find_uncounted_meetings(date, rows)

    covered, missing, suspect, pending, out_of_scope = [], [], [], [], []
    claimed = set()  # MOM paths already assigned; one MOM covers one recording
    for rec_id, entry in rows:
        title = title_of(entry)
        rec = {
            "recording_id": rec_id,
            "time_wib": entry.get("time_wib"),
            "matched_meeting": title,
            "fathom_url": entry.get("fathom_url"),
        }
        if is_personal_recording(entry):
            rec["reason"] = f"client={entry.get('client') or 'personal-title'}, minuted outside this repo"
            out_of_scope.append(rec)
            continue
        if title.strip().lower() in NON_MEETING_TITLES:
            rec["reason"] = "non-meeting calendar block, likely a mis-match at capture"
            suspect.append(rec)
            continue

        path, via = resolve_coverage(rec_id, entry, registry, claimed=claimed)
        if path:
            claimed.add(path)
        if not path:
            # A recording that just ended has not had time to be minuted yet.
            if within_grace(entry.get("recording_end_utc")):
                rec["reason"] = f"ended < {GRACE_MINUTES} min ago, minutes not due yet"
                pending.append(rec)
            else:
                missing.append(rec)
            continue

        ok, reason = inspect_mom(path)
        rec["mom_path"] = os.path.relpath(path, REPO_ROOT)
        rec["via"] = via
        if ok:
            covered.append(rec)
        else:
            rec["reason"] = reason
            suspect.append(rec)

    return {
        "date": date,
        "generated_at": datetime.datetime.now(WIB).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "authoritative": True,
        "unverifiable_reason": "",
        "total": len(rows) - len(out_of_scope),
        "covered": len(covered),
        "missing": missing,
        "suspect": suspect,
        "pending": pending,
        "out_of_scope": out_of_scope,
        "covered_detail": covered,
        "uncounted": uncounted,
    }

def main():
    ap = argparse.ArgumentParser(description="Reconcile Fathom recordings against MOM files")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD in WIB (default: today)")
    ap.add_argument("--quiet", action="store_true", help="only print the summary line")
    ap.add_argument("--no-heartbeat", action="store_true")
    args = ap.parse_args()

    date = args.date or today_wib()
    try:
        result = reconcile(date)
    except Exception as e:
        msg = f"{date}: reconcile failed: {e}"
        print(f"[mom-reconcile] {msg}", file=sys.stderr)
        if not args.no_heartbeat:
            heartbeat("fail", msg)
        return 2

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    os.replace(tmp, OUT_PATH)

    # PENDING recordings ended within the grace window and are not yet due, so
    # they are deliberately excluded from the gap set that trips the alarm.
    # UNCOUNTED is included: a calendar event nobody recorded is exactly the
    # kind of miss this script exists to surface, not a softer category.
    gaps = result["missing"] + result["suspect"] + result.get("uncounted", [])
    if not args.quiet:
        for r in result["covered_detail"]:
            print(f"  OK      {r['time_wib']}  {r['matched_meeting']}  -> {r['mom_path']}")
        for r in result.get("pending", []):
            print(f"  PENDING {r['time_wib']}  {r['matched_meeting']}  ({r['reason']})")
        for r in result["missing"]:
            print(f"  MISSING {r['time_wib']}  {r['matched_meeting']}  {r['fathom_url'] or ''}")
        for r in result["suspect"]:
            print(f"  SUSPECT {r['time_wib']}  {r['matched_meeting']}  ({r['reason']})")
        for r in result.get("out_of_scope", []):
            print(f"  SKIP    {r['time_wib']}  {r['matched_meeting']}  ({r['reason']})")
        for r in result.get("uncounted", []):
            print(f"  UNCOUNTED {r.get('start')}  {r['title']}  ({r['reason']})")

    # A non-authoritative result cannot be graded. Never let it exit 0: that is
    # exactly the false clean of 17 Jul 2026 (registry blind mid-day). The day
    # is now enumerated from live Fathom, so this fires when Fathom itself was
    # unreachable and we truly could not look.
    if not result.get("authoritative", True):
        reason = result.get("unverifiable_reason") or "could not enumerate the day"
        msg = f"{date}: CANNOT VERIFY coverage, {reason}"
        print(f"[mom-reconcile] {msg}", file=sys.stderr)
        if not args.no_heartbeat:
            heartbeat("fail", msg)
        return 2

    summary = (f"{date}: {result['covered']}/{result['total']} meetings minuted"
               f", {len(result['missing'])} missing, {len(result['suspect'])} suspect"
               f", {len(result.get('pending', []))} pending"
               f", {len(result.get('out_of_scope', []))} non-work skipped"
               f", {len(result.get('uncounted', []))} uncounted (calendar, never recorded)")
    print(f"[mom-reconcile] {summary}")

    if gaps:
        # uncounted entries carry start/title, not time_wib/matched_meeting
        # (they never had a Fathom row to take those fields from).
        titles = ", ".join(f"{r.get('time_wib') or r.get('start')} "
                            f"{r.get('matched_meeting') or r.get('title')}" for r in gaps[:5])
        if not args.no_heartbeat:
            heartbeat("fail", f"{summary}: {titles}")
        return 1
    if not args.no_heartbeat:
        heartbeat("ok", summary)
    return 0

if __name__ == "__main__":
    sys.exit(main())
