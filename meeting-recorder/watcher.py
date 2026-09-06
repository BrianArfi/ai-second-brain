#!/usr/bin/env python3
"""Watcher daemon: turns finished local recordings into transcripts + MOM drafts.

Runs on the repo host (WSL or macOS). Polls <recordings_dir> from config.json;
for each finished recording (no .recording marker, file stable) it:
  1. Mixes Windows two-part captures (<base>.sys.wav + <base>.mic.wav) into one WAV.
  2. Transcribes via transcribe.py (whisper.cpp GPU -> Gemini CLI; never CPU).
  3. Registers the recording in journal/fathom_registry.json as "local-<epoch>"
     so /mom resolves it like a Fathom call (best-effort calendar match for title).
  4. Drafts a MOM via agy-bridge (harvest -> draft, templates/mom_work.md); if the
     bridge signals fallback_to_claude (exit 3), saves transcript only and flags
     the recording as "needs /mom manual".
  5. Writes heartbeat + activity log rows.

Usage:
  python3 watcher.py                 # poll loop (30s)
  python3 watcher.py --once          # single scan, then exit
  python3 watcher.py --file <audio>  # process one specific file, then exit
"""
import argparse
import datetime
import json
import os
import platform
import subprocess
import sys
import threading
import time

from common import REPO_ROOT, load_config, parse_json_tail, slugify
from transcribe import transcribe

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(MODULE_DIR, "state.json")
REGISTRY_PATH = os.path.join(REPO_ROOT, "journal", "fathom_registry.json")
TRANSCRIPTS_DIR = os.path.join(REPO_ROOT, "Clients", "Work", "meetings", "transcripts")
MOM_DIR = os.path.join(REPO_ROOT, "Clients", "Work", "meetings")
MOM_TEMPLATE = os.path.join(REPO_ROOT, "templates", "mom_work.md")
AGY_BRIDGE = os.path.join(REPO_ROOT, ".agent", "skills", "agy-bridge", "run.py")
HEARTBEAT = os.path.join(REPO_ROOT, ".agent", "scripts", "heartbeat.py")
ACTIVITY_LOG = os.path.join(REPO_ROOT, ".agent", "scripts", "activity_log.py")
GCAL = os.path.join(REPO_ROOT, ".agent", "skills", "google-calendar-connector", "gcal_manager.py")

# Historical state entries hold absolute WSL paths from the automation host.
# See _rebase below.
LEGACY_PREFIX = "./"

AUDIO_EXTS = (".wav", ".m4a", ".mp3", ".ogg", ".flac")
WIB = datetime.timezone(datetime.timedelta(hours=7))

CALENDAR_MATCH_WINDOW_SEC = 30 * 60

# Calendar blocks that are never a meeting. A recording that matches one of
# these gets filed under that title and produces a MOM with no meeting content,
# which reads as done and hides the real miss.
NON_MEETING_BLOCKS = (
    "prayer", "focus time", "home", "lunch", "break", "ooo",
    "out of office", "leave", "holiday", "remind", "reminder", "travel",
)

def is_non_meeting_block(summary):
    s = (summary or "").strip().lower()
    if not s:
        return True
    return any(tok in s for tok in NON_MEETING_BLOCKS)

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"processed": {}}

def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)

def heartbeat(status, summary):
    try:
        subprocess.run([sys.executable, HEARTBEAT, "--job", "local-recorder",
                        "--status", status, "--summary", summary],
                       capture_output=True, timeout=30)
    except Exception:
        pass

def activity(target, summary):
    try:
        subprocess.run([sys.executable, ACTIVITY_LOG, "--actor", "agent",
                        "--action", "transcribe", "--project", "Other",
                        "--target", target, "--summary", summary],
                       capture_output=True, timeout=30)
    except Exception:
        pass

# ---------- discovery ----------

def mix_parts(base, ffmpeg):
    """Merge <base>.sys.wav + <base>.mic.wav into <base>.wav (Windows captures)."""
    sysw, micw, out = base + ".sys.wav", base + ".mic.wav", base + ".wav"
    parts = [p for p in (sysw, micw) if os.path.exists(p)]
    if not parts or os.path.exists(out):
        return
    cmd = [ffmpeg, "-y", "-v", "quiet"]
    for p in parts:
        cmd += ["-i", p]
    if len(parts) > 1:
        cmd += ["-filter_complex", "amix=inputs=2:duration=longest:normalize=0"]
    cmd += ["-ac", "1", "-ar", "16000", out]
    subprocess.run(cmd, check=True, timeout=1800)
    print(f"[watcher] mixed {len(parts)} part(s) -> {out}")

