#!/usr/bin/env python3
"""Ingest server: receives meeting audio from the phone and hands it to the watcher.

The phone runs a PWA (webapp/) that records with MediaRecorder and POSTs a chunk
every ~15 seconds. This server appends those chunks, and on /finish transcodes the
result to .m4a, writes the sidecar .json that recorder.py would have written, and
fires `watcher.py --file` directly. Everything downstream -- transcription,
calendar match, registry, MOM draft -- is unchanged and unaware a phone was involved.

Why chunks and not one upload at the end: Android will eventually kill a
backgrounded browser tab. Streaming means a tab that dies at minute 47 of a
60-minute meeting has already landed 47 minutes here, and the stale sweep
finishes that session into a real transcript instead of losing the meeting.

Why the watcher is fired directly instead of waiting for cron: macOS has no
crontab at all (WSL is the sole automation host, per CLAUDE.md), so a recording
that landed on the Mac would otherwise sit untouched forever.

Assembly happens in ingest_sessions/, NOT in recordings_dir, so a half-uploaded
meeting is never visible to a concurrent `watcher.py --once` on the WSL host.
The finished file enters recordings_dir as `<name>.m4a.tmp` (an extension the
watcher does not scan) and is renamed within that same directory -- rename, not
os.replace across directories, because on WSL recordings_dir sits on /mnt/f and a
cross-device move would fail.

Usage:
  python3 ingest_server.py                 # serve (port 8787, 127.0.0.1)
  python3 ingest_server.py --port 9000
  python3 ingest_server.py --bind 0.0.0.0  # LAN-visible; token is then the only guard
  python3 ingest_server.py --print-token   # show the pairing token, creating one if absent
"""
import argparse
import datetime
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

from common import REPO_ROOT, load_config, slugify

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(MODULE_DIR, "ingest_sessions")
WEBAPP_DIR = os.path.join(MODULE_DIR, "webapp")
TOKEN_PATH = os.path.join(MODULE_DIR, "ingest_token.env")
WATCHER = os.path.join(MODULE_DIR, "watcher.py")

DEFAULT_PORT = 8787
DEFAULT_BIND = "127.0.0.1"
# A meeting that has gone quiet this long is treated as a killed tab and finished
# with whatever audio arrived. Long enough that a genuine pause (phone locked, a
# tunnel through a lift) does not trip it.
STALE_SEC = 30 * 60
SWEEP_INTERVAL_SEC = 120
MAX_CHUNK_BYTES = 32 * 1024 * 1024

WIB = datetime.timezone(datetime.timedelta(hours=7))
SAFE_ASSET = re.compile(r"^[A-Za-z0-9._-]+$")

_lock = threading.Lock()          # guards the SESSIONS map itself
_sessions = {}                    # sid -> dict(meta + "lock")

# ---------- token ----------

def load_token(create=False):
    """Shared pairing secret. Same shape as the connector skills' token.env."""
    env = os.environ.get("ASB_INGEST_TOKEN", "").strip()
    if env:
        return env
    if os.path.exists(TOKEN_PATH):
        for line in open(TOKEN_PATH, encoding="utf-8"):
            line = line.strip()
            if line.startswith("ASB_INGEST_TOKEN="):
                tok = line.split("=", 1)[1].strip()
                if tok:
                    return tok
    if not create:
        return ""
    tok = secrets.token_urlsafe(24)
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write("# Pairing secret for meeting-recorder/ingest_server.py.\n"
                "# Paste this into the phone web app once. Gitignored on purpose.\n"
                f"ASB_INGEST_TOKEN={tok}\n")
    os.chmod(TOKEN_PATH, 0o600)
    return tok

# ---------- session state ----------

def session_path(sid):
    return os.path.join(SESSIONS_DIR, sid + ".json")

def part_path(sid):
    return os.path.join(SESSIONS_DIR, sid + ".part")

