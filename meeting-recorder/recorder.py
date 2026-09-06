#!/usr/bin/env python3
"""Cross-platform meeting recorder (capture side of the local note-taker).

Records system audio (what you hear: the meeting) + microphone (you), writes WAV
into <recordings_dir> from config.json, plus a sidecar .json with metadata.
While recording, a <base>.recording marker file exists; the watcher only picks up
recordings whose marker is gone (clean stop).

Per platform:
  windows  Run with WINDOWS Python (not WSL): pip install pyaudiowpatch
           Captures WASAPI loopback (system) and mic as TWO wav parts
           (<base>.sys.wav + <base>.mic.wav); the WSL watcher mixes them.
  macos    ffmpeg avfoundation, one device (use a BlackHole aggregate device to
           get system+mic in one stream). recorder.py --list-devices to find it.
  linux    ffmpeg PulseAudio: default mic + default sink .monitor, mixed live.

Usage:
  python recorder.py "Work Growth Weekly"        # record until Ctrl-C
  python recorder.py --list-devices
  python recorder.py "title" --mic-only           # skip system audio
"""
import argparse
import datetime
import json
import os
import signal
import subprocess
import sys
import threading
import time
import wave

from common import detect_platform, load_config, slugify

CHUNK = 1024

def now_stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d_%H%M")

def write_sidecar(base, title, start, parts, plat, ad_hoc=False, attendees=None):
    """ad_hoc=True marks a meeting you started yourself (a huddle, a phone call,
    anything not on the calendar). The watcher then skips the calendar match
    entirely, so the recording cannot inherit the title, attendees, or dedupe
    key of whatever calendar event happened to overlap it."""
    end = datetime.datetime.now(datetime.timezone.utc)
    meta = {
        "title": title,
        "start_utc": start.isoformat(timespec="seconds"),
        "end_utc": end.isoformat(timespec="seconds"),
        "duration_sec": int((end - start).total_seconds()),
        "platform": plat,
        "ad_hoc": bool(ad_hoc),
        "attendees": [a.strip() for a in (attendees or []) if a.strip()],
        "parts": [os.path.basename(p) for p in parts],
    }
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"\nSaved: {', '.join(parts)}\nMeta:  {base}.json "
          f"({meta['duration_sec']}s)")

# ---------- windows (pyaudiowpatch, WASAPI loopback) ----------

class WindowsCapture:
    """WASAPI capture (system loopback + mic) usable from CLI and GUI.

    start() opens the streams (audio flows via callbacks), stop() closes them
    and returns the list of written wav parts.
    """

    def __init__(self, base, mic_only=False, system_only=False):
        import pyaudiowpatch as pyaudio
        self.pyaudio = pyaudio
        self.base = base
        self.mic_only = mic_only
        self.system_only = system_only
        self.p = pyaudio.PyAudio()
        self.streams, self.wavs, self.parts = [], [], []
        self.devices = []

    def _open_stream(self, dev, path, channels, rate):
        wf = wave.open(path, "wb")
        wf.setnchannels(channels)
        wf.setsampwidth(self.p.get_sample_size(self.pyaudio.paInt16))
        wf.setframerate(rate)

        def cb(in_data, frame_count, time_info, status):
            wf.writeframes(in_data)
            return (None, self.pyaudio.paContinue)

        st = self.p.open(format=self.pyaudio.paInt16, channels=channels,
                         rate=rate, input=True, input_device_index=dev["index"],
                         frames_per_buffer=CHUNK, stream_callback=cb)
        self.streams.append(st)
        self.wavs.append(wf)
        self.parts.append(path)

    def start(self):
        if not self.mic_only:
            lb = self.p.get_default_wasapi_loopback()
            self.devices.append(f"System: {lb['name']}")
            self._open_stream(lb, self.base + ".sys.wav",
                              int(lb["maxInputChannels"]),
                              int(lb["defaultSampleRate"]))
        if not self.system_only:
            mic = self.p.get_default_input_device_info()
            self.devices.append(f"Mic: {mic['name']}")
            self._open_stream(mic, self.base + ".mic.wav", 1,
                              int(mic["defaultSampleRate"]))
        return self.devices

    def stop(self):
        for st in self.streams:
            st.stop_stream()
            st.close()
        for wf in self.wavs:
            wf.close()
        self.p.terminate()
        return self.parts

def record_windows(base, title, start, mic_only, system_only):
    try:
        cap = WindowsCapture(base, mic_only, system_only)
    except ImportError:
        sys.exit("ERROR: pip install pyaudiowpatch (run with Windows Python, not WSL)")

    for d in cap.start():
        print(d)
    stop = threading.Event()
    print("Recording... Ctrl-C to stop.")
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    # Second way to ask for a clean stop: a <base>.stop file appearing next to the marker.
    #
    # SIGINT is fine from a terminal, but a GUI that spawned this cannot send one on Windows,
    # where a detached child has no console to receive Ctrl-C. Killing the process instead would
    # skip the `finally` below, leaving the .recording marker in place -- and watcher.py ignores
    # any recording whose marker still exists, so the audio would sit on disk forever looking
    # like it never happened. The sentinel gives every caller a clean stop on every platform.
    stop_file = base + ".stop"
    try:
        while not stop.is_set():
            stop.wait(1)
            if os.path.exists(stop_file):
                stop.set()
            elapsed = (datetime.datetime.now(datetime.timezone.utc) - start).seconds
            print(f"\r  {elapsed // 60:02d}:{elapsed % 60:02d}", end="", flush=True)
    finally:
        if os.path.exists(stop_file):
            os.remove(stop_file)
        return cap.stop()

# ---------- optional screen recording (video sidecar) ----------

