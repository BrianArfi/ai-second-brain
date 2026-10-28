import os
import sys
import json
import re
import subprocess
import tempfile
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# The You (personal brand) repo is a SEPARATE checkout, per the hard data
# separation rule in CLAUDE.md. You-classified meetings must land there, NOT
# inside this Work repo. Writing to `REPO_ROOT / "You"` created a phantom
# You/ directory inside PSB and committed personal-brand notes into the Work
# repo (found 17 Jul 2026). Resolve the real sibling repo; if it is not present
# on this machine, fall back to local triage rather than recreating the phantom.
YOU_REPO = Path(
    os.environ.get("YOU_REPO", "~/antigravity-projects/You"))
TRIAGE_DIR = REPO_ROOT / "_temp" / "meeting_triage"

# LLM transcript-scan pass (catches action items Fathom's auto-detect missed).
# Set FATHOM_LLM_ACTION_ITEMS=0 to disable. Routes through agy-bridge (GLM 5.2).
LLM_ACTION_ITEMS = os.environ.get("FATHOM_LLM_ACTION_ITEMS", "1") != "0"
AGY_BRIDGE = REPO_ROOT / ".agent/skills/agy-bridge/run.py"

# Add skill path for Fathom client
sys.path.append(os.path.join(os.getcwd(), ".agent/skills/fathom-connector/scripts"))
try:
    import fathom_client
except ImportError:
    print("[ERROR] Could not import fathom_client. Make sure you are in the repo root.")
    sys.exit(1)

# Mapping constants
CLIENT_MAPPINGS = [
    {"domain": "yourcompany.com", "client": "Work"},
    {"domain": "secondary.id", "client": "Secondary"},
]

PERSONAL_EMAIL = "you@example.com"
PROJECT_KEYWORDS = ["Taaruf Lalu Nikah", "You", "BWC"]

# fathom_registry_sync already resolves client/project via calendar matching, which
# is more reliable than the invitee/title heuristic below. In particular, meetings
# the owner joins on his personal Google account (no yourcompany.com invitee captured)
# would otherwise fall through to a You/General default. Trust the registry first.
FATHOM_REGISTRY = REPO_ROOT / "journal" / "fathom_registry.json"
_REGISTRY_CACHE = None

def registry_lookup(m_id):
    """Return (client, project) from fathom_registry for this recording_id, or (None, None).
    Only returns a known enterprise client (Work/Secondary) so genuinely-personal meetings
    still fall through to the heuristic classifier."""
    global _REGISTRY_CACHE
    if m_id is None:
        return None, None
    if _REGISTRY_CACHE is None:
        try:
            with open(FATHOM_REGISTRY) as f:
                _REGISTRY_CACHE = json.load(f)
        except Exception:
            _REGISTRY_CACHE = {}
    entry = _REGISTRY_CACHE.get(str(m_id))
    if not entry:
        return None, None
    client = entry.get("client")
    if client in ("Work", "Secondary"):
        return client, entry.get("project") or "General"
    return None, None

# Calendar Helper (adapted from gcal_sweep_raw.py)
DEFAULT_TOKEN = os.path.join(os.getcwd(), "token_calendar.json")
WORK_TOKEN = os.path.join(os.getcwd(), ".agent", "skills", "work-drive-connector", "token_calendar_work.json")

def _load_token_file(path):
    # An empty or corrupt token file is treated as missing, not as a crash.
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (ValueError, OSError):
        print(f"  [cal] unreadable token file, skipping: {path}", file=sys.stderr)
        return None

def refresh_gcal_token():
    data = _load_token_file(DEFAULT_TOKEN) or _load_token_file(WORK_TOKEN)
    if not data:
        return None
    refresh_url = data.get('token_uri', 'https://oauth2.googleapis.com/token')
    payload = {
        'client_id': data.get('client_id'),
        'client_secret': data.get('client_secret'),
        'refresh_token': data.get('refresh_token'),
        'grant_type': 'refresh_token'
    }
    try:
        req = urllib.request.Request(refresh_url, data=urllib.parse.urlencode(payload).encode(), method='POST')
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.load(resp)
            new_token = res.get('access_token')
            if new_token:
                data['token'] = new_token
                return new_token
    except Exception:
        return data.get('token')
    return None