def save_session(sess):
    """Persist everything except the lock, so a restart can still sweep."""
    data = {k: v for k, v in sess.items() if k != "lock"}
    tmp = session_path(sess["sid"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, session_path(sess["sid"]))

def load_sessions_from_disk():
    """Recover in-flight sessions after a server restart."""
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    for name in os.listdir(SESSIONS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS_DIR, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("sid")
        if not sid or data.get("done"):
            continue
        data["lock"] = threading.Lock()
        _sessions[sid] = data
    if _sessions:
        print(f"[ingest] recovered {len(_sessions)} in-flight session(s) from disk")

def drop_session(sid):
    with _lock:
        _sessions.pop(sid, None)
    for p in (session_path(sid), part_path(sid), session_path(sid) + ".tmp"):
        try:
            os.remove(p)
        except OSError:
            pass

# ---------- finishing ----------

def unique_base(rec_dir, stamp, slug):
    """Two meetings started in the same minute must not collide: the stamp is
    minute-resolution (matching recorder.py's now_stamp) so a second recording
    would otherwise overwrite the first."""
    base = os.path.join(rec_dir, f"{stamp}_{slug}")
    if not os.path.exists(base + ".m4a") and not os.path.exists(base + ".json"):
        return base
    for n in range(2, 100):
        cand = f"{base}_{n}"
        if not os.path.exists(cand + ".m4a") and not os.path.exists(cand + ".json"):
            return cand
    return f"{base}_{int(time.time())}"

def transcode(ffmpeg, src, dst):
    """WebM/Opus (what Chrome Android produces) -> m4a AAC mono, the format the
    rest of the pipeline already handles.

    A stale-swept recording is a truncated stream with no closing cluster, so
    ffmpeg is told to keep whatever it can decode rather than abort: that
    truncated tail is exactly the case this whole design exists to save."""
    # -f ipod (ffmpeg's m4a muxer) is required, not cosmetic: the staging name
    # ends in .m4a.tmp so the watcher never scans a half-written file, and
    # ffmpeg cannot infer a container from the .tmp extension.
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-err_detect", "ignore_err", "-fflags", "+genpts+discardcorrupt",
           "-i", src, "-vn", "-c:a", "aac", "-b:a", "64k", "-ac", "1",
           "-f", "ipod", "-y", dst]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): "
                           f"{(proc.stderr or '').strip()[:400]}")

DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")

def probe_duration(ffmpeg, path):
    """Real audio length, which is not the same as wall time. A session swept 30
    minutes after the tab died would otherwise report a 30-minute-too-long
    meeting, and that number reaches the registry and the MOM.

    ffprobe is preferred but often absent -- the owner's ffmpeg is a standalone
    static binary with no sibling ffprobe -- so the fallback asks ffmpeg itself.
    `ffmpeg -i <file>` with no output prints the container header and exits
    non-zero without decoding anything, so this stays instant on a 60-minute file."""
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe") if os.path.dirname(ffmpeg) else ""
    if not (ffprobe and os.path.isfile(ffprobe)):
        ffprobe = shutil.which("ffprobe") or ""
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", path],
                capture_output=True, text=True, timeout=30)
            return int(float(out.stdout.strip()))
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-i", path],
                             capture_output=True, text=True, timeout=30)
        m = DURATION_RE.search(out.stderr or "")
        if m:
            h, mnt, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return int(h * 3600 + mnt * 60 + sec)
    except (subprocess.SubprocessError, ValueError, OSError):
        pass
    return 0

def fire_watcher(audio_path):
    """Detached, so a 5-minute transcription never holds the phone's HTTP
    request open. Output goes to the same cron log the WSL watcher writes."""
    if os.environ.get("ASB_INGEST_NO_WATCHER"):
        # Test escape hatch: exercise the upload path without spending a Gemini
        # transcription and without writing a MOM draft into Clients/.
        print(f"[ingest] ASB_INGEST_NO_WATCHER set, not firing watcher for {audio_path}")
        return
    log_path = os.path.join(MODULE_DIR, "ingest_watcher.log")
    log = open(log_path, "a", encoding="utf-8")
    log.write(f"\n=== {datetime.datetime.now(WIB).isoformat(timespec='seconds')} "
              f"{os.path.basename(audio_path)} ===\n")
    log.flush()
    subprocess.Popen([sys.executable, WATCHER, "--file", audio_path],
                     cwd=MODULE_DIR, stdout=log, stderr=subprocess.STDOUT,
                     start_new_session=True)