# Retry ladder for files that failed. The cron only runs 12:00-22:59 WIB and the box is
# off overnight, so the last rung has to be long enough to survive an evening break plus
# a night off plus a human fixing the environment the next day. Retrying is cheap: both
# engines shell ffmpeg before spending anything, so a genuinely broken file dies for free.
RETRY_DELAYS = [600, 1800, 7200, 21600, 86400]
MAX_ATTEMPTS = len(RETRY_DELAYS)

def retryable(entry, now):
    """True if this processed-entry is a failure that has earned another attempt.

    Success entries have no "failed" key, so they are skipped forever by falling through
    to False -- including the hand-written ones ("finalized (GDoc ...)") that no code path
    produces. Nothing here parses a status string.
    """
    if not isinstance(entry, dict) or not entry.get("failed"):
        return False
    if entry.get("attempts", 0) >= MAX_ATTEMPTS:
        return False  # quarantined; recover by hand with --file
    return now >= entry.get("next_attempt", 0)

def find_candidates(rec_dir, state, ffmpeg):
    if not os.path.isdir(rec_dir):
        return []
    # first, mix any finished two-part Windows captures
    for f in os.listdir(rec_dir):
        if f.endswith(".sys.wav") or f.endswith(".mic.wav"):
            base = os.path.join(rec_dir, f.rsplit(".", 2)[0])
            if not os.path.exists(base + ".recording"):
                try:
                    mix_parts(base, ffmpeg)
                except (subprocess.SubprocessError, OSError) as e:
                    # OSError matters: a missing ffmpeg raises FileNotFoundError, which is
                    # NOT a SubprocessError. It used to escape all the way out of scan_once
                    # (find_candidates is called outside its try), killing the whole run
                    # before any file was looked at -- no state entry, no heartbeat.
                    print(f"[watcher] mix failed for {base}: {e}", file=sys.stderr)

    out = []
    now = time.time()
    for f in sorted(os.listdir(rec_dir)):
        path = os.path.join(rec_dir, f)
        base, ext = os.path.splitext(path)
        if ext.lower() not in AUDIO_EXTS or base.endswith((".sys", ".mic")):
            continue
        if os.path.exists(base + ".recording"):
            continue  # still recording
        # a sidecar .json means the recorder finished cleanly -> final file;
        # the 60s stability window only guards files copied in manually
        if not os.path.exists(base + ".json") and now - os.path.getmtime(path) < 60:
            continue  # not stable yet
        entry = state["processed"].get(path)
        if entry is not None and not retryable(entry, now):
            continue
        out.append(path)
    return out

# ---------- registry + calendar ----------

def read_sidecar(base):
    meta_path = base + ".json"
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def calendar_match(start_wib, cfg):
    """Best-effort: find a Work calendar event overlapping the recording start.

    Picks the NEAREST event, not the first one inside the window: a 15:04
    recording must not claim a 15:15 "Prayer" block over the 14:30 standup it
    actually belongs to. Non-meeting blocks are skipped outright, since a
    recording matched to one produces a MOM that reads as done but holds no
    meeting content, which is worse than no match at all."""
    if not cfg.get("calendar_match", True):
        return None
    try:
        r = subprocess.run([sys.executable, GCAL, "list", "--profile", "work",
                            "--days-back", "2", "--days-forward", "0", "--json"],
                           capture_output=True, text=True, timeout=120)
        events = parse_json_tail(r.stdout)
        if isinstance(events, dict):
            events = events.get("events", [])
        candidates = []
        for ev in events:
            summary = (ev.get("summary") or "").strip()
            if not summary or is_non_meeting_block(summary):
                continue
            raw = ev.get("start") or {}
            st = raw.get("dateTime") if isinstance(raw, dict) else raw
            if not st:
                continue
            ev_start = datetime.datetime.fromisoformat(st.replace("Z", "+00:00"))
            if ev_start.tzinfo is None:  # all-day/naive events: assume WIB
                ev_start = ev_start.replace(tzinfo=WIB)
            delta = abs((ev_start - start_wib).total_seconds())
            if delta <= CALENDAR_MATCH_WINDOW_SEC:
                candidates.append((delta, summary))
        if candidates:
            return min(candidates, key=lambda c: c[0])[1]
    except Exception as e:
        print(f"[watcher] calendar match skipped: {e}", file=sys.stderr)
    return None

