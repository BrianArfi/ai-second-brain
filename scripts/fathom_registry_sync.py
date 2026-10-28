#!/usr/bin/env python3
"""
Fathom Meeting Registry Sync
=============================
Builds and maintains a CUMULATIVE local registry that maps every Fathom
recording (mostly titled "Impromptu Google Meet Meeting") to the real meeting
it belongs to, by matching against the Work AND personal Google Calendars on
time proximity + attendee-email overlap.

Why this exists
---------------
The Fathom API only returns recent meetings per page (~10) but DOES paginate via
`next_cursor`. This script walks that cursor and accumulates results into a local
file keyed by recording_id, so historical mappings are never lost even after they
fall out of the API window. Goal: when the owner names "meeting X on date Y", we can
look up the right Fathom link instantly.

Outputs
-------
  journal/fathom_registry.json   -- machine-readable master list (source of truth)
  Fathom_Registry.md             -- human-readable index, sorted newest-first

Usage
-----
  python3 scripts/fathom_registry_sync.py              # incremental daily sync
  python3 scripts/fathom_registry_sync.py --backfill   # walk ALL Fathom history once
  python3 scripts/fathom_registry_sync.py --max-pages N
  python3 scripts/fathom_registry_sync.py --rebuild-md  # only regenerate the .md from json

On Windows: prefix with the wsl.exe wrapper from CLAUDE.md.
"""
import os
import sys
import json
import argparse
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_JSON = REPO_ROOT / "journal" / "fathom_registry.json"
REGISTRY_MD = REPO_ROOT / "Fathom_Registry.md"

FATHOM_TOKEN_ENV = REPO_ROOT / ".agent" / "skills" / "fathom-connector" / "token.env"
PERSONAL_CAL_TOKEN = REPO_ROOT / "token_calendar.json"
WORK_CAL_TOKEN = REPO_ROOT / ".agent" / "skills" / "work-drive-connector" / "token_calendar_work.json"

WIB = timezone(timedelta(hours=7))
MATCH_WINDOW_SEC = 1800          # +/- 30 min between recording start and event start
BRIAN_EMAILS = {"you@example.com"}   # excluded when scoring "shared attendee"

# ---- client/project classification (ported from fathom_to_notes.py) ----
CLIENT_MAPPINGS = [
    {"domain": "yourcompany.com", "client": "Work"},
    {"domain": "secondary.id", "client": "Secondary"},
]
PERSONAL_EMAIL = "you@example.com"
PROJECT_KEYWORDS = ["Taaruf Lalu Nikah", "You", "BWC", "ClientB"]

