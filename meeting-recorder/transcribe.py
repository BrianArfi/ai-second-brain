#!/usr/bin/env python3
"""Transcription for the local meeting note-taker: provider chain, config-driven.

Providers are listed in config.json under `transcription.providers` and tried in
order until one works. Adding a provider is a config entry, not a code change.

  gemini      Google AI Studio, audio-in. The only one that returns SPEAKER
              LABELS, which is why it leads the default chain. Has a free tier.
  groq        whisper-large-v3 over an OpenAI-compatible route. Free tier, fast.
  openai      whisper-1, same route. Any OpenAI-compatible /audio/transcriptions
              endpoint works by setting `base_url` -- a self-hosted whisper
              server, another vendor, a local router.
  whispercpp  Local GPU (Metal on Apple Silicon, Vulkan/CUDA elsewhere).
  cpu         Local faster-whisper. Needs no account and no GPU, and is roughly
              as slow as the audio is long, so it is always appended LAST and
              never listed first. Governed by `transcription.cpu_fallback`,
              which defaults to the inverse of `require_gpu`.

Every provider raises EngineSkip rather than exiting, so a missing key means
"try the next one" instead of killing a watcher pass. When the whole chain
fails, the error names each failure and points at --doctor.

API keys come from the environment first, then meeting-recorder/.env, then
.env / secrets.env in the workspace root. Nothing is tied to one person's
machine or to another skill's credentials.

Usage:
  python3 transcribe.py --in recording.wav --out transcript.md \
      [--engine auto|gemini|groq|openai|whispercpp|cpu] [--lang auto|en|id]
  python3 transcribe.py --doctor      # what works here, and how to fix the rest

Output: markdown transcript with **[mm:ss]** timestamps (same format the /mom
pipeline already consumes) + a plain .txt sibling.
"""
import argparse
import base64
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from common import (REPO_ROOT, fmt_ts, load_config, load_secret,
                    secret_search_path)

LOG_PATH = os.path.join(REPO_ROOT, "dashboard-data", "meeting_recorder_log.jsonl")
GEMINI_BASE = "https://generativelanguage.googleapis.com"
# Gemini audio pricing is folded into normal token pricing; log tokens + est cost.
GEMINI_PRICE_PER_MTOK = {"in": 0.30, "out": 2.50}  # flash-tier list price, USD

GEMINI_PROMPT = """Transcribe this meeting recording completely and accurately.
The audio may mix English and Indonesian; transcribe each utterance in its
original language, do not translate.

Output format, one line per utterance, nothing else:
**[mm:ss]** Speaker N: text

Rules:
- Timestamps are elapsed time from the start of the audio.
- Distinguish speakers by voice; label them Speaker 1, Speaker 2, ... consistently.
  If a speaker states their own name or is addressed by name, use that name instead.
- Do not summarize, skip, or clean up content. Include the full transcript.
"""

class EngineSkip(Exception):
    """This engine is unavailable/failed; try the next one in the chain."""