def register_recording(audio_path, meta, matched, duration_sec):
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        registry = json.load(f)
    rec_id = f"local-{int(time.time())}"
    start_utc = meta.get("start_utc")
    if start_utc:
        start_dt = datetime.datetime.fromisoformat(start_utc)
    else:
        start_dt = datetime.datetime.fromtimestamp(
            os.path.getmtime(audio_path), datetime.timezone.utc)
    start_wib = start_dt.astimezone(WIB)
    registry[rec_id] = {
        "recording_id": rec_id,
        "local_path": audio_path,
        "date_wib": start_wib.strftime("%Y-%m-%d"),
        "time_wib": start_wib.strftime("%H:%M"),
        "start_utc": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration": f"{max(1, round((duration_sec or 0) / 60))} min",
        "raw_title": meta.get("title", os.path.basename(audio_path)),
        "matched_meeting": matched,
        "match_source": "local-recorder",
        "confidence": "high" if matched else "medium",
        "client": "Work",
        "project": None,
        "participants": [],
        "transcript_language": None,
        "last_synced_utc": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    os.replace(tmp, REGISTRY_PATH)
    return rec_id, start_wib

def update_registry_entry(rec_id, **fields):
    """Merge fields into one registry entry (atomic write)."""
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        registry = json.load(f)
    if rec_id not in registry:
        return
    registry[rec_id].update(fields)
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    os.replace(tmp, REGISTRY_PATH)

def find_related(matched, date_wib, exclude_id=None):
    """Other registry entries covering the same calendar meeting on the same
    date (Fathom + Vexa + local all land here, so this is the dedupe key).
    Returns [(rec_id, entry), ...]."""
    if not matched:
        return []
    key = matched.strip().lower()
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        registry = json.load(f)
    return [(rid, e) for rid, e in registry.items()
            if rid != exclude_id and e.get("date_wib") == date_wib
            and (e.get("matched_meeting") or "").strip().lower() == key]

def link_related(rec_id, related):
    """Cross-reference duplicate recordings of one meeting, both directions."""
    if not related:
        return
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        registry = json.load(f)
    ids = [rid for rid, _ in related]
    mine = registry.get(rec_id, {})
    mine["related_recordings"] = sorted(set(mine.get("related_recordings", []) + ids))
    for rid in ids:
        other = registry.get(rid)
        if other is not None:
            other["related_recordings"] = sorted(
                set(other.get("related_recordings", []) + [rec_id]))
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    os.replace(tmp, REGISTRY_PATH)

def _rebase(p):
    """Legacy state entries hold absolute WSL paths. Rebase onto this
    checkout when the original does not exist, so historical lookups keep
    working on macOS. No-op on the WSL host, where the path resolves."""
    if p and p.startswith(LEGACY_PREFIX) and not os.path.exists(p):
        return os.path.join(REPO_ROOT, p[len(LEGACY_PREFIX):])
    return p

def existing_mom(related):
    """Path of an already-drafted MOM among related recordings, if any."""
    for rid, e in related:
        p = _rebase(e.get("mom_path"))
        if p and os.path.exists(os.path.join(REPO_ROOT, p)):
            return rid, p
    return None, None

# ---------- MOM drafting via agy-bridge ----------

def agy(task, prompt, workdir, model=None, backend=None):
    """Run agy-bridge; returns text or None on fallback_to_claude (exit 3).

    With model/backend set, tries that model first and falls back to the
    task's normal chain if it fails (agentic CLI models flake sometimes).
    """
    pf = os.path.join(workdir, f"prompt_{task}.txt")
    with open(pf, "w", encoding="utf-8") as f:
        f.write(prompt)
    cmd = [sys.executable, AGY_BRIDGE, "--task", task, "--prompt-file", pf]
    if model:
        r = subprocess.run(cmd + ["--model", model] +
                           (["--backend", backend] if backend else []),
                           capture_output=True, text=True, timeout=1200)
        if r.returncode == 0:
            return _strip_narration(r.stdout.strip())
        print(f"[watcher] forced model '{model}' failed, using default chain",
              file=sys.stderr)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if r.returncode == 3:
        return None
    if r.returncode != 0:
        raise RuntimeError(f"agy-bridge {task} failed: {r.stderr[-300:]}")
    return _strip_narration(r.stdout.strip())

def _strip_narration(text):
    """Drop agentic-CLI preamble ('I have created...') before the MOM body."""
    idx = text.find("# MOM")
    if idx == -1:
        idx = text.find("\n# ")
        idx = idx + 1 if idx != -1 else -1
    return text[idx:].strip() if idx > 0 else text

def resolve_speakers(transcript_md, attendees):
    """Put names on the "Speaker N" labels, using the confidence ladder in
    speaker_map.py. Returns (resolved {label: name}, still_open [label]).

    Never fatal: a resolver failure must not cost the MOM, so the transcript is
    left exactly as the ASR produced it and every label reports as open."""
    try:
        sys.path.insert(0, MODULE_DIR)
        import speaker_map
        result = speaker_map.resolve(transcript_md, attendees or [])
        if not result["labels"]:
            return {}, []
        speaker_map.merge_into_store(transcript_md, result)
        resolved = speaker_map.trusted_map(transcript_md)
        if resolved:
            speaker_map.cmd_apply(
                argparse.Namespace(transcript=transcript_md, dry_run=False))
        entry = speaker_map.load_store()["maps"][speaker_map.store_key(transcript_md)]
        still_open = speaker_map.unresolved(entry)
        print(f"[watcher] speakers: {len(resolved)} named, {len(still_open)} still open")
        return resolved, still_open
    except Exception as e:                                   # noqa: BLE001
        print(f"[watcher] speaker resolution skipped: {e}", file=sys.stderr)
        return {}, []

def draft_mom(transcript_md, title, start_wib, matched, scratch, cfg=None,
              attendees=None, unresolved_speakers=None):
    cfg = cfg or {}
    roster = ", ".join(attendees) if attendees else ""
    with open(transcript_md, encoding="utf-8") as f:
        transcript = f.read()
    with open(MOM_TEMPLATE, encoding="utf-8") as f:
        template = f.read()

    facts = agy("harvest",
                "You are extracting raw facts from a meeting transcript for minutes.\n"
                "Return, as plain structured text: participants (from speaker labels/"
                "context), topics discussed with key points, decisions made with "
                "rationale, action items with owner and deadline if stated, notable "
                "quotes. Do NOT synthesize or prioritize; facts only. Keep the "
                "original language of quotes.\n"
                + (f"Known attendees, in no particular order: {roster}. Map the "
                   "speaker labels onto these names ONLY where the transcript "
                   "makes the mapping unambiguous; otherwise keep the raw label.\n"
                   if roster else "")
                + (f"These labels are still unidentified and were checked already: "
                   f"{', '.join(unresolved_speakers)}. Keep them as labels. Do NOT "
                   "assign them a name, and do NOT drop the action items they "
                   "speak: attribute those to the raw label.\n"
                   if unresolved_speakers else "")
                + "\n=== TRANSCRIPT ===\n" + transcript,
                scratch)
    if facts is None:
        return None

    meeting_line = matched or title
    mom = agy("draft",
              "Write meeting minutes (MOM) in ENGLISH following EXACTLY this markdown "
              "template structure (replace placeholders, keep the section order and "
              "table formats). No em-dashes anywhere. Meeting: "
              f"{meeting_line}. Date: {start_wib.strftime('%Y-%m-%d')}, start "
              f"{start_wib.strftime('%H:%M')} WIB."
              + (f" Attendees: {roster}." if roster else "")
              + f"\n\n=== TEMPLATE ===\n{template}\n\n"
              "=== EXTRACTED FACTS ===\n" + facts,
              scratch,
              model=cfg.get("draft_model"), backend=cfg.get("draft_backend"))
    return mom

# ---------- main processing ----------

def process(audio_path, cfg, state):
    name = os.path.basename(audio_path)
    base = os.path.splitext(audio_path)[0]
    meta = read_sidecar(base)
    title = meta.get("title") or os.path.splitext(name)[0]
    print(f"[watcher] processing: {name} ({title})")
    # Named in the heartbeat so a reader can tell "working on this one" from "queue not moving".
    set_busy(title)

    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
    slug = slugify(title)
    transcript_md = os.path.join(TRANSCRIPTS_DIR, os.path.splitext(name)[0] + ".md")
    transcript_md, engine_note = transcribe(audio_path, transcript_md, cfg=cfg)

    duration = meta.get("duration_sec", 0)
    rec_id, start_wib = register_recording(audio_path, meta, None, duration)
    ad_hoc = bool(meta.get("ad_hoc"))
    attendees = meta.get("attendees") or []
    if ad_hoc:
        # the owner created this meeting himself; the typed title is authoritative.
        # Matching it to an overlapping calendar event would rename the MOM,
        # brief the wrong room, and dedupe it against an unrelated recording.
        matched = None
        update_registry_entry(rec_id, matched_meeting=title, confidence="high",
                              match_source="local-recorder-adhoc",
                              participants=attendees)
        print(f"[watcher] ad-hoc meeting, calendar match skipped: {title}")
    else:
        matched = calendar_match(start_wib, cfg)
        if matched:
            update_registry_entry(rec_id, matched_meeting=matched, confidence="high")
        if attendees:
            update_registry_entry(rec_id, participants=attendees)
    video = base + ".mp4"
    if os.path.exists(video):
        update_registry_entry(rec_id, video_path=video)
        print(f"[watcher] video sidecar registered: {video}")

    # dedupe: one meeting -> one MOM (Vexa / Fathom / local all share the registry)
    related = find_related(matched, start_wib.strftime("%Y-%m-%d"), exclude_id=rec_id)
    link_related(rec_id, related)
    dup_rid, dup_mom = existing_mom(related)

    mom_path, status = None, "transcribed"
    if dup_mom:
        status = f"transcribed (duplicate of {dup_rid}, MOM draft skipped)"
        print(f"[watcher] {status} -> {dup_mom}")
    elif cfg.get("auto_draft", True):
        # Name the "Speaker N" labels BEFORE the MOM is drafted, so an action
        # item spoken by a resolved voice carries its owner instead of being
        # dropped. Only tiers that cannot be wrong are written back; everything
        # else stays a proposal in `speaker_map.py pending`.
        resolved, still_open = resolve_speakers(transcript_md, attendees)
        scratch = os.path.join(MODULE_DIR, "scratch")
        os.makedirs(scratch, exist_ok=True)
        try:
            mom = draft_mom(transcript_md, title, start_wib, matched, scratch, cfg,
                            attendees=attendees, unresolved_speakers=still_open)
        except RuntimeError as e:
            print(f"[watcher] draft failed: {e}", file=sys.stderr)
            mom = None
        if mom:
            mom_path = os.path.join(
                MOM_DIR, f"MOM_{slug}_{start_wib.strftime('%Y-%m-%d')}.md")
            header = (f"> Status: DRAFT (local pipeline, belum direview)\n"
                      f"> Source: local recording `{name}`, {engine_note}\n"
                      f"> Registry: {rec_id} | Review via /mom before sharing\n\n")
            with open(mom_path, "w", encoding="utf-8") as f:
                f.write(header + mom + "\n")
            status = "drafted"
            update_registry_entry(rec_id, mom_path=os.path.relpath(mom_path, REPO_ROOT))
            print(f"[watcher] MOM draft -> {mom_path}")
        else:
            status = "needs /mom manual (agy-bridge fallback_to_claude)"
            print(f"[watcher] {status}")

    state["processed"][audio_path] = {
        "rec_id": rec_id, "transcript": transcript_md, "mom": mom_path,
        "status": status,
        "ts": datetime.datetime.now(WIB).isoformat(timespec="seconds"),
    }
    save_state(state)
    heartbeat("ok", f"{name}: {status} ({rec_id})")
    activity(rec_id, f"local recording {name}: {status}")

def scan_once(cfg, state):
    rec_dir = cfg["machine"].get("recordings_dir", "")
    ffmpeg = cfg["machine"].get("ffmpeg", "ffmpeg")
    for path in find_candidates(rec_dir, state, ffmpeg):
        try:
            process(path, cfg, state)
        except Exception as e:  # noqa: BLE001 -- one bad file must not end the watch
            prev = state["processed"].get(path)
            attempts = (prev.get("attempts", 0) if isinstance(prev, dict) else 0) + 1
            quarantined = attempts >= MAX_ATTEMPTS
            delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
            name = os.path.basename(path)
            print(f"[watcher] FAILED ({attempts}/{MAX_ATTEMPTS}) {path}: {e}", file=sys.stderr)
            state["processed"][path] = {
                "status": (f"QUARANTINED after {attempts} attempts: {e}" if quarantined
                           else f"failed ({attempts}/{MAX_ATTEMPTS}), will retry: {e}"),
                "failed": True,
                "attempts": attempts,
                "next_attempt": time.time() + delay,
                "last_error": str(e)[:1000],
                "ts": datetime.datetime.now(WIB).isoformat(timespec="seconds")}
            save_state(state)
            # Only shout when we have actually given up. Every retry firing a fail
            # heartbeat would train the owner to ignore the channel that matters.
            if quarantined:
                heartbeat("fail", f"{name}: QUARANTINED after {attempts} attempts, "
                                  f"no further retries -- recover with "
                                  f"'watcher.py --file <path>'. Last error: {e}")
        finally:
            set_busy(None)

# ---------- liveness ----------
#
# The ASB app's corner feed used to label every un-transcribed recording "transcription
# running", because the only fact it had was "audio on disk, not yet in state.json". That is
# true whether this watcher is working through the file or has not been started at all, and on
# 24 Aug 2026 four meetings sat there for ten hours reading as work in progress while nothing
# was running. A count of hours is not a symptom anyone can act on; "the transcriber is not
# running" is.
#
# So the watcher says it is alive, every poll, in one small file. Absent or stale means not
# running, and the app says exactly that instead of guessing.

HEARTBEAT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "watcher_heartbeat.json")