# ----------------------------------------------------------------------------
# Fathom
# ----------------------------------------------------------------------------
def load_fathom_token():
    if FATHOM_TOKEN_ENV.exists():
        for line in FATHOM_TOKEN_ENV.read_text().splitlines():
            if line.startswith("FATHOM_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("FATHOM_API_KEY")

def fathom_get(token, cursor=None):
    base = "https://api.fathom.ai/external/v1/meetings"
    params = {
        "include_transcript": "false",   # registry stores metadata only; pull transcript on demand
        "include_summary": "false",
        "include_action_items": "false",
    }
    if cursor:
        params["cursor"] = cursor
    url = base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Api-Key": token, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))

def fetch_fathom(token, known_ids, backfill=False, max_pages=200):
    """Walk Fathom pages via next_cursor. Returns list of raw meeting dicts.
    Incremental mode stops once a page contains only already-known recording_ids."""
    out, cursor, pages = [], None, 0
    while pages < max_pages:
        data = fathom_get(token, cursor)
        items = data.get("items", [])
        if not items:
            break
        new_on_page = [m for m in items if (m.get("recording_id") or m.get("id")) not in known_ids]
        out.extend(items)
        pages += 1
        print(f"  [fathom] page {pages}: {len(items)} items ({len(new_on_page)} new)", file=sys.stderr)
        cursor = data.get("next_cursor")
        if not cursor:
            break
        if not backfill and not new_on_page:
            print("  [fathom] reached already-known recordings, stopping incremental walk", file=sys.stderr)
            break
    return out

# ----------------------------------------------------------------------------
# Google Calendar (manual token refresh, no external deps)
# ----------------------------------------------------------------------------
def refresh_cal_token(token_path):
    if not token_path.exists():
        return None
    try:
        data = json.loads(token_path.read_text())
    except ValueError:
        # Empty or corrupt token file: skip this calendar instead of crashing the sync.
        print(f"  [cal] unreadable token file, skipping: {token_path}", file=sys.stderr)
        return None
    payload = {
        "client_id": data.get("client_id"),
        "client_secret": data.get("client_secret"),
        "refresh_token": data.get("refresh_token"),
        "grant_type": "refresh_token",
    }
    refresh_url = data.get("token_uri", "https://oauth2.googleapis.com/token")
    try:
        req = urllib.request.Request(
            refresh_url, data=urllib.parse.urlencode(payload).encode(), method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.load(resp)
        new_token = res.get("access_token")
        if new_token:
            data["token"] = new_token
            token_path.write_text(json.dumps(data))   # persist refreshed access token
            return new_token
    except Exception as e:
        print(f"  [cal] refresh failed for {token_path.name}: {e}", file=sys.stderr)
        return data.get("token")
    return data.get("token")

def fetch_cal_events(access_token, time_min, time_max):
    """Fetch all primary-calendar events in [time_min, time_max] (paginated)."""
    events, page_token = [], None
    while True:
        params = {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "2500",
        }
        if page_token:
            params["pageToken"] = page_token
        url = ("https://www.googleapis.com/calendar/v3/calendars/primary/events?"
               + urllib.parse.urlencode(params))
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
        except Exception as e:
            print(f"  [cal] fetch error: {e}", file=sys.stderr)
            break
        events.extend(data.get("items", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return events

# ----------------------------------------------------------------------------
# Matching + classification
# ----------------------------------------------------------------------------
def parse_dt(s):
    if not s:
        return None
    if "T" not in s:
        s += "T00:00:00+00:00"
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def event_emails(event):
    org = event.get("organizer", {}).get("email", "").lower()
    att = [a.get("email", "").lower() for a in event.get("attendees", [])]
    return {e for e in ([org] + att) if e}

def classify(all_emails, title, desc=""):
    title_l = (title or "").lower()
    if "tln" in title_l or "taaruf" in title_l or any("tln" in e for e in all_emails):
        return "You", "Taaruf Lalu Nikah"
    if "hsi" in title_l:
        return "ClientB", "General"
    for mapping in CLIENT_MAPPINGS:
        if any(mapping["domain"] in email for email in all_emails):
            client, project = mapping["client"], "General"
            if client == "Work":
                if "marketplace" in title_l: project = "Marketplace"
                elif "portal" in title_l: project = "Seller Portal"
                elif "b2c" in title_l or "super app" in title_l: project = "B2C SuperApp"
                elif "ExampleProgram" in title_l or "exampleco" in title_l: project = "Example Program"
                elif "platform" in title_l: project = "Platform"
                elif "pim" in title_l: project = "B2C SuperApp"
                elif "catalog" in title_l or "ecom" in title_l: project = "E-Commerce Solution"
            return client, project
    if any(PERSONAL_EMAIL in email for email in all_emails):
        for kw in PROJECT_KEYWORDS:
            if kw.lower() in title_l or kw.lower() in (desc or "").lower():
                return "You", kw
        return "You", "General"
    return None, None

def match_recording(meeting, all_events):
    """Return (best_event, source, confidence) or (None, None, 'impromptu')."""
    start = parse_dt(meeting.get("recording_start_time") or meeting.get("scheduled_start_time"))
    if not start:
        return None, None, "no_time"

    f_emails = {i.get("email", "").lower() for i in (meeting.get("calendar_invitees") or [])}
    f_emails = {e for e in f_emails if e and e not in BRIAN_EMAILS}

    candidates = []
    for idx, (ev, source) in enumerate(all_events):
        e_start = parse_dt(ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date"))
        if not e_start:
            continue
        diff = abs((start - e_start).total_seconds())
        if diff <= MATCH_WINDOW_SEC:
            overlap = len((event_emails(ev) - BRIAN_EMAILS) & f_emails)
            # idx is a unique tiebreaker so sort never falls through to comparing dicts
            candidates.append((overlap, -diff, idx, ev, source))

    if not candidates:
        return None, None, "impromptu"

    # best = most attendee overlap, then closest in time
    candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
    overlap, _, _, ev, source = candidates[0]
    confidence = "high" if overlap >= 1 else "medium"
    return ev, source, confidence

# ----------------------------------------------------------------------------
# Registry I/O
# ----------------------------------------------------------------------------
def load_registry():
    if REGISTRY_JSON.exists():
        try:
            return json.loads(REGISTRY_JSON.read_text())
        except Exception:
            return {}
    return {}

def to_wib_str(s, fmt="%Y-%m-%d %H:%M"):
    dt = parse_dt(s)
    return dt.astimezone(WIB).strftime(fmt) if dt else ""

def build_entry(meeting, ev, source, confidence):
    rid = meeting.get("recording_id") or meeting.get("id")
    start = meeting.get("recording_start_time") or meeting.get("scheduled_start_time")
    end = meeting.get("recording_end_time") or meeting.get("scheduled_end_time")
    invitees = meeting.get("calendar_invitees") or []

    duration = ""
    sdt, edt = parse_dt(start), parse_dt(end)
    if sdt and edt:
        duration = f"{int((edt - sdt).total_seconds() / 60)} min"

    if ev:
        matched_title = ev.get("summary", "(untitled event)")
        all_emails = list(event_emails(ev))
        client, project = classify(all_emails, ev.get("summary", ""), ev.get("description", ""))
    else:
        matched_title = None
        all_emails = [i.get("email", "").lower() for i in invitees]
        client, project = classify(all_emails, meeting.get("title") or meeting.get("meeting_title", ""))

    return {
        "recording_id": rid,
        "fathom_url": meeting.get("url") or meeting.get("share_url"),
        "date_wib": to_wib_str(start, "%Y-%m-%d"),
        "time_wib": to_wib_str(start, "%H:%M"),
        "start_utc": start,
        "duration": duration,
        "raw_title": meeting.get("title") or meeting.get("meeting_title"),
        "matched_meeting": matched_title,
        "match_source": source,            # 'work' | 'personal' | None
        "confidence": confidence,          # high | medium | impromptu | no_time
        "client": client or "Unsorted",
        "project": project or "",
        "participants": [i.get("name") or i.get("email") for i in invitees],
        "transcript_language": meeting.get("transcript_language"),
        "last_synced_utc": SYNC_STAMP,
    }

def upsert(registry, entry):
    rid = str(entry["recording_id"])
    existing = registry.get(rid)
    if existing:
        # Upgrade a previously-unmatched/medium entry if we now have a better match.
        rank = {"no_time": 0, "impromptu": 1, "medium": 2, "high": 3}
        if rank.get(entry["confidence"], 0) > rank.get(existing.get("confidence"), 0):
            existing.update({
                "matched_meeting": entry["matched_meeting"],
                "match_source": entry["match_source"],
                "confidence": entry["confidence"],
                "client": entry["client"],
                "project": entry["project"],
            })
        existing["last_synced_utc"] = SYNC_STAMP
        return False
    registry[rid] = entry
    return True

def link_duplicate_recordings(registry):
    """Cross-reference entries that cover the SAME calendar meeting (same
    matched_meeting + date) — happens when Fathom AND a Vexa bot / local
    recorder both captured it. Downstream MOM drafting uses this to keep
    one MOM per meeting. Returns number of entries updated."""
    by_key = {}
    for rid, e in registry.items():
        m = (e.get("matched_meeting") or "").strip().lower()
        if m:
            by_key.setdefault((m, e.get("date_wib")), []).append(rid)
    updated = 0
    for ids in by_key.values():
        if len(ids) < 2:
            continue
        for rid in ids:
            merged = sorted(set(registry[rid].get("related_recordings", []))
                            | {x for x in ids if x != rid})
            if merged != registry[rid].get("related_recordings"):
                registry[rid]["related_recordings"] = merged
                updated += 1
    return updated

CONF_ICON = {"high": "🟢", "medium": "🟡", "impromptu": "⚪", "no_time": "❓"}

def write_md(registry):
    rows = sorted(registry.values(), key=lambda e: e.get("start_utc") or "", reverse=True)
    matched = sum(1 for e in rows if e["confidence"] in ("high", "medium"))
    impromptu = sum(1 for e in rows if e["confidence"] == "impromptu")
    lines = [
        "# Fathom Meeting Registry",
        "",
        "> Auto-generated by `scripts/fathom_registry_sync.py`. Do not edit by hand — re-run the sync.",
        f"> Total recordings: **{len(rows)}** | matched to calendar: **{matched}** | truly impromptu: **{impromptu}**",
        f"> Last sync: {SYNC_STAMP} UTC",
        "",
        "Confidence: 🟢 time + shared attendee · 🟡 time only · ⚪ no calendar event (impromptu) · ❓ no timestamp",
        "",
        "| Date (WIB) | Time | Meeting | Client / Project | Conf | Cal | Dur | Fathom |",
        "| :--- | :--- | :--- | :--- | :---: | :--- | :--- | :--- |",
    ]
    for e in rows:
        meeting = e.get("matched_meeting") or f"_{e.get('raw_title') or 'Untitled'}_"
        cp = e["client"] + (f" / {e['project']}" if e.get("project") else "")
        icon = CONF_ICON.get(e["confidence"], "")
        cal = e.get("match_source") or "—"
        url = e.get("fathom_url") or ""
        link = f"[link]({url})" if url else "—"
        lines.append(
            f"| {e.get('date_wib','')} | {e.get('time_wib','')} | {meeting} | {cp} "
            f"| {icon} | {cal} | {e.get('duration','')} | {link} |"
        )
    REGISTRY_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

def save_registry(registry):
    REGISTRY_JSON.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_JSON.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    write_md(registry)

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
SYNC_STAMP = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def main():
    ap = argparse.ArgumentParser(description="Fathom Meeting Registry Sync")
    ap.add_argument("--backfill", action="store_true", help="Walk entire Fathom history via next_cursor")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--rebuild-md", action="store_true", help="Only regenerate the .md from existing json")
    args = ap.parse_args()

    registry = load_registry()

    if args.rebuild_md:
        write_md(registry)
        print(f"Rebuilt {REGISTRY_MD.relative_to(REPO_ROOT)} from {len(registry)} entries.")
        return

    f_token = load_fathom_token()
    if not f_token:
        print("[ERROR] FATHOM_API_KEY not found.", file=sys.stderr)
        sys.exit(1)

    known_ids = {int(k) for k in registry.keys() if str(k).isdigit()}
    print(f"Registry has {len(registry)} entries. Fetching Fathom ({'BACKFILL' if args.backfill else 'incremental'})...", file=sys.stderr)
    meetings = fetch_fathom(f_token, known_ids, backfill=args.backfill, max_pages=args.max_pages)
    print(f"Fetched {len(meetings)} recordings from Fathom.", file=sys.stderr)

    # Time window to pull calendar events for (cover all fetched recordings).
    starts = [parse_dt(m.get("recording_start_time") or m.get("scheduled_start_time")) for m in meetings]
    starts = [s for s in starts if s]
    all_events = []
    if starts:
        tmin = (min(starts) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        tmax = (max(starts) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        print(f"Fetching calendar events {tmin} -> {tmax}", file=sys.stderr)
        for label, token_path in (("work", WORK_CAL_TOKEN), ("personal", PERSONAL_CAL_TOKEN)):
            tok = refresh_cal_token(token_path)
            if not tok:
                print(f"  [cal] {label}: no token, skipping", file=sys.stderr)
                continue
            evs = fetch_cal_events(tok, tmin, tmax)
            print(f"  [cal] {label}: {len(evs)} events", file=sys.stderr)
            all_events.extend((ev, label) for ev in evs)

    added = 0
    for m in meetings:
        ev, source, conf = match_recording(m, all_events)
        entry = build_entry(m, ev, source, conf)
        if upsert(registry, entry):
            added += 1

    linked = link_duplicate_recordings(registry)
    if linked:
        print(f"  [dedupe] cross-referenced {linked} duplicate-recording entries", file=sys.stderr)
    save_registry(registry)
    matched = sum(1 for e in registry.values() if e["confidence"] in ("high", "medium"))
    print(f"\nDone. {added} new, {len(registry)} total ({matched} matched to calendar).")
    print(f"  -> {REGISTRY_JSON.relative_to(REPO_ROOT)}")
    print(f"  -> {REGISTRY_MD.relative_to(REPO_ROOT)}")

if __name__ == "__main__":
    main()
