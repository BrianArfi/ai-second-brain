#!/usr/bin/env python3
"""Shared helpers for the local meeting recorder.

Trimmed copy of the harness module, carrying only what `recorder.py` imports:
detect_platform, load_config, slugify (and resolve_ffmpeg, which load_config uses).

The full version also has transcription and Gemini helpers. Those belong to the
pipeline that turns a recording into notes, not to capturing one, and they reach
into a repo layout a member's workspace does not have -- so they are deliberately
absent rather than shipped and unreachable.
"""
import json
import os
import platform
import re
import shutil

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(MODULE_DIR, "config.json")

# A bare "ffmpeg" in config resolves fine in an interactive shell and dies under a
# launcher with a minimal PATH. Search these before giving up.
FFMPEG_FALLBACK_DIRS = (
    os.path.expanduser("~/.local/bin"),
    "/usr/local/bin",
    "/usr/bin",
    "/opt/homebrew/bin",
)

def detect_platform():
    """Return macos | wsl | windows, matching config.json's `machines` keys.

    `wsl` also covers plain Linux: both capture through PulseAudio, so they share
    a config section rather than duplicating one."""
    sysname = platform.system()
    if sysname == "Darwin":
        return "macos"
    if sysname == "Linux":
        return "wsl"
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
    machine["ffmpeg"] = resolve_ffmpeg(machine.get("ffmpeg"))
    cfg["platform"] = plat
    cfg["machine"] = machine
    return cfg

def slugify(title):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    return slug[:60] or "meeting"