def finish_session(sess, cfg, reason="finish"):
    """Assemble -> transcode -> land in recordings_dir -> sidecar -> fire watcher.

    Order matters at the end: the .m4a is renamed into place BEFORE the sidecar
    is written, because the sidecar's existence is what tells find_candidates the
    file is final and skips its 60-second stability window."""
    sid = sess["sid"]
    src = part_path(sid)
    if not os.path.exists(src) or os.path.getsize(src) == 0:
        drop_session(sid)
        raise RuntimeError("no audio received")

    rec_dir = cfg["machine"].get("recordings_dir", "")
    ffmpeg = cfg["machine"].get("ffmpeg", "ffmpeg")
    os.makedirs(rec_dir, exist_ok=True)

    stamp = datetime.datetime.fromtimestamp(sess["start_epoch"]).strftime("%Y-%m-%d_%H%M")
    base = unique_base(rec_dir, stamp, slugify(sess["title"]))
    final = base + ".m4a"
    staged = final + ".tmp"          # .tmp is not in AUDIO_EXTS, so never scanned

    transcode(ffmpeg, src, staged)
    os.rename(staged, final)         # same directory: safe on /mnt/f too

    duration = probe_duration(ffmpeg, final)
    if not duration:
        duration = max(0, int(sess.get("last_activity", time.time()) - sess["start_epoch"]))

    start = datetime.datetime.fromtimestamp(sess["start_epoch"], datetime.timezone.utc)
    meta = {
        "title": sess["title"],
        "start_utc": start.isoformat(timespec="seconds"),
        "end_utc": (start + datetime.timedelta(seconds=duration)).isoformat(timespec="seconds"),
        "duration_sec": duration,
        "platform": cfg.get("platform", ""),
        "ad_hoc": bool(sess.get("ad_hoc")),
        "attendees": sess.get("attendees") or [],
        "parts": [os.path.basename(final)],
        "source": "phone",
        "ingest_reason": reason,
    }
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    sess["done"] = True
    drop_session(sid)
    fire_watcher(final)
    print(f"[ingest] {reason}: {os.path.basename(final)} "
          f"({duration}s, {os.path.getsize(final)} bytes) -> watcher")
    return {"ok": True, "file": os.path.basename(final), "duration_sec": duration}

def sweep_stale(cfg):
    """A tab Android killed leaves a session that never calls /finish. Finish it
    anyway rather than let the meeting evaporate."""
    now = time.time()
    with _lock:
        candidates = [s for s in _sessions.values()
                      if now - s.get("last_activity", 0) > STALE_SEC]
    for sess in candidates:
        with sess["lock"]:
            if sess.get("done"):
                continue
            try:
                if os.path.getsize(part_path(sess["sid"])) > 0:
                    finish_session(sess, cfg, reason="stale-sweep")
                else:
                    drop_session(sess["sid"])
            except (OSError, RuntimeError) as e:
                print(f"[ingest] stale sweep failed for {sess['sid']}: {e}",
                      file=sys.stderr)
                drop_session(sess["sid"])

def sweeper(cfg):
    while True:
        time.sleep(SWEEP_INTERVAL_SEC)
        try:
            sweep_stale(cfg)
        except Exception as e:                      # a sweeper must never die
            print(f"[ingest] sweeper error: {e}", file=sys.stderr)

# ---------- HTTP ----------