# The beat runs on its own thread, NOT on the poll loop. `scan_once` transcribes synchronously and
# one meeting can hold it for an hour, so a beat written by the loop would go stale during exactly
# the work it is meant to prove -- and the app would report "the transcriber is not running" about
# a machine that was busy transcribing.
HEARTBEAT_TICK_S = 10

# What the beat says it is working on, set by `process` and read by the beat thread.
_busy = None
_busy_lock = threading.Lock()

def set_busy(name):
    global _busy
    with _busy_lock:
        _busy = name

def write_heartbeat(cfg, interval):
    """One line of proof that this process is alive. Atomic: the reader polls it every few
    seconds and a half-written file would read as a dead watcher."""
    with _busy_lock:
        busy = _busy
    row = {
        "pid": os.getpid(),
        "at": round(time.time(), 3),
        "interval_s": interval,
        "tick_s": HEARTBEAT_TICK_S,
        "device": cfg.get("machine", {}).get("name") or platform.node(),
        "recordings_dir": cfg.get("machine", {}).get("recordings_dir"),
        "busy": busy,
    }
    tmp = HEARTBEAT_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(row, f, indent=1)
        os.replace(tmp, HEARTBEAT_PATH)
    except OSError:
        pass            # a missed beat is a stale reading, never a reason to stop working