def fetch_calendar_events(token, days_back=1):
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=days_back)).isoformat()
    params = urllib.parse.urlencode({
        'timeMin': time_min,
        'singleEvents': 'true',
        'orderBy': 'startTime'
    })
    url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{params}"
    req = urllib.request.Request(url)
    req.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp).get('items', [])
    except Exception:
        return []

def match_meeting_to_event(meeting, events):
    """Matches a Fathom meeting to a Calendar event by time window (+/- 15m)."""
    start_str = meeting.get("recording_start_time") or meeting.get("start_at")
    if not start_str: return None
    m_start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
    
    for event in events:
        e_start_str = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
        if not e_start_str: continue
        # Handle date-only fields
        if 'T' not in e_start_str: e_start_str += "T00:00:00+00:00"
        e_start = datetime.fromisoformat(e_start_str.replace('Z', '+00:00'))
        
        # Check window
        diff = abs((m_start - e_start).total_seconds())
        if diff < 1800: # 30 minutes for better matching of delayed starts
            return event
    return None

# Content signals that a recording is Work work. the owner's Fathom account is his
# PERSONAL one, so PERSONAL_EMAIL rides on nearly every recording and is NOT
# evidence of a personal meeting. Combined with an "Impromptu Google Meet
# Meeting" title (no keyword to match) and Fathom invitees that resolve to just
# "owner arfi" (no yourcompany.com address), that sent the ExampleCo Ideation
# Workshop and the Sprint End Demo into You on 17 Jul. Same class of failure
# as the 4 Jul incident where 13 meetings were mis-filed. The 4 Jul rule is:
# classify Impromptu recordings by CONTENT, never DEFAULT to You.
WORK_SIGNALS = (
    "work", "exampleprogram", "example program", "ExampleProgram", "exampleco", "examplevendor", "ExampleVendor",
    "storefront", "seller portal", "marketplace", "ExamplePlatform", "super platform",
    "Example Catalogue", "online catalog", "ExampleSupplier", "ExampleVendor", "Teammate", "ExampleVendor",
    "ExampleFeature", "buy box", "oms", "pim", "b2c", "ExampleProgram", "Example Alliance",
    "Teammate", "Teammate", "Teammate", "Teammate", "Teammate", "Teammate", "Teammate", "Teammate",
    "Teammate", "Teammate", "Teammate", "YourManager Teammate", "Teammate", "Teammate",
)

def work_signal(*texts):
    """True if any Work signal appears in the given text. Used only to RESCUE a
    recording that would otherwise fall through to You."""
    blob = " ".join(t for t in texts if t).lower()
    return any(s in blob for s in WORK_SIGNALS)

def work_project_from_text(*texts):
    blob = " ".join(t for t in texts if t).lower()
    if "ExampleProgram" in blob or "exampleco" in blob: return "Example Program"
    if "marketplace" in blob: return "Marketplace"
    if "seller portal" in blob: return "Seller Portal"
    if "b2c" in blob or "super app" in blob or "pim" in blob: return "B2C SuperApp"
    if "platform" in blob: return "Platform"
    return "General"