class IngestHandler(BaseHTTPRequestHandler):
    server_version = "ASBIngest/1.0"
    protocol_version = "HTTP/1.1"

    cfg = None
    token = ""
    host_label = ""

    # -- helpers --

    def _cors(self):
        """The phone may be served the page by one machine and upload to the
        other, which is a cross-origin POST carrying a custom header, so the
        preflight has to pass. Reachability is already limited to the owner's own
        tailnet; the token is what actually authorises."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "X-ASB-Token, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "86400")

    def _json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def _authed(self):
        supplied = self.headers.get("X-ASB-Token", "")
        if self.token and hmac.compare_digest(supplied, self.token):
            return True
        self._json(401, {"error": "bad or missing X-ASB-Token"})
        return False

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_CHUNK_BYTES:
            raise ValueError(f"chunk too large ({length} bytes)")
        remaining, buf = length, []
        while remaining > 0:
            block = self.rfile.read(min(remaining, 1 << 20))
            if not block:
                break
            buf.append(block)
            remaining -= len(block)
        return b"".join(buf)

    def _session(self, query):
        sid = (query.get("sid") or [""])[0]
        with _lock:
            sess = _sessions.get(sid)
        if sess is None:
            self._json(404, {"error": "unknown or already-finished session"})
            return None
        return sess

    def log_message(self, fmt, *args):
        # One line per meaningful call; chunk spam would bury everything else.
        msg = args[0] if args and isinstance(args[0], str) else ""
        if "/chunk" not in msg:
            print(f"  [ingest] {msg}")

    # -- routes --

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            # Deliberately unauthenticated: the phone races /health against both
            # machines to decide where to upload, before it has been paired.
            return self._json(200, {"ok": True, "host": self.host_label,
                                    "platform": self.cfg.get("platform", ""),
                                    "paired": bool(self.token),
                                    "sessions": len(_sessions),
                                    # So the phone learns the other machine's
                                    # address by itself instead of it being
                                    # typed into two devices by hand.
                                    "hosts": self.cfg.get("ingest", {}).get("hosts", [])})
        if path in ("/", "/index.html"):
            return self._serve_asset("index.html")
        asset = path.lstrip("/")
        if SAFE_ASSET.match(asset):
            return self._serve_asset(asset)
        self._json(404, {"error": "not found"})

    def _serve_asset(self, name):
        full = os.path.join(WEBAPP_DIR, name)
        if not os.path.isfile(full):
            return self._json(404, {"error": f"no such asset: {name}"})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The service worker must never be served from cache, or a stale one
        # pins the whole app to an old build.
        self.send_header("Cache-Control", "no-cache" if name.endswith((".js", ".html"))
                         else "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parts = urlsplit(self.path)
        path, query = parts.path, parse_qs(parts.query)
        if not self._authed():
            return
        try:
            if path == "/session":
                return self._start(query)
            if path == "/chunk":
                return self._chunk(query)
            if path == "/finish":
                return self._finish(query)
            if path == "/abort":
                return self._abort(query)
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        except (OSError, RuntimeError) as e:
            return self._json(500, {"error": str(e)})
        self._json(404, {"error": "not found"})

    def _start(self, query):
        raw = self._body()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("body must be JSON")

        title = (data.get("title") or "").strip() or "phone meeting"
        attendees = [a.strip() for a in (data.get("attendees") or []) if a.strip()]
        sid = secrets.token_urlsafe(12)

        # The phone may open this session late -- it records offline and creates
        # the session when a host comes back. Trust its start time so the file
        # stamp and the meeting's start_utc describe the meeting, not the upload.
        # Bounded to the last 24h and never in the future, so a phone with a wrong
        # clock cannot file a recording under some unrelated day.
        now = time.time()
        try:
            start_epoch = float(data.get("start_epoch") or 0) or now
        except (TypeError, ValueError):
            start_epoch = now
        if not (now - 86400) <= start_epoch <= now:
            start_epoch = now

        sess = {
            "sid": sid,
            "title": title,
            # Defaults True: a phone recording is usually a room conversation, and
            # watcher.py skips calendar matching for ad-hoc so the MOM cannot
            # inherit the title of whatever event happened to overlap it.
            "ad_hoc": bool(data.get("ad_hoc", True)),
            "attendees": attendees,
            "start_epoch": start_epoch,
            "last_activity": now,
            "next_seq": 0,
            "bytes": 0,
            "done": False,
            "lock": threading.Lock(),
        }
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        open(part_path(sid), "wb").close()
        with _lock:
            _sessions[sid] = sess
        save_session(sess)
        print(f"[ingest] session start: {title!r} ad_hoc={sess['ad_hoc']} sid={sid}")
        self._json(200, {"ok": True, "sid": sid, "title": title})

    def _chunk(self, query):
        sess = self._session(query)
        if sess is None:
            return
        try:
            seq = int((query.get("seq") or ["-1"])[0])
        except ValueError:
            raise ValueError("seq must be an integer")
        blob = self._body()

        with sess["lock"]:
            if sess.get("done"):
                return self._json(409, {"error": "session already finished"})
            # Idempotent replay: the phone retries a chunk whose response was lost,
            # and appending it twice would duplicate audio.
            if seq < sess["next_seq"]:
                return self._json(200, {"ok": True, "duplicate": True,
                                        "next_seq": sess["next_seq"]})
            if seq > sess["next_seq"]:
                return self._json(409, {"error": "out of order",
                                        "next_seq": sess["next_seq"]})
            with open(part_path(sess["sid"]), "ab") as f:
                f.write(blob)
            sess["next_seq"] = seq + 1
            sess["bytes"] += len(blob)
            sess["last_activity"] = time.time()
            save_session(sess)
        self._json(200, {"ok": True, "next_seq": sess["next_seq"], "bytes": sess["bytes"]})

    def _finish(self, query):
        sess = self._session(query)
        if sess is None:
            return
        self._body()   # drain, so keep-alive stays in sync
        with sess["lock"]:
            if sess.get("done"):
                return self._json(409, {"error": "session already finished"})
            result = finish_session(sess, self.cfg, reason="finish")
        self._json(200, result)

    def _abort(self, query):
        sess = self._session(query)
        if sess is None:
            return
        self._body()
        with sess["lock"]:
            drop_session(sess["sid"])
        print(f"[ingest] session aborted: {sess['sid']}")
        self._json(200, {"ok": True})

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("ASB_INGEST_PORT", DEFAULT_PORT)))
    ap.add_argument("--bind", default=os.environ.get("ASB_INGEST_BIND", DEFAULT_BIND),
                    help="127.0.0.1 (default; Tailscale fronts it) or 0.0.0.0 for LAN")
    ap.add_argument("--print-token", action="store_true",
                    help="print the pairing token, creating one if absent")
    args = ap.parse_args()

    if args.print_token:
        print(load_token(create=True))
        return

    cfg = load_config()
    ing = cfg.get("ingest", {})
    # CLI beats env beats config, so a one-off run can override without an edit.
    if args.port == DEFAULT_PORT and not os.environ.get("ASB_INGEST_PORT"):
        args.port = int(ing.get("port", DEFAULT_PORT))
    if args.bind == DEFAULT_BIND and not os.environ.get("ASB_INGEST_BIND"):
        args.bind = ing.get("bind", DEFAULT_BIND)
    global STALE_SEC
    STALE_SEC = int(ing.get("stale_minutes", STALE_SEC // 60)) * 60

    rec_dir = cfg["machine"].get("recordings_dir", "")
    if not rec_dir:
        sys.exit(f"ERROR: no recordings_dir for platform {cfg.get('platform')} "
                 f"in meeting-recorder/config.json")

    token = load_token(create=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    load_sessions_from_disk()

    IngestHandler.cfg = cfg
    IngestHandler.token = token
    IngestHandler.host_label = cfg.get("platform", "unknown")

    threading.Thread(target=sweeper, args=(cfg,), daemon=True).start()

    server = ThreadingHTTPServer((args.bind, args.port), IngestHandler)
    server.daemon_threads = True
    print(f"\n  [ingest] listening on http://{args.bind}:{args.port}")
    print(f"  Recordings:  {rec_dir}")
    print(f"  Webapp:      {WEBAPP_DIR}")
    print(f"  Token:       {TOKEN_PATH} ({'set' if token else 'MISSING'})")
    print(f"  Stale sweep: every {SWEEP_INTERVAL_SEC}s, cutoff {STALE_SEC // 60} min\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  [ingest] stopped.")
        server.server_close()

if __name__ == "__main__":
    main()