def start_heartbeat(cfg, interval):
    """Beats until the process exits. Daemon, so Ctrl-C still ends the watcher at once."""
    def beat():
        while True:
            write_heartbeat(cfg, interval)
            time.sleep(HEARTBEAT_TICK_S)

    write_heartbeat(cfg, interval)
    threading.Thread(target=beat, daemon=True, name="watcher-heartbeat").start()

def report_status(state):
    now = time.time()
    rows = [(p, e) for p, e in state["processed"].items()
            if isinstance(e, dict) and e.get("failed")]
    if not rows:
        print("no failing recordings")
        return
    for path, e in sorted(rows, key=lambda r: r[1].get("attempts", 0), reverse=True):
        attempts = e.get("attempts", 0)
        if attempts >= MAX_ATTEMPTS:
            when = "QUARANTINED -- will not retry"
        else:
            mins = max(0, int((e.get("next_attempt", 0) - now) / 60))
            when = f"retry in ~{mins}m"
        print(f"{os.path.basename(path)}\n  {attempts}/{MAX_ATTEMPTS} attempts | {when}\n"
              f"  {e.get('last_error', e.get('status', ''))[:160]}\n  {path}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single scan, then exit")
    ap.add_argument("--file", help="process one specific audio file, then exit")
    ap.add_argument("--status", action="store_true",
                    help="list failing/quarantined recordings, then exit")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()

    # state before config: --status has to work even when config.json is what broke.
    state = load_state()
    if args.status:
        report_status(state)
        return
    cfg = load_config()
    if args.file:
        path = os.path.abspath(args.file)
        state["processed"].pop(path, None)
        process(path, cfg, state)
        return
    if args.once:
        scan_once(cfg, state)
        return
    print(f"[watcher] polling {cfg['machine'].get('recordings_dir')} "
          f"every {args.interval}s (Ctrl-C to stop)")
    start_heartbeat(cfg, args.interval)
    while True:
        scan_once(cfg, state)
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