def classify_by_emails_and_title(all_emails, title, desc="", content=""):
    """Core classification logic — shared by calendar and Fathom invitee paths.

    `content` is the recording summary/transcript when available. It is consulted
    ONLY as a last resort, to rescue a recording from the You default.
    """
    title_l = title.lower()
    # TLN check
    if "tln" in title_l or "taaruf" in title_l or any("tln" in e for e in all_emails):
        return "You", "Taaruf Lalu Nikah"
    # Enterprise domain check
    for mapping in CLIENT_MAPPINGS:
        if any(mapping['domain'] in email for email in all_emails):
            client = mapping['client']
            project = "General"
            if client == "Work":
                if "marketplace" in title_l: project = "Marketplace"
                elif "portal" in title_l: project = "Seller Portal"
                elif "b2c" in title_l or "super app" in title_l: project = "B2C SuperApp"
                elif "ExampleProgram" in title_l: project = "Example Program"
                elif "platform" in title_l: project = "Platform"
                elif "pim" in title_l: project = "B2C SuperApp"
                elif "standup" in title_l or "scrum" in title_l: project = "General"
            return client, project
    # Personal projects. An EXPLICIT You keyword is required - the personal
    # email alone proves nothing, since the owner records everything on that account.
    if any(PERSONAL_EMAIL in email for email in all_emails):
        for kw in PROJECT_KEYWORDS:
            if kw.lower() in title_l or kw.lower() in (desc or "").lower():
                return "You", kw
        # No You keyword. Before giving up, look for Work signal in the
        # content: this is the Impromptu case, where the title carries nothing.
        if work_signal(title, desc, content):
            return "Work", work_project_from_text(title, desc, content)
        # Genuinely unclassifiable. Return None so it lands in triage rather than
        # defaulting into You and crossing the data-separation boundary.
        return None, None
    # Not a personal-account meeting either, but still rescue an obvious Work one.
    if work_signal(title, desc, content):
        return "Work", work_project_from_text(title, desc, content)
    return None, None

def recording_content(meeting):
    """Summary + participant names as one blob, for content-based classification.

    An 'Impromptu Google Meet Meeting' carries no signal in its title, so the
    only thing that can tell Work from You is what was actually said and who
    said it.
    """
    if not meeting:
        return ""
    parts = []
    for key in ('default_summary', 'summary', 'ai_summary', 'meeting_title', 'title'):
        v = meeting.get(key)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            parts.append(json.dumps(v, ensure_ascii=False))
    for p in (meeting.get('participants') or []):
        parts.append(p if isinstance(p, str) else json.dumps(p, ensure_ascii=False))
    for i in (meeting.get('calendar_invitees') or []):
        if isinstance(i, dict):
            parts.append(f"{i.get('name', '')} {i.get('email', '')}")
    return " ".join(parts)[:20000]

def identify_client_and_project(event, meeting=None):
    """Determines folder structure. Uses calendar event first, falls back to Fathom invitees."""
    content = recording_content(meeting)

    # --- Path 1: Google Calendar event ---
    if event:
        organizer = event.get('organizer', {}).get('email', '').lower()
        attendees = [a.get('email', '').lower() for a in event.get('attendees', [])]
        all_emails = [organizer] + attendees
        title = event.get('summary', '')
        desc = event.get('description', '')
        client, project = classify_by_emails_and_title(all_emails, title, desc, content)
        if client:
            return client, project

    # --- Path 2: Fathom calendar_invitees (fallback when no calendar match) ---
    if meeting:
        invitees = meeting.get('calendar_invitees') or []
        all_emails = [i.get('email', '').lower() for i in invitees]
        title = meeting.get('title') or meeting.get('meeting_title', '')
        client, project = classify_by_emails_and_title(all_emails, title, "", content)
        if client:
            return client, project

    return "General", "Unsorted"

def resolve_meetings_path(path_parts):
    """Maps [client, project] to filesystem path for meeting notes."""
    client, project = path_parts[0], path_parts[1] if len(path_parts) > 1 else "General"
    if client == "You":
        # Write into the SEPARATE You repo, never into this Work repo. If the
        # sibling repo is not checked out here, triage locally instead of
        # recreating the phantom You/ directory inside PSB.
        base = YOU_REPO if YOU_REPO.is_dir() else TRIAGE_DIR
        if project == "General":
            return base / "meetings"
        return base / project / "meetings"
    if client == "Work":
        if project == "General":
            return REPO_ROOT / "Clients" / "Work" / "meetings"
        return REPO_ROOT / "Clients" / "Work" / project / "meetings"
    if client == "Secondary":
        return REPO_ROOT / "Clients" / "Secondary" / "meetings"
    return REPO_ROOT / "Clients" / "General" / "meetings"

def sanitize_filename(title):
    """Converts meeting title to a safe filename slug."""
    slug = re.sub(r'[^\w\s-]', '', title)
    slug = re.sub(r'\s+', '_', slug.strip())
    return slug[:60]

