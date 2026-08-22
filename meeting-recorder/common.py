#!/usr/bin/env python3
"""Shared helpers for the local meeting note-taker (recorder / transcribe / watcher).

Platform detection is delegated to .agent/scripts/harness_config.py, which caches
the output of detect_platform.sh. This file used to keep its own Python copy of
that table; two implementations of one rule is one too many.
"""
import json
import os
import platform
import re
import shutil
import sys

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(MODULE_DIR)
CONFIG_PATH = os.path.join(MODULE_DIR, "config.json")

# Appended, not inserted at 0, so this never shadows a module the recorder
# scripts import from their own directory.
_AGENT_SCRIPTS = os.path.join(REPO_ROOT, ".agent", "scripts")
if _AGENT_SCRIPTS not in sys.path:
    sys.path.append(_AGENT_SCRIPTS)
try:
    import harness_config
except Exception:  # absent or unimportable -> the inline fallback below takes over
    harness_config = None

# Named harness_secrets, NOT secrets: a module called secrets.py on sys.path
# shadows the standard library's secrets, which ingest_server.py relies on for
# token generation.
import harness_secrets as _secrets

# Cron runs with a minimal PATH that excludes ~/.local/bin, so a bare "ffmpeg"
# in config resolves fine in an interactive shell and dies under cron with
# FileNotFoundError. Search these before giving up.
FFMPEG_FALLBACK_DIRS = (
    os.path.expanduser("~/.local/bin"),
    "/usr/local/bin",
    "/usr/bin",
    "/opt/homebrew/bin",
)

def detect_platform():
    """Return macos | wsl | windows, matching config.json's `machines` keys.

    Asks harness_config first so there is one platform table in the repo. The
    inline table stays as a fallback only: the recorder runs under cron and must
    not fail to pick a machine profile because a helper module moved.

    Must stay on harness_config's platform-only path (platform_facts, which
    platform() also wraps) and never on load(): load() resolves the RUNTIME too, and
    outside claude-code that shells into `agy models` behind an 8s timeout,
    measured at 6.9s. This function only ever needed the OS name, and the
    recorder calls it on every cron tick."""
    if harness_config is not None:
        try:
            plat = harness_config.platform_facts().get("platform")
            if plat in ("macos", "wsl", "windows"):
                return plat
        except Exception:
            pass
    sysname = platform.system()
    if sysname == "Darwin":
        return "macos"
    if sysname == "Linux":
        return "wsl"      # the owner's Linux is always WSL
    return "windows"

def resolve_ffmpeg(value):
    """Turn whatever config says into an absolute, existing ffmpeg path.

    Accepts an absolute path, a bare name, or nothing. Returns the input
    unchanged when it cannot do better, so callers still fail loudly rather
    than silently transcoding with the wrong binary."""
    value = (value or "ffmpeg").strip()
    if os.sep in value:
        expanded = os.path.expanduser(value)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        value = os.path.basename(expanded)
    found = shutil.which(value)
    if found:
        return found
    for d in FFMPEG_FALLBACK_DIRS:
        cand = os.path.join(d, value)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return value

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    plat = detect_platform()
    machine = dict(cfg.get("machines", {}).get(plat, {}))
    if machine.get("recordings_dir"):
        machine["recordings_dir"] = os.path.expanduser(machine["recordings_dir"])
    if machine.get("whispercpp_model"):
        machine["whispercpp_model"] = os.path.expanduser(machine["whispercpp_model"])
    machine["ffmpeg"] = resolve_ffmpeg(machine.get("ffmpeg"))
    cfg["platform"] = plat
    cfg["machine"] = machine
    return cfg

def slugify(title):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    return slug[:60] or "meeting"

def fmt_ts(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def parse_json_tail(text):
    """gcal_manager prints a 'Fetching events...' line before the JSON on
    stdout; parse from the first [ or { onward."""
    for i, ch in enumerate(text):
        if ch in "[{":
            return json.loads(text[i:])
    raise ValueError("no JSON found in output")

# Credential lookup is shared with the rest of the harness (.agent/scripts/harness_secrets.py)
# so a key pasted once is visible to every skill. Two recorder-specific rungs:
# meeting-recorder/.env first, because it sits next to the thing being configured,
# and the gemini-image skill's token.env last, which is where this key used to be
# borrowed from in the owner's workspace only.
_RECORDER_ENV = [os.path.join(MODULE_DIR, ".env")]
_LEGACY_ENV = [os.path.join(REPO_ROOT, ".agent", "skills", "gemini-image", "token.env")]

def load_secret(name, default=None):
    """Find a credential by env-var name, or return `default`.

    Returns rather than exits: a missing key means "this provider is unavailable,
    try the next one", not "kill the run". The old load_gemini_key called
    sys.exit(), which is why a machine with no Gemini key could take down a whole
    watcher pass instead of quietly falling through to another engine.
    """
    return _secrets.load_secret(name, default, extra_files=_LEGACY_ENV,
                                extra_first=_RECORDER_ENV)

def secret_search_path():
    """Human-readable list of the places load_secret looks, for error messages
    and for --doctor. A user who is told 'no key found' must also be told where
    to put one."""
    return _secrets.search_files(extra_first=_RECORDER_ENV)

def load_gemini_key():
    """Back-compat wrapper. Prefer load_secret('GEMINI_API_KEY')."""
    return load_secret("GEMINI_API_KEY")
