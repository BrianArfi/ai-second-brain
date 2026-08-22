#!/usr/bin/env python3
"""Vexa bot integration: send note-taker bots to meetings the owner doesn't attend.

Talks to the self-hosted Vexa Lite API (localhost:8056). Transcription is served
by the local whisper-server.exe (Vulkan, Radeon) that Vexa is configured to use.
Transcripts come back with real speaker names (from the meeting UI), then flow
into the same pipeline as local recordings: registry + MOM draft.

Commands:
  setup                       one-time: create Vexa user + API key
  send  --meet <url|code> [--title T] [--platform google_meet|teams] [--passcode P]
  pull  --meet <url|code> [--title T]     fetch transcript -> registry + MOM draft
  stop  --meet <url|code>                 make the bot leave
  status                                  list known meetings/bots
  auto                                    cron mode: join upcoming calendar meetings,
                                          pull + process finished ones

State: meeting-recorder/vexa_state.json ; API key: meeting-recorder/vexa_token.env
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, MODULE_DIR)
from common import REPO_ROOT, fmt_ts, load_config, parse_json_tail, slugify  # noqa: E402
import watcher as W  # noqa: E402  (register/draft/heartbeat reuse)

API_BASE = os.environ.get("VEXA_API_BASE", "http://localhost:8056")
TOKEN_PATH = os.path.join(MODULE_DIR, "vexa_token.env")
STATE_PATH = os.path.join(MODULE_DIR, "vexa_state.json")
ENV_PATH = os.path.join(os.path.expanduser("~"), "tools", "vexa", ".env")
GCAL = os.path.join(REPO_ROOT, ".agent", "skills", "google-calendar-connector", "gcal_manager.py")
MEET_RE = re.compile(r"meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})")
WIB = datetime.timezone(datetime.timedelta(hours=7))
BOT_NAME = "Your Name"
WHISPER_PORT = 8083
MEETBOT_PORT = 8060  # the Rust replacement; vexa-lite is 8056

def meetbot_mode():
    """True when this client is pointed at the Rust `meetbot` server instead of
    the vexa-lite container.

    Two container-maintenance behaviours below are vexa-only and actively wrong
    against meetbot: rewriting TRANSCRIPTION_SERVICE_URL in ~/tools/vexa/.env
    (meetbot resolves the WSL gateway IP itself, per call, via Config::gateway_ip)
    and `docker restart vexa-lite` (meetbot is not that container, and vexa must
    stay untouched because it is the rollback path). Both are gated on this.

    Detection is explicit only -- never guessed:
      * MEETBOT=1/0 in the environment wins outright, and
      * otherwise VEXA_API_BASE naming meetbot's port.
    With MEETBOT unset and VEXA_API_BASE unset or on :8056, this is False and
    every legacy code path runs exactly as it did before."""
    flag = os.environ.get("MEETBOT", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    try:
        return urllib.parse.urlsplit(API_BASE).port == MEETBOT_PORT
    except ValueError:  # malformed port in the URL -> assume legacy vexa
        return False

def gateway_ip():
    """WSL gateway IP = the Windows host running whisper-server.exe.
    Changes across reboots, so resolve it live instead of hardcoding."""
    try:
        out = subprocess.run(["ip", "route", "show", "default"],
                             capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "172.25.32.1"

def whisper_url():
    return f"http://{gateway_ip()}:{WHISPER_PORT}/"

def heartbeat(status, summary):
    try:
        subprocess.run([sys.executable,
                        os.path.join(REPO_ROOT, ".agent", "scripts", "heartbeat.py"),
                        "--job", "vexa-auto", "--status", status, "--summary", summary],
                       capture_output=True, timeout=30)
    except Exception:
        pass

def ensure_env_url():
    """Keep TRANSCRIPTION_SERVICE_URL in ~/tools/vexa/.env pointed at the live
    gateway IP; on drift, patch the .env and restart the container (else bots
    silently produce empty transcripts).

    No-op under meetbot: it reads the gateway IP itself on every whisper call,
    so there is nothing to patch, and the restart would bounce the one container
    that has to stay untouched as the rollback path."""
    if meetbot_mode():
        return
    want = f"http://{gateway_ip()}:{WHISPER_PORT}/v1/audio/transcriptions"
    try:
        lines = open(ENV_PATH, encoding="utf-8").read().splitlines()
    except OSError as e:
        print(f"[vexa] cannot read {ENV_PATH}: {e}", file=sys.stderr)
        return
    for i, line in enumerate(lines):
        if line.startswith("TRANSCRIPTION_SERVICE_URL="):
            if line.split("=", 1)[1] == want:
                return
            lines[i] = f"TRANSCRIPTION_SERVICE_URL={want}"
            break
    else:
        return  # no such key; leave the .env alone
    open(ENV_PATH, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"[vexa] gateway IP drifted; .env updated -> {want}; restarting container",
          file=sys.stderr)
    subprocess.run(["sg", "docker", "-c", "docker restart vexa-lite"],
                   capture_output=True, timeout=180)

def ensure_vexa_api():
    """API reachable? Under vexa, try one docker start if not."""
    def ok():
        try:
            urllib.request.urlopen(API_BASE + "/", timeout=5)
            return True
        except urllib.error.HTTPError:
            return True  # API answered (even 404) = container alive
        except Exception:
            return False
    if ok():
        return True
    if meetbot_mode():
        # meetbot is a systemd user unit, not a container. Never `docker start`
        # here: it would resurrect vexa-lite behind meetbot's back, and vexa is
        # the rollback path -- it must only ever move on a deliberate operator
        # action. Report down and let the caller heartbeat + exit.
        print(f"[meetbot] API down at {API_BASE}; not a container, no auto-start. "
              "Recover with: systemctl --user restart meetbot.service "
              "(or roll back: unset VEXA_API_BASE MEETBOT)", file=sys.stderr)
        return False
    print("[vexa] API down; attempting docker start...", file=sys.stderr)
    subprocess.run(["sg", "docker", "-c", "docker start vexa-postgres vexa-lite"],
                   capture_output=True, timeout=120)
    import time
    for _ in range(6):
        time.sleep(5)
        if ok():
            return True
    return False

# Windows-side keeper (crash-recovery, interop-independent). Preferred restart path.
KEEPER_WIN = r"C:\tools\whisper-keeper.ps1"
_INTEROP_HINT = ("PowerShell interop unavailable from WSL -- cannot restart whisper "
                 "remotely. On Windows run (once, elevated): "
                 r"powershell -ExecutionPolicy Bypass -File C:\tools\install-whisper-service.ps1")

def interop_ok():
    """Can we launch a Windows .exe from this WSL session at all? When interop is
    broken (binfmt not registered), powershell.exe fails with OSError/Exec format
    error, so the remote whisper restart can never work -- we must tell the owner."""
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", "$true"],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False

def ensure_whisper():
    """The bot produces ZERO transcript if whisper-server is down (silent loss).
    Check before sending a bot; try a restart via the Windows keeper. Returns
    (ok: bool, reason: str) -- callers turn `reason` into an actionable heartbeat."""
    def ok():
        try:
            urllib.request.urlopen(whisper_url(), timeout=5)
            return True
        except Exception:
            return False
    if ok():
        return True, "up"
    if not interop_ok():
        print(f"[vexa] whisper-server down + {_INTEROP_HINT}", file=sys.stderr)
        return False, "whisper-server down; " + _INTEROP_HINT
    print("[vexa] whisper-server down; attempting restart via keeper...", file=sys.stderr)
    try:
        # Prefer the idempotent keeper script; fall back to an inline start if the
        # keeper isn't deployed yet.
        if os.path.exists("/mnt/c/tools/whisper-keeper.ps1"):
            subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", KEEPER_WIN], capture_output=True, timeout=90)
        else:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 "Stop-Process -Name whisper-server -Force -ErrorAction SilentlyContinue; "
                 "Start-Sleep 2; Start-Process -WindowStyle Hidden "
                 r"-FilePath C:\tools\whisper.cpp-src\build\bin\whisper-server.exe "
                 "-ArgumentList '-m','C:\\tools\\whisper.cpp-src\\models\\ggml-large-v3-turbo.bin',"
                 "'--host','0.0.0.0','--port','8083','--inference-path','/v1/audio/transcriptions'"],
                capture_output=True, timeout=60)
        import time
        for _ in range(6):
            time.sleep(5)
            if ok():
                print("[vexa] whisper-server restarted OK", file=sys.stderr)
                return True, "restarted"
    except Exception as e:
        print(f"[vexa] restart failed: {e}", file=sys.stderr)
        return False, f"whisper-server down; auto-restart errored ({e}); {_INTEROP_HINT}"
    return False, ("whisper-server down; auto-restart ran but port 8083 still not "
                   "listening (check Windows Firewall inbound 8083 + C:\\tools\\whisper-logs\\)")

def req(method, path, body=None, headers=None, timeout=60):
    r = urllib.request.Request(
        API_BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:300]}")

def admin_token():
    for line in open(ENV_PATH, encoding="utf-8"):
        if line.startswith("ADMIN_TOKEN="):
            return line.strip().split("=", 1)[1]
    sys.exit("ERROR: ADMIN_TOKEN not found in vexa .env")

def api_key():
    if not os.path.exists(TOKEN_PATH):
        sys.exit("ERROR: no API key yet; run: python3 meeting-recorder/vexa_bots.py setup")
    for line in open(TOKEN_PATH, encoding="utf-8"):
        if line.startswith("VEXA_API_KEY="):
            return line.strip().split("=", 1)[1]
    sys.exit("ERROR: VEXA_API_KEY missing in vexa_token.env")

def load_state():
    if os.path.exists(STATE_PATH):
        return json.load(open(STATE_PATH, encoding="utf-8"))
    return {"meetings": {}}

def save_state(st):
    tmp = STATE_PATH + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), indent=2)
    os.replace(tmp, STATE_PATH)

def parse_meet(s):
    """Accept a Meet URL/code or Teams URL; return (platform, native_id, passcode)."""
    m = MEET_RE.search(s)
    if m:
        return "google_meet", m.group(1), None
    if re.fullmatch(r"[a-z]{3}-[a-z]{4}-[a-z]{3}", s):
        return "google_meet", s, None
    if "teams.microsoft.com" in s or s.isdigit():
        # {10,20} to match TEAMS_RE; the old {10,14} silently truncated 15-digit
        # Teams meeting IDs, producing a 404 on the transcript pull.
        mid = re.search(r"(\d{10,20})", s)
        return "teams", (mid.group(1) if mid else s), None
    sys.exit(f"ERROR: cannot parse meeting id from: {s}")

# ---------- commands ----------

def cmd_setup(_):
    tok = admin_token()
    user = req("POST", "/admin/users",
               {"email": "teammate@yourcompany.com", "name": "the owner (local)"},
               {"X-Admin-API-Key": tok})
    uid = user.get("id", 1)
    t = req("POST", f"/admin/users/{uid}/tokens?scopes=bot,tx,browser", {},
            {"X-Admin-API-Key": tok})
    key = t.get("token") or t.get("api_key") or t.get("key")
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(f"VEXA_API_KEY={key}\nVEXA_USER_ID={uid}\n")
    print(f"OK: user {uid} + API key saved -> {TOKEN_PATH}")

def cmd_send(a):
    ok, reason = ensure_whisper()
    if not ok:
        sys.exit(f"ERROR: {reason}\nBot would produce an empty transcript; "
                 "fix transcription first before sending.")
    platform, mid, passcode = parse_meet(a.meet)
    body = {"platform": platform, "native_meeting_id": mid,
            "bot_name": BOT_NAME, "language": a.lang,
            "recording_enabled": False, "transcribe_enabled": True}
    if a.passcode or passcode:
        body["passcode"] = a.passcode or passcode
    if body["language"] is None:
        body.pop("language")
    res = req("POST", "/bots", body, {"X-API-Key": api_key()}, timeout=120)
    st = load_state()
    st["meetings"][f"{platform}/{mid}"] = {
        "title": a.title or mid, "sent_at": datetime.datetime.now(WIB).isoformat(timespec="seconds"),
        "status": "bot_sent"}
    save_state(st)
    print(f"OK: bot sent to {platform}/{mid} ({a.title or 'untitled'})")
    print(json.dumps(res, indent=2)[:400])

def cmd_stop(a):
    platform, mid, _ = parse_meet(a.meet)
    try:
        req("DELETE", f"/bots/{platform}/{mid}", None, {"X-API-Key": api_key()})
        print(f"OK: bot leaving {platform}/{mid}")
    except RuntimeError as e:
        print(f"stop failed: {e}")

def cmd_status(_):
    st = load_state()
    for k, v in st["meetings"].items():
        print(f"{k}: {v.get('status')} ({v.get('title')})")
    if not st["meetings"]:
        print("(no meetings tracked)")

def transcript_to_md(data, out_md, truncated=False):
    lines = []
    for seg in data.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        spk = seg.get("speaker") or "Unknown"
        lines.append(f"**[{fmt_ts(seg.get('start_time') or 0)}]** {spk}: {text}")
    warn = ""
    if truncated:
        warn = (f"\n> INCOMPLETE: the bot left at {data.get('end_time')} with status "
                f"'{data.get('status')}' while the call was still running. Everything after "
                f"that point is MISSING. Recover the full call from Fathom before drafting "
                f"anything off this file.\n")
    header = (f"# Transcript: {data.get('constructed_meeting_url') or data.get('native_meeting_id')}\n"
              f"{warn}\n"
              f"- Engine: Vexa bot + whisper-server (Vulkan GPU), speaker labels from meeting UI\n"
              f"- Status: {data.get('status')}\n"
              f"- Start: {data.get('start_time')}\n\n---\n\n")
    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
    with open(os.path.splitext(out_md)[0] + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)

def cmd_pull(a):
    platform, mid, _ = parse_meet(a.meet)
    data = req("GET", f"/transcripts/{platform}/{mid}", None, {"X-API-Key": api_key()})
    final = getattr(a, "final", False) or data.get("status") in ("completed", "failed", "stopped")
    st = load_state()
    kid = f"{platform}/{mid}"
    meta = st["meetings"].get(kid, {})
    title = a.title or meta.get("title") or mid

    n_segs = sum(1 for seg in data.get("segments", [])
                 if (seg.get("text") or "").strip())
    if n_segs == 0:
        # never write an empty transcript file or mark the meeting done
        if final:
            # Check STATUS BEFORE segment count. This ordering is load-bearing:
            # it used to be the other way round, so ANY zero-segment terminal row
            # was laundered into an operational skip with an "ok" heartbeat --
            # including a genuine mid-call recorder failure. A meeting lost to a
            # dead transcription backend disappeared behind a green light, which
            # is the exact failure mode the ledgers exist to prevent.
            if data.get("status") in ("failed", "stopped"):
                st["meetings"][kid] = {**meta, "title": title,
                                       "status": "failed_empty"}
                save_state(st)
                heartbeat("fail", f"vexa {mid} ({title}): bot ended '{data.get('status')}' "
                                  "with NO transcript -- recorder failure, not a skip. "
                                  "Meeting is unrecorded; check whisper + bot logs")
                print(f"FAILED: {kid} ended {data.get('status')} with an empty "
                      f"transcript -- treat as unrecorded, NOT as not-admitted",
                      file=sys.stderr)
                return
            # Empty after a clean finish splits into two OPPOSITE cases, and
            # `start_time` is what tells them apart. The bot writes it exactly
            # once, in session.rs, the instant admission is granted -- so its
            # presence means the bot was in the room.
            #
            #   absent  -> never admitted. Nothing was ever recordable. A real
            #              operational skip, ok heartbeat, no MOM.
            #   present -> admitted, sat through the call, produced nothing.
            #              That is a LOST MEETING, not a skip.
            #
            # Filing the second as `skipped_not_admitted` with an ok heartbeat
            # is what hid the 5 Aug 2026 outage for two days: 14 sessions, 0
            # transcripts, every heartbeat green, because a bot that sat deaf in
            # a live meeting was indistinguishable from one left in a waiting
            # room.
            if data.get("start_time"):
                st["meetings"][kid] = {**meta, "title": title,
                                       "status": "admitted_no_audio"}
                save_state(st)
                heartbeat("fail", f"vexa {mid} ({title}): bot was ADMITTED and recorded "
                                  "NOTHING -- the meeting is lost, not skipped. Check the "
                                  "'tap stats' line in `journalctl --user -u meetbot.service`: "
                                  "any tab_state other than 'ok' means Chrome never handed "
                                  "over the tab's audio")
                print(f"LOST: {kid} was admitted at {data.get('start_time')} but the "
                      f"transcript is empty -- deaf capture, NOT a skip", file=sys.stderr)
                return
            st["meetings"][kid] = {**meta, "title": title,
                                   "status": "skipped_not_admitted"}
            save_state(st)
            heartbeat("ok", f"vexa {mid} ({title}): never admitted, empty transcript "
                            "-- skipped, no MOM")
            print(f"SKIPPED: {kid} finished but was never admitted "
                  f"(no start_time, empty transcript)", file=sys.stderr)
        else:
            print("no segments yet; meeting may not have started")
        return

    # A bot that ended in "failed"/"stopped" left the call early (kicked, crashed,
    # or its removal-detector misfired). Segments still come back, so the pull
    # looks identical to a clean "completed" run -- the transcript is just silently
    # truncated at the point the bot died. Never launder that into a normal draft.
    bot_failed = data.get("status") in ("failed", "stopped")
    slug = slugify(title)
    stamp = datetime.datetime.now(WIB).strftime("%Y-%m-%d")
    out_md = os.path.join(W.TRANSCRIPTS_DIR, f"{stamp}_{slug}_vexa.md")
    n = transcript_to_md(data, out_md, truncated=bot_failed)
    print(f"transcript: {n} segments -> {out_md}")
    if bot_failed:
        print(f"WARNING: bot status={data.get('status')} -- transcript is TRUNCATED "
              f"at {n} segments; recover the full call from Fathom", file=sys.stderr)

    # registry entry (reuse watcher shape)
    start_iso = data.get("start_time")
    start_dt = (datetime.datetime.fromisoformat(start_iso).replace(tzinfo=datetime.timezone.utc)
                if start_iso else datetime.datetime.now(datetime.timezone.utc))
    sidecar = {"title": title, "start_utc": start_dt.isoformat(timespec="seconds")}
    rec_id, start_wib = W.register_recording(
        data.get("constructed_meeting_url") or mid, sidecar, title, 0)

    # dedupe: one meeting -> one MOM (Fathom/local/Vexa share the registry)
    related = W.find_related(title, start_wib.strftime("%Y-%m-%d"), exclude_id=rec_id)
    W.link_related(rec_id, related)
    dup_rid, dup_mom = W.existing_mom(related)
    if dup_mom:
        status = f"transcribed (duplicate of {dup_rid}, MOM skipped)"
        print(f"duplicate of {dup_rid}; MOM draft skipped -> {dup_mom}")
    else:
        cfg = load_config()
        scratch = os.path.join(MODULE_DIR, "scratch")
        os.makedirs(scratch, exist_ok=True)
        mom = W.draft_mom(out_md, title, start_wib, title, scratch, cfg)
        if mom:
            mom_path = os.path.join(W.MOM_DIR, f"MOM_{slug}_{start_wib.strftime('%Y-%m-%d')}.md")
            header = (f"> Status: DRAFT (Vexa bot, belum direview)\n"
                      f"> Source: bot transcript {platform}/{mid}, speaker labels from meeting UI\n"
                      f"> Registry: {rec_id} | Review via /mom before sharing\n")
            if bot_failed:
                header += (f"> ⚠ PARTIAL: bot died mid-call (status '{data.get('status')}', "
                           f"ended {data.get('end_time')}). This MOM only covers the call up to "
                           f"that point. Pull the full recording from Fathom before trusting it.\n")
            open(mom_path, "w", encoding="utf-8").write(header + "\n" + mom + "\n")
            W.update_registry_entry(rec_id, mom_path=os.path.relpath(mom_path, REPO_ROOT))
            print(f"MOM draft -> {mom_path}")
            status = "drafted_partial" if bot_failed else "drafted"
        else:
            status = "transcribed (needs /mom manual)"
    if bot_failed:
        status = f"{status} (bot {data.get('status')} mid-call, transcript truncated)"
    st["meetings"][kid] = {**meta, "title": title, "status": status,
                           "transcript": out_md, "rec_id": rec_id}
    save_state(st)
    if bot_failed:
        heartbeat("fail", f"vexa {mid} ({title}): bot {data.get('status')} mid-call; "
                          f"only {n} segments captured -- recover from Fathom")
    else:
        heartbeat("ok", f"vexa {mid}: {status}")

TEAMS_RE = re.compile(r"teams\.microsoft\.com/meet/(\d{10,20})")
PASSCODE_RE = re.compile(r"Passcode:\s*([A-Za-z0-9]+)")

def extract_meeting_link(ev):
    """(platform, native_id, passcode) from a calendar event; (None,None,None) if none.
    Prefers the hangoutLink field; falls back to Meet/Teams links in description."""
    text = (ev.get("hangoutLink") or "") + "\n" + (ev.get("description") or "")
    m = MEET_RE.search(text)
    if m:
        return "google_meet", m.group(1), None
    t = TEAMS_RE.search(text)
    if t:
        p = PASSCODE_RE.search(text)
        return "teams", t.group(1), (p.group(1) if p else None)
    return None, None, None

def cmd_auto(a):
    """Cron mode: join meetings starting soon; pull finished ones."""
    dry = getattr(a, "dry_run", False)
    st = load_state()
    key = api_key()
    if not dry:
        ensure_env_url()  # no-op under meetbot
        if not ensure_vexa_api():
            if meetbot_mode():
                heartbeat("fail", f"meetbot API unreachable at {API_BASE}; "
                                  "restart meetbot.service or roll back to vexa")
                sys.exit("ERROR: meetbot API down")
            heartbeat("fail", "vexa API unreachable (docker start failed)")
            sys.exit("ERROR: Vexa API down")
    if dry:
        whisper_ok = True
    else:
        whisper_ok, whisper_reason = ensure_whisper()  # still pull finished meetings even if down
        if not whisper_ok:
            heartbeat("fail", whisper_reason + " -- joins skipped this cycle")
    now = datetime.datetime.now().astimezone()

    # 1) join upcoming calendar meetings with a Meet link
    calendar_ok = True
    sent = 0
    try:
        r = subprocess.run([sys.executable, GCAL, "list", "--profile", "work",
                            "--days-back", "0", "--days-forward", "1", "--json"],
                           capture_output=True, text=True, timeout=120)
        events = parse_json_tail(r.stdout)
    except Exception as e:
        events = []
        calendar_ok = False
        # An empty event list makes the join loop below a no-op, so without a
        # fail heartbeat this cycle silently declines to join every meeting and
        # still reports healthy. On 16 Jul this dropped YourManager's mandatory weekly.
        print(f"calendar unavailable: {e}", file=sys.stderr)
        heartbeat("fail", f"calendar unavailable, joins skipped this cycle: {e}")
    for ev in events:
        stt = ev.get("start", "")
        if "T" not in stt:
            continue
        platform, mid, passcode = extract_meeting_link(ev)
        if not mid:
            continue
        start = datetime.datetime.fromisoformat(stt)
        mins = (start - now).total_seconds() / 60
        kid = f"{platform}/{mid}"
        # A recurring meeting reuses the SAME Meet code for every occurrence, so
        # a state key of platform/code alone made the first occurrence suppress
        # every later one -- permanently, and silently. On 20 Jul that had four
        # of the day's meetings skipped against records from 8-17 Jul. Only the
        # occurrence already handled TODAY may suppress a join.
        prev = st["meetings"].get(kid)
        done_this_occurrence = bool(
            prev and (prev.get("sent_at") or "")[:10] == start.date().isoformat()
        )
        # Join EARLY, up to 8 minutes before start. This is deliberate: the bot
        # must be the account's first presence in the call. If the owner is already
        # in (same account), Google buries the working "Join here too" and offers
        # only the session-stealing "Switch here", so the bot cannot join. Getting
        # there first secondarys it -- the bot joins normally into an empty room, and
        # when the owner arrives HE gets "Join here too", which works for a real human
        # session. meetbot's empty_room_grace keeps it from leaving the quiet room
        # before the meeting starts. (Window: from 8 min before to 10 min after.)
        JOIN_EARLY_MIN, JOIN_LATE_MIN = 8, -10
        in_window = JOIN_LATE_MIN <= mins <= JOIN_EARLY_MIN
        if dry:
            when = "JOIN NOW" if in_window else f"in {mins:+.0f} min"
            if done_this_occurrence:
                tracked = " (already handled today)"
            elif prev:
                tracked = f" (earlier occurrence {(prev.get('sent_at') or '?')[:10]}, will re-join)"
            else:
                tracked = ""
            print(f"[dry-run] {ev.get('summary')} -> {kid} [{when}]{tracked}")
            continue
        if not in_window or done_this_occurrence:
            continue
        if not whisper_ok:
            print("skip join (whisper-server down)", file=sys.stderr)
            break
        # Force ISO code "en": the OpenAI-compat whisper-server returns the
        # language as a full name ("english"), which Vexa's TranscriptionSegment
        # validator rejects -> 0 segments saved -> empty transcript. cmd_auto only
        # joins Work-calendar meetings (always English), so "en" is safe here.
        # For non-English meetings use manual `send --lang <iso>`.
        body = {"platform": platform, "native_meeting_id": mid,
                "bot_name": BOT_NAME, "transcribe_enabled": True,
                "language": "en"}
        if passcode:
            body["passcode"] = passcode
        try:
            req("POST", "/bots", body, {"X-API-Key": key}, timeout=120)
            st["meetings"][kid] = {"title": ev.get("summary") or mid,
                                   "status": "bot_sent",
                                   "sent_at": now.isoformat(timespec="seconds")}
            sent += 1
            print(f"bot sent: {kid} ({ev.get('summary')})")
        except RuntimeError as e:
            # Record the rejection instead of losing it to stderr. The common
            # case is HTTP 403 "maximum concurrent bot limit (3)" during the
            # 18:00-21:00 WIB block, which silently drops a real meeting.
            print(f"send failed {kid}: {e}", file=sys.stderr)
            st["meetings"][kid] = {"title": ev.get("summary") or mid,
                                   "status": "send_failed",
                                   "error": str(e)[:300],
                                   "sent_at": now.isoformat(timespec="seconds")}
            heartbeat("fail", f"vexa send rejected for {ev.get('summary') or kid}: {e}")
    save_state(st)

    # Idle heartbeat ~once/hour (cron is */5, so minute<5 hits one slot per hour):
    # without this, quiet stretches with no meetings look like "silent cron" to
    # harness-health even though every 5-min tick ran fine.
    # Gated on calendar_ok and emitted only AFTER the join loop: a cycle that
    # never saw the calendar cannot honestly claim "no meeting events".
    if not dry and whisper_ok and calendar_ok and now.minute < 5:
        heartbeat("ok", "idle tick: stack healthy, no meeting events this cycle")

    # 2) pull + process meetings whose bot finished
    pulled = 0
    for kid, meta in list(st["meetings"].items()):
        if meta.get("status") != "bot_sent":
            continue
        platform, mid = kid.split("/", 1)
        try:
            data = req("GET", f"/transcripts/{platform}/{mid}", None, {"X-API-Key": key})
        except RuntimeError as e:
            # zombie guard: bot_sent but Vexa doesn't know the meeting (404) and
            # it was sent hours ago -> stop retrying forever
            sent = meta.get("sent_at")
            if "404" in str(e) and sent and not dry:
                age_h = (now - datetime.datetime.fromisoformat(sent)).total_seconds() / 3600
                if age_h > 3:
                    st["meetings"][kid]["status"] = "failed_not_found"
                    save_state(st)
                    heartbeat("fail", f"vexa {mid}: bot_sent but 404 after {age_h:.0f}h; marked failed")
            continue
        if data.get("status") in ("completed", "failed", "stopped"):
            if dry:
                print(f"[dry-run] would pull finished meeting: {kid}")
                continue
            ns = argparse.Namespace(meet=mid, title=meta.get("title"), final=True)
            try:
                cmd_pull(ns)
                pulled += 1
            except Exception as e:
                print(f"pull failed {kid}: {e}", file=sys.stderr)
                st["meetings"][kid]["status"] = f"pull_failed: {e}"
                save_state(st)

    # Cycle trace. Without this the cron log stays EMPTY on quiet cycles, which
    # makes "ran fine, nothing to do" indistinguishable from "cron never fired"
    # -- a silent-failure surface the dashboard now reads as cron freshness.
    # One terse line per 5-min tick (~70 B, ~20 kB/day); keep it that way.
    if not dry:
        print(f"{now.isoformat(timespec='seconds')} "
              f"backend={'meetbot' if meetbot_mode() else 'vexa'} "
              f"events={len(events)} sent={sent} pulled={pulled} "
              f"whisper={'ok' if whisper_ok else 'down'} "
              f"calendar={'ok' if calendar_ok else 'FAIL'}", flush=True)

def cmd_selftest(_):
    """Regression test for meetbot-mode gating. Pure in-process, sends no bots,
    touches no network, writes nothing outside a temp dir. Run it after editing
    anything in this file:  python3 vexa_bots.py selftest"""
    import contextlib
    import tempfile

    global API_BASE, ENV_PATH
    saved = (API_BASE, ENV_PATH, os.environ.get("MEETBOT"))
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    @contextlib.contextmanager
    def env(base, flag):
        global API_BASE
        API_BASE = base
        os.environ.pop("MEETBOT", None)
        if flag is not None:
            os.environ["MEETBOT"] = flag
        try:
            yield
        finally:
            API_BASE = saved[0]
            os.environ.pop("MEETBOT", None)

    # --- detection matrix. The first two rows are the backward-compat contract:
    # unset env and the legacy :8056 base MUST stay vexa.
    matrix = [
        ("http://localhost:8056", None, False),   # today's default
        ("http://localhost:8056", "", False),     # empty MEETBOT is not a signal
        ("http://127.0.0.1:8056", None, False),
        ("http://localhost", None, False),        # no port -> legacy
        ("http://localhost:8060", None, True),    # port check
        ("http://127.0.0.1:8060", None, True),
        ("http://localhost:8056", "1", True),     # explicit override on
        ("http://localhost:8056", "true", True),
        ("http://localhost:8060", "0", False),    # explicit override off
        ("http://localhost:8060", "no", False),
        ("http://localhost:99999", None, False),  # malformed port -> legacy
    ]
    for base, flag, want in matrix:
        with env(base, flag):
            check(f"meetbot_mode(base={base}, MEETBOT={flag!r})", meetbot_mode(), want)

    # --- ensure_env_url() must not touch vexa's .env under meetbot, and
    # --- ensure_vexa_api() must not shell out to docker under meetbot.
    calls = []
    real_run = subprocess.run
    with tempfile.TemporaryDirectory() as td:
        fake_env = os.path.join(td, ".env")
        original = "ADMIN_TOKEN=abc\nTRANSCRIPTION_SERVICE_URL=http://1.2.3.4:8083/v1/audio/transcriptions\n"
        open(fake_env, "w", encoding="utf-8").write(original)
        subprocess.run = lambda *a, **k: calls.append(a[0]) or real_run(["true"], capture_output=True)
        try:
            ENV_PATH = fake_env
            with env("http://localhost:8060", None):
                ensure_env_url()
                check("meetbot: vexa .env untouched",
                      open(fake_env, encoding="utf-8").read(), original)
                check("meetbot: ensure_env_url ran no subprocess", calls, [])
                # port 8060 is not listening during selftest -> the down path
                check("meetbot: ensure_vexa_api reports down", ensure_vexa_api(), False)
                check("meetbot: ensure_vexa_api ran no docker", calls, [])
            # legacy path still rewrites + restarts on drift (unchanged behaviour)
            with env("http://localhost:8056", None):
                ensure_env_url()
                rewritten = open(fake_env, encoding="utf-8").read()
                check("vexa: .env rewritten on drift", rewritten != original, True)
                check("vexa: container restarted on drift",
                      any("docker restart vexa-lite" in " ".join(c) for c in calls), True)
        finally:
            subprocess.run = real_run
            API_BASE, ENV_PATH = saved[0], saved[1]
            if saved[2] is not None:
                os.environ["MEETBOT"] = saved[2]

    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    if failures:
        sys.exit(f"selftest: {len(failures)} failure(s)")
    print(f"selftest OK ({len(matrix)} detection cases + gating checks)")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    p = sub.add_parser("send")
    p.add_argument("--meet", required=True)
    p.add_argument("--title")
    p.add_argument("--lang")
    p.add_argument("--passcode")
    p = sub.add_parser("pull")
    p.add_argument("--meet", required=True)
    p.add_argument("--title")
    p = sub.add_parser("stop")
    p.add_argument("--meet", required=True)
    sub.add_parser("status")
    p = sub.add_parser("auto")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="print join/pull decisions without sending bots")
    sub.add_parser("selftest", help="regression-test meetbot-mode gating; sends nothing")
    a = ap.parse_args()
    {"setup": cmd_setup, "send": cmd_send, "pull": cmd_pull,
     "stop": cmd_stop, "status": cmd_status, "auto": cmd_auto,
     "selftest": cmd_selftest}[a.cmd](a)

if __name__ == "__main__":
    main()