def format_transcript(transcript):
    """Converts Fathom transcript array to readable dialogue lines."""
    if not transcript:
        return "_No transcript available._"
    lines = []
    prev_speaker = None
    buffer = []
    for entry in transcript:
        speaker = entry.get("speaker", {}).get("display_name", "Unknown")
        text = entry.get("text", "").strip()
        if speaker == prev_speaker:
            buffer.append(text)
        else:
            if buffer:
                lines.append(f"**{prev_speaker}**: {' '.join(buffer)}")
            buffer = [text]
            prev_speaker = speaker
    if buffer:
        lines.append(f"**{prev_speaker}**: {' '.join(buffer)}")
    return "\n\n".join(lines)

def _owner_name(item, default=""):
    """Fathom returns assignee as an object ({name, email, team}), not a string.
    Stringifying it leaks the raw dict into the MOM table and from there into the
    commitment ledger, so always reduce it to a display name."""
    raw = item.get("assignee") or item.get("owner")
    if isinstance(raw, dict):
        raw = raw.get("name") or raw.get("email") or ""
    if not isinstance(raw, str):
        raw = "" if raw is None else str(raw)
    return raw.strip() or default

def format_action_items(action_items):
    """Formats Fathom action items into a markdown table."""
    if not action_items:
        return "| Task | Owner | Status |\n| :--- | :--- | :--- |\n| _None recorded_ | — | — |"
    rows = ["| Task | Owner | Status |", "| :--- | :--- | :--- |"]
    if isinstance(action_items, list):
        for item in action_items:
            if isinstance(item, dict):
                task = item.get("text") or item.get("description") or str(item)
                owner = _owner_name(item, "—")
                rows.append(f"| {task} | {owner} | Pending |")
            else:
                rows.append(f"| {item} | — | Pending |")
    else:
        rows.append(f"| {action_items} | — | Pending |")
    return "\n".join(rows)

def _fathom_items_as_text(action_items):
    """Flatten Fathom's action_items into plain lines, for LLM dedup context."""
    if not action_items:
        return "(none)"
    out = []
    if isinstance(action_items, list):
        for item in action_items:
            if isinstance(item, dict):
                task = item.get("text") or item.get("description") or str(item)
                owner = _owner_name(item)
                out.append(f"- {task}" + (f" (owner: {owner})" if owner else ""))
            else:
                out.append(f"- {item}")
    else:
        out.append(f"- {action_items}")
    return "\n".join(out) or "(none)"