class ScreenRecorder:
    """ffmpeg screen capture -> <base>.mp4 sidecar (Windows gdigrab).
    Video is reference material only; audio stays the transcript source.
    The watcher registers the .mp4 path on the registry entry."""

    def __init__(self, base, ffmpeg="ffmpeg"):
        self.out = base + ".mp4"
        self.ffmpeg = ffmpeg
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(
            [self.ffmpeg, "-hide_banner", "-loglevel", "error",
             "-f", "gdigrab", "-framerate", "15", "-i", "desktop",
             "-vcodec", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", "-y", self.out],
            stdin=subprocess.PIPE,
            creationflags=0x08000000 if os.name == "nt" else 0)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
                self.proc.wait(timeout=20)
            except Exception:
                self.proc.terminate()
        return self.out if os.path.exists(self.out) else None

# ---------- macos / linux (ffmpeg) ----------

def record_ffmpeg(base, plat, machine, mic_only, system_only):
    ffmpeg = machine.get("ffmpeg", "ffmpeg")
    out = base + ".wav"
    if plat == "macos":
        dev = machine.get("avfoundation_audio_device", ":0")
        cmd = [ffmpeg, "-hide_banner", "-f", "avfoundation", "-i", dev,
               "-ac", "1", "-ar", "48000", out]
    else:  # linux
        mon = subprocess.run(["bash", "-c",
                              "pactl get-default-sink 2>/dev/null"],
                             capture_output=True, text=True).stdout.strip()
        inputs = []
        if not mic_only and mon:
            inputs += ["-f", "pulse", "-i", mon + ".monitor"]
        if not system_only:
            inputs += ["-f", "pulse", "-i", "default"]
        if not inputs:
            sys.exit("ERROR: nothing to record (no default sink found?)")
        n = len(inputs) // 3
        cmd = [ffmpeg, "-hide_banner"] + inputs
        if n > 1:
            cmd += ["-filter_complex", f"amix=inputs={n}:duration=longest"]
        cmd += ["-ac", "1", "-ar", "48000", out]

    print("Recording... Ctrl-C or 'q' to stop.")
    proc = subprocess.Popen(cmd)
    # Same two stop paths as the Windows capture: Ctrl-C, or a <base>.stop sentinel appearing.
    # Poll rather than plain wait() so a GUI caller that cannot deliver SIGINT still gets ffmpeg
    # shut down through its own SIGINT, which is what makes it flush a playable file.
    stop_file = base + ".stop"
    try:
        while proc.poll() is None:
            if os.path.exists(stop_file):
                proc.send_signal(signal.SIGINT)
                break
            time.sleep(1)
        proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        proc.wait()
    finally:
        if os.path.exists(stop_file):
            os.remove(stop_file)
    if not os.path.exists(out):
        sys.exit("ERROR: ffmpeg produced no output")
    return [out]

def list_devices(plat, machine):
    if plat == "windows":
        import pyaudiowpatch as pyaudio
        p = pyaudio.PyAudio()
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["maxInputChannels"] > 0:
                print(f"[{i}] {d['name']} ({int(d['defaultSampleRate'])} Hz)")
        p.terminate()
    elif plat == "macos":
        subprocess.run([machine.get("ffmpeg", "ffmpeg"), "-f", "avfoundation",
                        "-list_devices", "true", "-i", ""])
    else:
        subprocess.run(["bash", "-c", "pactl list short sources"])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("title", nargs="?", default="meeting")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--mic-only", action="store_true")
    ap.add_argument("--system-only", action="store_true")
    ap.add_argument("--video", action="store_true",
                    help="also screen-record to <base>.mp4 (Windows, needs ffmpeg)")
    ap.add_argument("--ad-hoc", action="store_true",
                    help="meeting not on the calendar (Slack huddle, phone call). "
                         "Skips calendar matching so the title you type is the title "
                         "that sticks")
    ap.add_argument("--attendees", default="",
                    help="comma-separated names, used to resolve Speaker 1/2 labels "
                         "in the MOM draft")
    args = ap.parse_args()

    cfg = load_config()
    plat, machine = cfg["platform"], cfg["machine"]
    if args.list_devices:
        return list_devices(plat, machine)
    if plat == "wsl":
        sys.exit("ERROR: WSL cannot capture Windows audio. Run recorder.py with "
                 "WINDOWS Python (see README), or use the watcher here for processing.")

    rec_dir = machine["recordings_dir"]
    os.makedirs(rec_dir, exist_ok=True)
    base = os.path.join(rec_dir, f"{now_stamp()}_{slugify(args.title)}")
    marker = base + ".recording"
    open(marker, "w").close()
    start = datetime.datetime.now(datetime.timezone.utc)
    screen = None
    if args.video:
        if plat != "windows":
            print("WARNING: --video is Windows-only (gdigrab); recording audio only.")
        else:
            screen = ScreenRecorder(base, machine.get("ffmpeg", "ffmpeg"))
            try:
                screen.start()
                print("Screen recording -> " + screen.out)
            except FileNotFoundError:
                print("WARNING: ffmpeg not found; recording audio only.")
                screen = None
    try:
        if plat == "windows":
            parts = record_windows(base, args.title, start,
                                   args.mic_only, args.system_only)
        else:
            parts = record_ffmpeg(base, plat, machine,
                                  args.mic_only, args.system_only)
        if screen:
            video = screen.stop()
            if video:
                parts = list(parts) + [video]
        write_sidecar(base, args.title, start, parts, plat,
                      ad_hoc=args.ad_hoc,
                      attendees=args.attendees.split(","))
    finally:
        if os.path.exists(marker):
            os.remove(marker)

if __name__ == "__main__":
    main()