def log_row(row):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    row["ts_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

def audio_duration(path, ffmpeg):
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe") if os.sep in ffmpeg else "ffprobe"
    try:
        out = subprocess.run([ffprobe, "-v", "quiet", "-show_entries",
                              "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except Exception:
        pass
    # ffprobe is a separate binary and is not always installed next to ffmpeg
    # (macOS 2026-08-19: ffmpeg present, ffprobe absent, so every transcript
    # header read "Audio duration: 00:00"). ffmpeg itself reports the duration
    # on stderr when asked to decode with no output file.
    try:
        err = subprocess.run([ffmpeg, "-i", path], capture_output=True,
                             text=True, timeout=60).stderr
        m = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)", err)
        if m:
            h, mnt, s = m.groups()
            return int(h) * 3600 + int(mnt) * 60 + float(s)
    except Exception:
        pass
    return 0.0

# ---------- engine: whisper.cpp (GPU only) ----------

def _winpath(p):
    """WSL path -> Windows path for args passed to a Windows .exe via interop."""
    return subprocess.run(["wslpath", "-w", p], capture_output=True,
                          text=True, check=True).stdout.strip()

def run_whispercpp(audio, cfg, lang):
    machine = cfg["machine"]
    bin_path = machine.get("whispercpp_bin") or ""
    model = machine.get("whispercpp_model") or ""
    if not bin_path or not model or not os.path.exists(model):
        raise EngineSkip("whisper.cpp binary/model not configured on this machine")

    # A Windows .exe invoked from WSL can't read WSL-only paths (/tmp): keep the
    # temp files on a Windows drive and pass Windows-style path arguments.
    win_interop = bin_path.lower().endswith(".exe")
    tmp_parent = os.path.dirname(bin_path) if win_interop else None

    ffmpeg = machine.get("ffmpeg", "ffmpeg")
    with tempfile.TemporaryDirectory(dir=tmp_parent) as td:
        wav16 = os.path.join(td, "audio16k.wav")
        subprocess.run([ffmpeg, "-y", "-v", "quiet", "-i", audio,
                        "-ac", "1", "-ar", "16000", wav16], check=True, timeout=600)
        prefix = os.path.join(td, "out")
        if win_interop:
            cmd = [bin_path, "-m", _winpath(model), "-f", _winpath(wav16),
                   "-oj", "-of", _winpath(prefix)]
        else:
            cmd = [bin_path, "-m", model, "-f", wav16, "-oj", "-of", prefix]
        # -mc 0 drops the cross-segment text prompt. Carrying it is what lets
        # whisper lock into a repetition loop on quiet or crosstalk-heavy audio
        # (the 2026-08-19 ExampleVendor standup produced 45 identical segments across
        # a full minute). Costs a little context, buys back the lost minute.
        cmd += ["-mc", "0"]
        if lang != "auto":
            cmd += ["-l", lang]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3 * 3600)
        if r.returncode != 0:
            raise EngineSkip(f"whisper.cpp failed: {r.stderr[-300:]}")
        gpu_markers = ("Metal", "Vulkan", "CUDA", "gpu device")
        used_gpu = any(m.lower() in (r.stderr + r.stdout).lower() for m in gpu_markers)
        if cfg.get("require_gpu", True) and not used_gpu:
            raise EngineSkip("whisper.cpp ran without GPU (require_gpu on) -> skipping to CLI")
        with open(prefix + ".json", encoding="utf-8") as f:
            data = json.load(f)

    lines = []
    repeat_of, repeats = None, 0
    for seg in data.get("transcription", []):
        text = seg.get("text", "").strip()
        if not text:
            continue
        # Second line of defence against a repetition loop: collapse a phrase
        # that repeats back to back. Real speech repeats twice ("okay okay"),
        # never twenty times, so anything past the third copy is a hallucination
        # and is dropped rather than shipped into the MOM as content.
        if text == repeat_of:
            repeats += 1
            if repeats >= 3:
                continue
        else:
            repeat_of, repeats = text, 0
        start_s = seg.get("offsets", {}).get("from", 0) / 1000.0
        lines.append(f"**[{fmt_ts(start_s)}]** {text}")
    if not lines:
        raise EngineSkip("whisper.cpp produced an empty transcript")
    return lines, f"whisper.cpp `{os.path.basename(model)}` (GPU)"

# ---------- engine: cli (Gemini API, audio-in) ----------

def _gemini_req(url, body, key, timeout=1800):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def _gemini_upload_file(path, mime, key, base=GEMINI_BASE):
    """Files API resumable upload; returns the file URI once ACTIVE."""
    size = os.path.getsize(path)
    start = urllib.request.Request(
        f"{base}/upload/v1beta/files",
        data=json.dumps({"file": {"display_name": os.path.basename(path)}}).encode(),
        headers={"x-goog-api-key": key,
                 "X-Goog-Upload-Protocol": "resumable",
                 "X-Goog-Upload-Command": "start",
                 "X-Goog-Upload-Header-Content-Length": str(size),
                 "X-Goog-Upload-Header-Content-Type": mime,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(start, timeout=120) as r:
        upload_url = r.headers["X-Goog-Upload-URL"]
    with open(path, "rb") as f:
        blob = f.read()
    up = urllib.request.Request(
        upload_url, data=blob,
        headers={"X-Goog-Upload-Command": "upload, finalize",
                 "X-Goog-Upload-Offset": "0",
                 "Content-Length": str(size)})
    with urllib.request.urlopen(up, timeout=1800) as r:
        info = json.load(r)["file"]
    # wait until processed
    for _ in range(60):
        if info.get("state") == "ACTIVE":
            return info["uri"]
        time.sleep(5)
        req = urllib.request.Request(f"{base}/v1beta/{info['name']}",
                                     headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=60) as r:
            info = json.load(r)
    raise EngineSkip(f"Gemini file stuck in state {info.get('state')}")

def run_gemini(audio, cfg, lang, provider=None):
    provider = provider or {}
    # base_url lets this same code talk to a local router that proxies Gemini
    # instead of Google directly. Such a router may need no credential at all,
    # which is what no_auth covers.
    base = (provider.get("base_url") or GEMINI_BASE).rstrip("/")
    key_env = provider.get("api_key_env") or cfg.get("gemini_key_env") or "GEMINI_API_KEY"
    key = load_secret(key_env)
    if not key and not provider.get("no_auth"):
        # Missing key means "try the next provider", never "kill the run".
        raise EngineSkip(f"no {key_env} (looked in the environment and "
                         + ", ".join(secret_search_path()) + ")")
    key = key or "none"
    machine = cfg["machine"]
    ffmpeg = machine.get("ffmpeg", "ffmpeg")
    model = provider.get("model") or cfg.get("gemini_model", "gemini-2.5-flash")

    with tempfile.TemporaryDirectory() as td:
        # compress to ogg/opus 16k mono: ~1 MB per 8 min, keeps requests small
        ogg = os.path.join(td, "audio.ogg")
        subprocess.run([ffmpeg, "-y", "-v", "quiet", "-i", audio, "-ac", "1",
                        "-ar", "16000", "-c:a", "libopus", "-b:a", "24k", ogg],
                       check=True, timeout=600)
        size = os.path.getsize(ogg)
        prompt = GEMINI_PROMPT
        if lang != "auto":
            prompt += f"\nThe meeting is primarily in '{lang}'."
        if size < 15 * 1024 * 1024:  # inline under the ~20MB request cap
            audio_part = {"inline_data": {
                "mime_type": "audio/ogg",
                "data": base64.b64encode(open(ogg, "rb").read()).decode()}}
        else:
            uri = _gemini_upload_file(ogg, "audio/ogg", key, base)
            audio_part = {"file_data": {"mime_type": "audio/ogg", "file_uri": uri}}

        body = {"contents": [{"parts": [audio_part, {"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 65536}}
        try:
            data = _gemini_req(f"{base}/v1beta/models/{model}:generateContent",
                               body, key)
        except urllib.error.HTTPError as e:
            raise EngineSkip(f"Gemini HTTP {e.code}: {e.read().decode()[:300]}")

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise EngineSkip(f"Gemini returned no text: {json.dumps(data)[:300]}")

    usage = data.get("usageMetadata", {})
    in_tok = usage.get("promptTokenCount", 0)
    out_tok = usage.get("candidatesTokenCount", 0)
    cost = (in_tok * GEMINI_PRICE_PER_MTOK["in"] +
            out_tok * GEMINI_PRICE_PER_MTOK["out"]) / 1e6
    log_row({"kind": "transcribe", "engine": f"gemini:{model}",
             "file": os.path.basename(audio), "in_tok": in_tok,
             "out_tok": out_tok, "est_usd": round(cost, 4)})

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise EngineSkip("Gemini transcript empty")
    return lines, f"Gemini `{model}` (audio-in, speaker labels, ~${cost:.3f})"

# ---------- engine: any OpenAI-compatible /audio/transcriptions ----------

# One implementation covers OpenAI, Groq, and anything self-hosted that speaks the
# same route (whisper.cpp's own server, LocalAI, vLLM). They differ only in
# base_url and model name, so a new provider is a config entry, not new code.
OPENAI_AUDIO_PRESETS = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "whisper-1",
               "api_key_env": "OPENAI_API_KEY", "limit_mb": 25},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "whisper-large-v3",
             "api_key_env": "GROQ_API_KEY", "limit_mb": 25},
}

def _multipart(fields, file_field, filename, file_bytes, mime):
    """Build a multipart/form-data body. Written out by hand because this repo
    stays on the standard library, and `requests` is not a dependency of the
    recorder."""
    boundary = "----asb" + base64.urlsafe_b64encode(os.urandom(12)).decode().strip("=")
    out = []
    for k, v in fields.items():
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    out.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\";"
                f" filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n").encode())
    out.append(file_bytes)
    out.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"

def _compress_for_upload(audio, ffmpeg, td, seconds=None, offset=0.0):
    """ogg/opus 16k mono: about 10 MB per hour, so a normal meeting fits inside
    the 25 MB cap these APIs impose."""
    out = os.path.join(td, f"part_{int(offset)}.ogg")
    cmd = [ffmpeg, "-y", "-v", "quiet"]
    if offset:
        cmd += ["-ss", str(offset)]
    cmd += ["-i", audio]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k", out]
    subprocess.run(cmd, check=True, timeout=1800)
    return out

def _post_audio(path, base_url, model, key, lang, timeout=1800):
    fields = {"model": model, "response_format": "verbose_json"}
    if lang and lang != "auto":
        fields["language"] = lang
    body, ctype = _multipart(fields, "file", os.path.basename(path),
                             open(path, "rb").read(), "audio/ogg")
    req = urllib.request.Request(base_url.rstrip("/") + "/audio/transcriptions",
                                 data=body,
                                 headers={"Content-Type": ctype,
                                          "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def run_openai_audio(audio, cfg, lang, provider):
    kind = provider.get("kind", "openai")
    preset = OPENAI_AUDIO_PRESETS.get(kind, OPENAI_AUDIO_PRESETS["openai"])
    base_url = provider.get("base_url") or preset["base_url"]
    model = provider.get("model") or preset["model"]
    key_env = provider.get("api_key_env") or preset["api_key_env"]
    limit = int(provider.get("limit_mb") or preset["limit_mb"]) * 1024 * 1024

    key = load_secret(key_env)
    if not key:
        raise EngineSkip(f"no {key_env} (looked in the environment and "
                         f"{', '.join(secret_search_path())})")

    ffmpeg = cfg["machine"].get("ffmpeg", "ffmpeg")
    total = audio_duration(audio, ffmpeg)
    lines = []
    with tempfile.TemporaryDirectory() as td:
        whole = _compress_for_upload(audio, ffmpeg, td)
        # A two-hour meeting still clears the cap after compression, but a very
        # long or high-noise one may not. Split on time and shift each part's
        # timestamps, rather than silently transcribing only the first 25 MB.
        if os.path.getsize(whole) <= limit or not total:
            parts = [(whole, 0.0)]
        else:
            per_sec = os.path.getsize(whole) / max(total, 1)
            window = max(300, int(limit * 0.9 / max(per_sec, 1)))
            parts = []
            off = 0.0
            while off < total:
                parts.append((_compress_for_upload(audio, ffmpeg, td, window, off), off))
                off += window
            print(f"[transcribe] {kind}: audio exceeds {limit // 1024 // 1024}MB, "
                  f"split into {len(parts)} part(s)", flush=True)

        for path, offset in parts:
            try:
                data = _post_audio(path, base_url, model, key, lang)
            except urllib.error.HTTPError as e:
                raise EngineSkip(f"{kind} HTTP {e.code}: {e.read().decode()[:300]}")
            except urllib.error.URLError as e:
                raise EngineSkip(f"{kind} unreachable: {e.reason}")
            segs = data.get("segments") or []
            if segs:
                for s in segs:
                    text = (s.get("text") or "").strip()
                    if text:
                        lines.append(f"**[{fmt_ts(float(s.get('start', 0)) + offset)}]** {text}")
            elif (data.get("text") or "").strip():
                lines.append(f"**[{fmt_ts(offset)}]** {data['text'].strip()}")

    if not lines:
        raise EngineSkip(f"{kind} returned an empty transcript")
    log_row({"kind": "transcribe", "engine": f"{kind}:{model}",
             "file": os.path.basename(audio), "parts": len(lines)})
    # Said plainly in the header because it changes how the MOM reads: these APIs
    # return no diarization, so every line is unattributed.
    return lines, f"{kind} `{model}` (no speaker labels)"

# ---------- engine: cpu (local, no account, slow) ----------

# Run out-of-process so the watcher never imports torch/ctranslate2 into itself.
CPU_SNIPPET = """
import json, sys
from faster_whisper import WhisperModel
audio, model, lang = sys.argv[1], sys.argv[2], sys.argv[3]
m = WhisperModel(model, device="cpu", compute_type="int8")
segs, _ = m.transcribe(audio, language=(None if lang == "auto" else lang), vad_filter=True)
print(json.dumps([{"start": s.start, "text": s.text} for s in segs]))
"""

def _cpu_interpreter():
    """First interpreter that actually has faster-whisper installed."""
    candidates = [sys.executable, os.path.expanduser("~/.venvs/whisper/bin/python")]
    for py in candidates:
        if not py or not os.path.exists(py):
            continue
        probe = subprocess.run([py, "-c", "import faster_whisper"],
                               capture_output=True)
        if probe.returncode == 0:
            return py
    return None

def run_cpu(audio, cfg, lang, provider=None):
    provider = provider or {}
    model = provider.get("model") or "base"
    py = _cpu_interpreter()
    if not py:
        # Deliberately not sys.executable: under the ASB app or a cron shell that
        # can be Xcode's bundled Python or some other interpreter the user should
        # not be installing packages into.
        raise EngineSkip(
            "faster-whisper is not installed. Install it with "
            "`python3 -m pip install faster-whisper`, or configure an API "
            "provider (`python3 meeting-recorder/transcribe.py --doctor`).")

    dur = audio_duration(audio, cfg["machine"].get("ffmpeg", "ffmpeg"))
    # Loud on purpose. CPU transcription is roughly real time, so an hour of
    # meeting is about an hour of laptop, and the user deserves to know that
    # before it happens rather than wonder why the fan is on.
    print(f"[transcribe] ⚠ CPU fallback: no GPU and no API provider available.\n"
          f"[transcribe]   Model '{model}' on CPU takes roughly as long as the audio "
          f"itself (~{fmt_ts(dur)}).\n"
          f"[transcribe]   A free Gemini or Groq API key makes this near-instant: "
          f"see `python3 transcribe.py --doctor`.", flush=True)

    r = subprocess.run([py, "-c", CPU_SNIPPET, audio, model, lang or "auto"],
                       capture_output=True, text=True, timeout=6 * 3600)
    if r.returncode != 0:
        raise EngineSkip(f"faster-whisper failed: {r.stderr[-300:]}")
    try:
        segs = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise EngineSkip(f"faster-whisper gave unreadable output: {r.stdout[-200:]}")

    lines = [f"**[{fmt_ts(s['start'])}]** {s['text'].strip()}"
             for s in segs if (s.get("text") or "").strip()]
    if not lines:
        raise EngineSkip("faster-whisper produced an empty transcript")
    return lines, f"faster-whisper `{model}` (CPU, no speaker labels)"

# ---------- orchestration ----------

# ---------- provider verification ----------
#
# A provider that ADVERTISES audio support but silently drops the audio is the
# worst failure this pipeline has: it returns fluent, meeting-shaped text with no
# error anywhere, and that text becomes a MOM. Observed 2026-08-21 on a local
# router, where six of seven Gemini models invented a transcript and only one
# admitted the audio was missing.
#
# So any provider reached over a NON-DEFAULT endpoint must first transcribe a
# known 3-second clip and be checked against its ground truth. The sentence is
# deliberately absurd, so a model that never heard the audio cannot guess it.

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PROBE_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provider_probe.json")

def provider_signature(provider):
    return "|".join([provider.get("kind", "?"),
                     provider.get("base_url") or "default",
                     provider.get("model") or "default"])

def needs_verification(provider):
    """Stock cloud endpoints are trusted; anything redirected is not. The risk
    comes from a proxy in the middle, not from the vendor."""
    if provider.get("verify") is not None:
        return bool(provider["verify"])
    return bool(provider.get("base_url"))

def _probe_state():
    if os.path.exists(PROBE_STATE):
        try:
            with open(PROBE_STATE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {}

def _save_probe(sig, ok, detail):
    state = _probe_state()
    state[sig] = {"ok": ok, "detail": detail[:400],
                  "checked_utc": datetime.datetime.now(datetime.timezone.utc)
                  .isoformat(timespec="seconds")}
    try:
        with open(PROBE_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass

def verify_provider(provider, cfg, force=False):
    """Returns (ok, detail). Cached, because this costs a real API call."""
    sig = provider_signature(provider)
    if not force:
        prev = _probe_state().get(sig)
        if prev:
            return prev["ok"], prev["detail"]

    truth_path = os.path.join(FIXTURES_DIR, "probe.json")
    clip = os.path.join(FIXTURES_DIR, "probe.ogg")
    if not (os.path.exists(truth_path) and os.path.exists(clip)):
        return True, "no probe fixture shipped, verification skipped"
    with open(truth_path, encoding="utf-8") as f:
        truth = json.load(f)

    try:
        lines, _ = run_provider(provider, clip, cfg, "en", verified=True)
    except EngineSkip as e:
        return False, f"probe could not run: {e}"
    except Exception as e:
        return False, f"probe failed: {e}"

    heard = " ".join(lines).lower()
    hits = [k for k in truth["keywords"] if k.lower() in heard]
    if len(hits) >= int(truth.get("min_keywords", 3)):
        return True, f"probe passed ({len(hits)}/{len(truth['keywords'])} keywords)"
    return False, (f"probe FAILED: expected \"{truth['sentence']}\", got "
                   f"\"{' '.join(lines)[:120]}\". This endpoint accepts audio and "
                   f"returns text that does not match it, so it would invent "
                   f"meeting minutes. Disabled.")

LEGACY_KIND = {"cli": "gemini", "whispercpp": "whispercpp", "cpu": "cpu"}

# What `auto` means when config.json carries no `transcription.providers` block:
# the historical chain, so an existing install behaves exactly as before.
DEFAULT_PROVIDERS = [
    {"kind": "gemini"},
    {"kind": "groq"},
    {"kind": "openai"},
    {"kind": "whispercpp"},
]

# Local LLM routers that proxy a Gemini subscription. Probed only when
# `transcription.autodetect_local` is on, and whatever is found still has to
# pass verify_provider before it may touch a real meeting.
LOCAL_ROUTERS = [
    {"name": "9router", "models_url": "http://127.0.0.1:20128/v1/models",
     "gemini_base": "http://127.0.0.1:20128"},
]

def detect_local_router(timeout=2):
    """Return a provider dict for a reachable local router, or None.

    Picks a Gemini model the router claims can take audio. That claim is not
    trusted -- it is exactly the claim that was false on 2026-08-21 -- it only
    decides which model the probe interrogates.
    """
    for r in LOCAL_ROUTERS:
        try:
            with urllib.request.urlopen(r["models_url"], timeout=timeout) as resp:
                data = json.load(resp)
        except Exception:
            continue
        models = data.get("data") or []
        audio_gemini = [m["id"] for m in models
                        if "gemini" in m.get("id", "").lower()
                        and (m.get("capabilities") or {}).get("audioInput")]
        if not audio_gemini:
            continue
        return {"kind": "gemini", "base_url": r["gemini_base"],
                "model": audio_gemini[0], "no_auth": True, "verify": True,
                "_source": f"autodetected {r['name']}"}
    return None

def provider_chain(cfg, engine):
    """Ordered list of provider dicts to try.

    Config drives this, so adding a provider is an edit to config.json rather
    than a code change. `engine` on the command line still pins a single one,
    which is what --engine has always meant.
    """
    block = cfg.get("transcription") or {}
    providers = [dict(p) for p in (block.get("providers") or DEFAULT_PROVIDERS)]

    # A user whose Gemini access comes from a local router has no API key to put
    # anywhere, so nothing in the config would ever name it. Find it instead.
    if block.get("autodetect_local"):
        found = detect_local_router()
        if found and not any(p.get("base_url") == found["base_url"] for p in providers):
            # After the keyed providers, before the local engines: it is free and
            # fast, but it is also the one most likely to be lying.
            at = next((i for i, p in enumerate(providers)
                       if p.get("kind") in ("whispercpp", "cpu")), len(providers))
            providers.insert(at, found)

    if engine and engine != "auto":
        kind = LEGACY_KIND.get(engine, engine)
        picked = [p for p in providers if p.get("kind") == kind]
        return picked or [{"kind": kind}]

    # CPU is appended, never listed first: it always "works", so putting it in
    # the middle of a chain would mask a misconfigured fast provider behind an
    # hour of quietly burning laptop.
    if not any(p.get("kind") == "cpu" for p in providers):
        cpu_ok = block.get("cpu_fallback")
        if cpu_ok is None:
            cpu_ok = not cfg.get("require_gpu", False)
        if cpu_ok:
            providers.append({"kind": "cpu"})
    return providers

def run_provider(provider, audio, cfg, lang, verified=False):
    """`verified` is set only by verify_provider itself, so the probe can call
    the provider without recursing back into its own gate."""
    if not verified and needs_verification(provider):
        ok, detail = verify_provider(provider, cfg)
        if not ok:
            raise EngineSkip(detail)

    kind = provider.get("kind")
    if kind == "gemini":
        return run_gemini(audio, cfg, lang, provider)
    if kind == "whispercpp":
        return run_whispercpp(audio, cfg, lang)
    if kind == "cpu":
        return run_cpu(audio, cfg, lang, provider)
    if kind in OPENAI_AUDIO_PRESETS or provider.get("base_url"):
        return run_openai_audio(audio, cfg, lang, provider)
    raise EngineSkip(f"unknown provider kind '{kind}'")

def transcribe(audio, out_md, engine=None, lang=None, cfg=None):
    """Returns (out_md, engine_note). Raises RuntimeError if all providers fail."""
    cfg = cfg or load_config()
    engine = engine or cfg.get("engine", "auto")
    lang = lang or cfg.get("language", "auto")
    chain = provider_chain(cfg, engine)

    errors = []
    for provider in chain:
        eng = provider.get("kind")
        try:
            print(f"[transcribe] trying provider: {eng}", flush=True)
            lines, note = run_provider(provider, audio, cfg, lang)
            dur = audio_duration(audio, cfg["machine"].get("ffmpeg", "ffmpeg"))
            header = (f"# Transcript: {os.path.basename(audio)}\n\n"
                      f"- Engine: {note}\n"
                      f"- Audio duration: {fmt_ts(dur)}\n"
                      f"- Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n---\n\n")
            os.makedirs(os.path.dirname(os.path.abspath(out_md)) or ".", exist_ok=True)
            with open(out_md, "w", encoding="utf-8") as f:
                f.write(header + "\n".join(lines) + "\n")
            txt = os.path.splitext(out_md)[0] + ".txt"
            with open(txt, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print(f"[transcribe] OK via {eng} -> {out_md}", flush=True)
            return out_md, note
        except EngineSkip as e:
            print(f"[transcribe] {eng} skipped: {e}", file=sys.stderr, flush=True)
            errors.append(f"{eng}: {e}")
        except (subprocess.SubprocessError, OSError) as e:
            print(f"[transcribe] {eng} error: {e}", file=sys.stderr, flush=True)
            errors.append(f"{eng}: {e}")
    raise RuntimeError(
        "no transcription provider worked.\n  " + "\n  ".join(errors)
        + "\n\nRun `python3 meeting-recorder/transcribe.py --doctor` to see what is "
          "missing and how to fix it.")

# ---------- doctor ----------

SETUP_HELP = {
    "gemini": ("GEMINI_API_KEY", "Free tier. Create a key at https://aistudio.google.com/apikey "
                                 "-- the only provider here that returns speaker labels."),
    "groq": ("GROQ_API_KEY", "Free tier, very fast whisper-large-v3. "
                             "Create a key at https://console.groq.com/keys"),
    "openai": ("OPENAI_API_KEY", "Paid. Create a key at https://platform.openai.com/api-keys"),
}

def doctor(cfg):
    """Say exactly what is usable on THIS machine and what to do about the rest.

    Exists because the previous failure mode was a silent skip: a user with no
    key and no GPU got an empty chain and no explanation."""
    print("\nTranscription providers, in the order they will be tried:\n")
    chain = provider_chain(cfg, "auto")
    usable = 0

    for p in chain:
        kind = p.get("kind")
        if kind == "gemini":
            key_env = p.get("api_key_env") or "GEMINI_API_KEY"
            if p.get("no_auth"):
                ok, detail = True, "reachable, no credential needed"
            else:
                ok = bool(load_secret(key_env))
                detail = f"{key_env} found" if ok else f"no {key_env}"
        elif kind in OPENAI_AUDIO_PRESETS or p.get("base_url"):
            preset = OPENAI_AUDIO_PRESETS.get(kind, OPENAI_AUDIO_PRESETS["openai"])
            key_env = p.get("api_key_env") or preset["api_key_env"]
            ok = bool(load_secret(key_env))
            detail = f"{key_env} found" if ok else f"no {key_env}"
        elif kind == "whispercpp":
            b = cfg["machine"].get("whispercpp_bin") or ""
            m = cfg["machine"].get("whispercpp_model") or ""
            ok = bool(b and m and os.path.exists(os.path.expanduser(m)))
            detail = (f"{os.path.basename(m)}" if ok
                      else "binary or model not configured for this machine")
        elif kind == "cpu":
            py = _cpu_interpreter()
            ok = bool(py)
            detail = (f"faster-whisper via {py}" if ok
                      else "faster-whisper not installed")
        else:
            ok, detail = False, "unknown provider kind"

        label = kind + ("@router" if p.get("base_url") else "")
        usable += 1 if ok else 0
        print(f"  [{'OK ' if ok else '   '}] {label:<16} {detail}")
        if p.get("_source"):
            print(f"       {p['_source']}, model {p.get('model')}")
        if ok and needs_verification(p):
            prev = _probe_state().get(provider_signature(p))
            if prev is None:
                print("       audio not verified yet -- run --verify-providers")
            elif not prev["ok"]:
                usable -= 1
                print(f"       DISABLED: {prev['detail'][:160]}")
            else:
                print(f"       verified: {prev['detail']}")
        if not ok and kind in SETUP_HELP:
            env, how = SETUP_HELP[kind]
            print(f"       -> {how}")

    print(f"\nCredentials are read from the environment, then from:")
    for path in secret_search_path():
        print(f"  {path}{'  (exists)' if os.path.exists(path) else ''}")
    print("\nFile format is one KEY=value per line, for example:\n  GEMINI_API_KEY=...\n")

    if not usable:
        print("Nothing is usable yet. The quickest fix is a free Gemini key:\n"
              "  1. https://aistudio.google.com/apikey\n"
              f"  2. echo 'GEMINI_API_KEY=<key>' >> {secret_search_path()[0]}\n")
    return 0 if usable else 1

def verify_all(cfg):
    """Re-probe every provider reached over a non-default endpoint."""
    targets = [p for p in provider_chain(cfg, "auto") if needs_verification(p)]
    if not targets:
        print("\nNo redirected providers to verify. Stock cloud endpoints are "
              "trusted; only a custom base_url or an autodetected local router "
              "gets probed.\n")
        return 0

    with open(os.path.join(FIXTURES_DIR, "probe.json"), encoding="utf-8") as f:
        truth = json.load(f)
    print(f"\nProbing with a {truth['duration_sec']}s clip that says:")
    print(f"  \"{truth['sentence']}\"\n")

    bad = 0
    for p in targets:
        label = f"{p.get('kind')} @ {p.get('base_url')} ({p.get('model')})"
        print(f"  {label}")
        ok, detail = verify_provider(p, cfg, force=True)
        _save_probe(provider_signature(p), ok, detail)
        print(f"    {'PASS' if ok else 'FAIL'}: {detail}\n")
        bad += 0 if ok else 1

    print(f"Results cached in {PROBE_STATE}. Delete that file to re-test.\n")
    return 1 if bad else 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--out", dest="out")
    ap.add_argument("--engine", help="auto | gemini | groq | openai | whispercpp | cpu "
                                     "(cli is accepted as an alias for gemini)")
    ap.add_argument("--lang", choices=["auto", "en", "id"])
    ap.add_argument("--doctor", action="store_true",
                    help="report which providers this machine can use, and how to fix the rest")
    ap.add_argument("--verify-providers", action="store_true",
                    help="transcribe a known clip through every redirected provider "
                         "and disable any whose output does not match it")
    args = ap.parse_args()

    if args.verify_providers:
        sys.exit(verify_all(load_config()))
    if args.doctor:
        sys.exit(doctor(load_config()))
    if not args.inp or not args.out:
        ap.error("--in and --out are required (or use --doctor)")
    if not os.path.isfile(args.inp):
        sys.exit(f"ERROR: input not found: {args.inp}")
    out, note = transcribe(args.inp, args.out, args.engine, args.lang)
    print(f"DONE: {out} ({note})")

if __name__ == "__main__":
    main()