def detect_extra_action_items(transcript, fathom_items):
    """Scan the transcript via agy-bridge (GLM 5.2) for action items Fathom missed.

    Returns (items, note): items is a list of {"task","owner","due"} dicts (may be empty);
    note is a short status string for the reader. On any failure or on the bridge's
    fallback-to-claude sentinel (exit 3) we return ([], note) and never fabricate results —
    this runs headless with no Claude in the loop to honor the fallback.
    """
    if not LLM_ACTION_ITEMS:
        return [], None
    transcript_md = format_transcript(transcript)
    if not transcript or transcript_md.startswith("_No transcript"):
        return [], None
    if not AGY_BRIDGE.exists():
        return [], "_Transcript scan skipped (agy-bridge not found)._"

    existing = _fathom_items_as_text(fathom_items)
    prompt = (
        "You are extracting ACTION ITEMS from a meeting transcript. An action item is a "
        "concrete commitment someone made to DO something after the meeting (a task + an owner). "
        "Include implicit commitments, e.g. \"I'll send you the doc\", \"let me check with X\", "
        "\"we need to update Y before Friday\", and Indonesian ones like \"gw follow up ...\", "
        "\"tolong kerjain ...\", \"nanti aku share ...\".\n\n"
        "Fathom already auto-detected the items below. DO NOT repeat these or near-duplicates:\n"
        f"{existing}\n\n"
        "Read the transcript and return ONLY action items Fathom MISSED. Respond with a STRICT "
        "JSON array and nothing else. Each element: {\"task\": \"...\", \"owner\": \"...\", "
        "\"due\": \"...\"}. Use \"—\" when owner or due is unknown. If nothing was missed, return [].\n\n"
        "Transcript:\n"
        f"{transcript_md}"
    )

    tmp_path = None
    proc = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tf:
            tf.write(prompt)
            tmp_path = tf.name
        # Force GLM 5.2 (cheap + large context). Forcing a single model drops the fallback
        # chain, so we retry once to ride out a transient blip (e.g. GLM peak-hour throttle).
        for attempt in range(2):
            try:
                proc = subprocess.run(
                    [sys.executable, str(AGY_BRIDGE), "--task", "harvest",
                     "--model", "glm-5.2", "--backend", "zai", "--prompt-file", tmp_path],
                    cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=240,
                )
            except Exception as e:
                if attempt == 1:
                    return [], f"_Transcript scan skipped (bridge error: {e})._"
                continue
            if proc.returncode == 0:
                break  # got an answer; stop retrying
            # exit 3 = fallback_to_claude sentinel, or any other non-zero = transient error.
            # Retry once; on the second miss, fall through to the honest-skip notes below.
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if proc is None or proc.returncode == 3:
        # fallback_to_claude sentinel — no Claude here; do not fabricate.
        return [], "_Transcript scan skipped (LLM bridge unavailable — fell back to Claude tier)._"
    if proc.returncode != 0:
        return [], "_Transcript scan skipped (LLM bridge error)._"

    raw = (proc.stdout or "").strip()
    # Strip code fences / prose around the JSON array.
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return [], "_Transcript scan ran but returned no parseable items._"
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return [], "_Transcript scan ran but returned malformed JSON._"
    if not isinstance(items, list):
        return [], None
    cleaned = []
    for it in items:
        if isinstance(it, dict) and (it.get("task") or "").strip():
            cleaned.append({
                "task": str(it.get("task")).strip(),
                "owner": (str(it.get("owner")).strip() or "—"),
                "due": (str(it.get("due")).strip() or "—"),
            })
    return cleaned, None

def render_action_items_section(fathom_items, transcript):
    """Fathom's items + an auto-detected 'missed' subsection from the transcript scan."""
    section = format_action_items(fathom_items)
    extra, note = detect_extra_action_items(transcript, fathom_items)
    if extra:
        rows = ["| Task | Owner | Due |", "| :--- | :--- | :--- |"]
        for it in extra:
            rows.append(f"| {it['task']} | {it['owner']} | {it['due']} |")
        section += (
            "\n\n**🔎 Additional items detected from transcript** "
            "_(auto-scan, unverified — Fathom missed these):_\n\n" + "\n".join(rows)
        )
    elif note:
        section += f"\n\n{note}"
    return section

def generate_meeting_note(result):
    """Generates a structured .md meeting note from a fathom_sync result. Returns True if written."""
    meeting = result.get("meeting", {})
    path_parts = result.get("path_parts", ["General", "Unsorted"])

    title = meeting.get("title") or meeting.get("meeting_title") or "Untitled Meeting"
    recording_id = meeting.get("recording_id") or meeting.get("id", "")
    share_url = meeting.get("share_url") or meeting.get("url") or ""

    # Parse times
    start_str = meeting.get("recording_start_time") or meeting.get("scheduled_start_time") or ""
    end_str = meeting.get("recording_end_time") or meeting.get("scheduled_end_time") or ""
    date_label = "Unknown"
    time_label = "—"
    duration_label = "—"

    if start_str:
        try:
            start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            # Convert UTC → WIB (UTC+7)
            from datetime import timezone as tz
            wib = timezone(timedelta(hours=7))
            start_wib = start_dt.astimezone(wib)
            date_label = start_wib.strftime("%Y-%m-%d")
            time_label = start_wib.strftime("%H:%M")
            if end_str:
                end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
                end_wib = end_dt.astimezone(wib)
                time_label += f" – {end_wib.strftime('%H:%M')} WIB"
                duration_min = int((end_dt - start_dt).total_seconds() / 60)
                duration_label = f"{duration_min} minutes"
        except Exception:
            pass

    # Participants
    invitees = meeting.get("calendar_invitees") or []
    participants = ", ".join(
        i.get("name") or i.get("email", "Unknown") for i in invitees
    ) or "—"

    # Content fields
    summary = meeting.get("default_summary") or "_No summary available._"
    action_items_md = render_action_items_section(meeting.get("action_items"), meeting.get("transcript"))
    transcript_md = format_transcript(meeting.get("transcript"))

    # Build markdown
    note = f"""# Meeting Notes: {title}

| Field | Value |
| :--- | :--- |
| Date | {date_label} |
| Time | {time_label} |
| Duration | {duration_label} |
| Participants | {participants} |
| Fathom Recording | [View Recording]({share_url}) |

---

## 📌 Executive Summary

{summary}

---

## ✅ Action Items

{action_items_md}

---

## 📝 Full Transcript

<details>
<summary>Expand transcript</summary>

{transcript_md}

</details>
"""

    # Write file
    meetings_dir = resolve_meetings_path(path_parts)
    meetings_dir.mkdir(parents=True, exist_ok=True)
    slug = sanitize_filename(title)
    base_filename = f"{date_label}_{slug}"
    # Add time suffix to avoid collisions (multiple meetings on same day with same title)
    if start_str:
        try:
            _start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            _wib = timezone(timedelta(hours=7))
            _time_suffix = _start_dt.astimezone(_wib).strftime("%H%M")
            base_filename = f"{date_label}_{_time_suffix}_{slug}"
        except Exception:
            pass
    filename = f"{base_filename}.md"
    filepath = meetings_dir / filename

    if filepath.exists():
        return False  # already exists, skip

    filepath.write_text(note, encoding="utf-8")
    # os.path.relpath, not Path.relative_to: You notes land in the sibling
    # repo outside REPO_ROOT, where relative_to raises ValueError.
    print(f"  [note] Written: {os.path.relpath(filepath, REPO_ROOT)}")
    return True

def process_sync():
    f_token = fathom_client.load_fathom_token()
    c_token = refresh_gcal_token()
    
    if not f_token or not c_token:
        print("Missing API tokens.")
        return

    # Fetch data (look back 2 days for verification)
    meetings = fathom_client.list_meetings(f_token, limit=20, include_all=True)
    events = fetch_calendar_events(c_token, days_back=2)
    
    results = []
    # fathom_client.list_meetings returns the 'items' list directly now
    for m in meetings:
        event = match_meeting_to_event(m, events)
        m_id = m.get("recording_id") or m.get("id")
        m_title = m.get("title") or m.get("meeting_title", "Untitled")

        # Registry (calendar-resolved) wins over the invitee/title heuristic for
        # enterprise clients — prevents Work meetings joined on the owner's personal
        # account from being misfiled into You/meetings.
        reg_client, reg_project = registry_lookup(m_id)
        if reg_client:
            client, project = reg_client, reg_project
        else:
            client, project = identify_client_and_project(event, meeting=m)
        
        # Print for logs
        print(f"\n--- MATCH FOUND ---")
        print(f"Fathom ID: {m_id}")
        print(f"Fathom Title: {m_title}")
        print(f"Calendar Title: {event['summary'] if event else 'N/A'}")
        print(f"Target: {client}/{project}")
        
        results.append({
            "meeting": m,  # In v1, transcription is already in the meeting object if requested
            "calendar": event,
            "path_parts": [client, project]
        })
    
    # Save results
    os.makedirs("_temp", exist_ok=True)
    with open("_temp/fathom_sync_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSync complete. Results saved to _temp/fathom_sync_results.json.")

    # Generate local meeting note .md files
    notes_written = 0
    notes_skipped = 0
    notes_failed = 0
    for result in results:
        # One bad meeting must not kill the rest of the batch.
        try:
            if generate_meeting_note(result):
                notes_written += 1
            else:
                notes_skipped += 1
        except Exception as e:
            notes_failed += 1
            m_title = (result.get("meeting") or {}).get("title") or "Untitled"
            print(f"  [note] FAILED ({m_title}): {e}")
    print(f"Meeting notes: {notes_written} written, {notes_skipped} already existed, {notes_failed} failed.")

if __name__ == "__main__":
    process_sync()
