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
  macos    ffmpeg avfoundation, one device. The app ships `asb-systemaudio`, which
           builds a Core Audio tap + mic aggregate for the length of the capture
           (macOS 14.2+, no driver). A BlackHole aggregate set in
           `avfoundation_audio_device` still wins when configured.
  linux    ffmpeg PulseAudio: default mic + default sink .monitor, mixed live.

Usage:
  python recorder.py "Work Growth Weekly"        # record until Ctrl-C
  python recorder.py --list-devices
  python recorder.py "title" --mic-only           # skip system audio
  python recorder.py "title" --video              # + screen video (Windows)
  python recorder.py --list-windows               # titles --video-window takes
  python recorder.py "title" --video-window "Zoom Meeting"
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

def warn(msg):
    """A degraded recording, said where the caller can hear it.

    stderr, not stdout: a GUI caller (the ASB app) sends stdout to nowhere and
    keeps stderr, so a warning printed the other way is a warning nobody ever
    sees. Which is how a recording could lose its video and still look fine."""
    print("WARNING: " + msg, file=sys.stderr, flush=True)

def write_sidecar(base, title, start, parts, plat, ad_hoc=False, attendees=None,
                  audio_route=None):
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
    if audio_route:
        # "mic_only" here means the file holds one voice, not the meeting. Kept
        # on the recording itself so a write-up can say so instead of presenting
        # half a conversation as the whole one.
        meta["audio_route"] = audio_route
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

def list_windows(exclude_pids=()):
    """Top-level windows gdigrab's `title=` can actually open, front to back.

    EnumWindows walks the Z-order, so the first entry is the window in front.
    That ordering is what makes `active_window()` below a one-liner, and it is
    also the most useful order for a picker.

    ctypes rather than pywin32: the recorder's only hard Windows dependency is
    pyaudiowpatch, and a window list is not worth a second one.

    Cloaked windows are dropped. A UWP app that is closed to the user still
    leaves a visible, titled window behind (Settings and Mail both do), and
    offering one is offering a capture of a black rectangle.

    `exclude_pids` drops windows belonging to those processes. The caller that
    needs it is the one that asked: press Record in an app and that app is now
    the window in front, so without this "record what I am looking at" means
    "record the button you just pressed"."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwm = ctypes.windll.dwmapi
    titles = []
    seen = set()
    skip_pids = {int(p) for p in exclude_pids} | {os.getpid()}
    DWMWA_CLOAKED = 14
    GWL_EXSTYLE = -20
    GW_OWNER = 4
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_APPWINDOW = 0x00040000
    # Smaller than this is a notification toast, a splash, or a sliver, never
    # the thing somebody meant by "the window I am working in".
    MIN_W, MIN_H = 320, 200
    # The desktop's own shell windows are top-level and titled, but capturing
    # one gets you the wallpaper.
    SKIP = {"Program Manager", "Windows Input Experience", "Setup"}

    def cloaked(hwnd):
        val = ctypes.c_int(0)
        hr = dwm.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_int(DWMWA_CLOAKED),
            ctypes.byref(val), ctypes.sizeof(val))
        return hr == 0 and val.value != 0

    def owner_pid(hwnd):
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def alt_tabbable(hwnd):
        """The window would appear in Alt-Tab.

        Visible and titled is not enough, and that is not a detail: a WhatsApp
        helper called "Status" sits at the top of the Z-order on this machine,
        so "the window in front" resolved to a capture of nothing. The rule
        below is the one the shell itself uses -- an owned window is a dialog
        or a popup belonging to something else, and a tool window is chrome."""
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex & WS_EX_TOOLWINDOW:
            return False
        if user32.GetWindow(hwnd, GW_OWNER) and not (ex & WS_EX_APPWINDOW):
            return False
        r = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return False
        return (r.right - r.left) >= MIN_W and (r.bottom - r.top) >= MIN_H

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        t = buf.value.strip()
        if not t or t in SKIP or t in seen or cloaked(hwnd):
            return True
        if owner_pid(hwnd) in skip_pids or not alt_tabbable(hwnd):
            return True
        seen.add(t)
        titles.append(t)
        return True

    proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(proc, 0)
    return titles

def active_window(exclude_pids=()):
    """The window the user was last working in, or None.

    Not GetForegroundWindow: by the time this runs, the foreground window is
    whatever holds the Record button. The first capturable window that is not
    one of `exclude_pids` is the one they were actually looking at."""
    windows = list_windows(exclude_pids)
    return windows[0] if windows else None

class ScreenRecorder:
    """ffmpeg screen capture -> <base>.mp4 sidecar (Windows gdigrab).
    Video is reference material only; audio stays the transcript source.
    The watcher registers the .mp4 path on the registry entry.

    `window` picks one window instead of the whole desktop. That is a privacy
    control as much as a framing one: a full-desktop capture of a call also
    captures every notification, inbox and tab that crosses the screen.

    KNOWN LIMIT, and it decides how this is offered: gdigrab copies the
    window's RECTANGLE OF THE SCREEN, not the window's own back buffer. So a
    window that is covered records whatever covers it, and a minimised one
    records black. Measured on four windows: only the one actually visible
    produced anything but black frames. That is why the default is the window
    the user was last in, which is by definition the visible one, and why the
    UI says to keep it in view. True occluded per-window capture needs the
    Windows Graphics Capture API, which ffmpeg has no input for."""

    def __init__(self, base, ffmpeg="ffmpeg", window=None):
        self.out = base + ".mp4"
        self.ffmpeg = ffmpeg
        self.window = (window or "").strip() or None
        self.proc = None

    def _codec(self):
        """x264 where the machine's ffmpeg has it, mpeg4 otherwise.

        The ffmpeg the ASB app bundles is built LGPL, because shipping a GPL
        binary inside the app would put the app under the GPL, and x264 is what
        every convenient build turns GPL for. So the bundled one carries mpeg4:
        a bigger file at the same quality, which is the right trade for video
        nobody transcodes. A user with a full ffmpeg on PATH still gets x264.

        Asked of the binary rather than assumed from the platform, because
        which ffmpeg answers `self.ffmpeg` depends on PATH order."""
        try:
            out = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=20,
                creationflags=0x08000000 if os.name == "nt" else 0).stdout
        except Exception:
            out = ""
        if " libx264 " in out:
            return ["-vcodec", "libx264", "-preset", "ultrafast"]
        return ["-vcodec", "mpeg4", "-q:v", "5"]

    def start(self):
        source = f"title={self.window}" if self.window else "desktop"
        self.proc = subprocess.Popen(
            [self.ffmpeg, "-hide_banner", "-loglevel", "error",
             # 5, not 15. Measured on a two-monitor desktop: 15 fps writes
             # about 7.7 GB an hour, 5 fps about a third of that, and this is
             # reference video of people talking over a shared screen. Nobody
             # is studying the mouse cursor's easing curve.
             "-f", "gdigrab", "-framerate", "5", "-i", source,
             # A window is whatever size the user left it, and yuv420p refuses
             # an odd width or height. Cropping to the nearest even pair costs
             # at most one row of pixels and is the difference between a video
             # and an immediate exit.
             "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2"]
            + self._codec() +
            ["-pix_fmt", "yuv420p", "-y", self.out],
            stdin=subprocess.PIPE,
            creationflags=0x08000000 if os.name == "nt" else 0)
        # gdigrab fails at open time when the title does not match a live
        # window, and `-loglevel error` means it fails quietly. Without this
        # the audio records fine, nobody is told, and the missing video is
        # discovered when someone goes looking for it.
        time.sleep(1.5)
        if self.proc.poll() is not None:
            self.proc = None
            return False
        return True

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

# How long `asb-systemaudio hold` gets to build its device. Creating a tap and an aggregate is a
# few hundred milliseconds; the first run can also sit behind the audio-capture permission prompt.
HELPER_READY_TIMEOUT_S = 20

def macos_audio_route(machine, mic_only):
    """Pick what a Mac records from. Returns (device, route, reason, helper).

    `route` is one of:
      configured  the user pointed `avfoundation_audio_device` at something
                  (a BlackHole aggregate, usually). Trusted as is.
      system_tap  the app's Core Audio helper built "ASB Meeting Capture": the
                  meeting plus the microphone.
      mic_only    the microphone and nothing else. `reason` says why.

    `helper` is the running `asb-systemaudio hold` process when route is
    system_tap. It owns the tap, so it must stay alive until ffmpeg stops;
    close_helper() tears the device down.

    Before this existed nothing on this path ever ran the helper. The app
    stopped warning once the helper file was present, and the recording was
    still the microphone alone, so a Mac could look fixed and record one voice.
    """
    configured = str(machine.get("avfoundation_audio_device") or "").strip()
    if configured and configured != ":0":
        return configured, "configured", "", None
    if mic_only:
        return ":0", "mic_only", "mic_only_requested", None

    helper = os.environ.get("ASB_SYSTEMAUDIO", "").strip()
    if not helper:
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "macos", "asb-systemaudio")
        helper = local if os.path.isfile(local) else ""
    if not helper or not os.path.isfile(helper):
        return ":0", "mic_only", "no_helper", None

    try:
        proc = subprocess.Popen([helper, "hold"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
    except OSError as e:
        return ":0", "mic_only", f"helper_spawn_failed: {e}", None

    import select
    ready, _, _ = select.select([proc.stdout], [], [], HELPER_READY_TIMEOUT_S)
    line = proc.stdout.readline().strip() if ready else ""
    if line.startswith("READY\t"):
        name = line.split("\t")[1]
        return ":" + name, "system_tap", "", proc

    close_helper(proc)
    err = ""
    try:
        err = (proc.stderr.read() or "").strip()
    except Exception:
        pass
    why = err.splitlines()[0] if err else (line or "no answer in time")
    return ":0", "mic_only", f"helper_failed: {why[:200]}", None

def close_helper(proc):
    """Stop `asb-systemaudio hold`. Closing its stdin is the signal; it
    destroys the tap and the aggregate device on the way out."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.stdin.close()
        proc.wait(timeout=5)
    except Exception:
        proc.terminate()

def record_ffmpeg(base, plat, machine, mic_only, system_only, device=None):
    ffmpeg = machine.get("ffmpeg", "ffmpeg")
    out = base + ".wav"
    if plat == "macos":
        dev = device or machine.get("avfoundation_audio_device", ":0")
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
                    help="also record video to <base>.mp4 (Windows, needs "
                         "ffmpeg). Records the window you were last working "
                         "in, not the whole desktop")
    ap.add_argument("--video-window", default="", metavar="TITLE",
                    help="record the window with this exact title instead of "
                         "the active one. Implies --video")
    ap.add_argument("--video-whole-screen", action="store_true",
                    help="record every monitor instead of one window. Costs "
                         "roughly ten times the disk; see README. Implies --video")
    ap.add_argument("--video-exclude-pid", default="", metavar="PIDS",
                    help="comma-separated pids whose windows are not the "
                         "'active' one. Pass the pid of whatever holds the "
                         "Record button, or it records itself")
    ap.add_argument("--list-windows", action="store_true",
                    help="print the window titles --video-window accepts, as JSON")
    ap.add_argument("--ad-hoc", action="store_true",
                    help="meeting not on the calendar (Slack huddle, phone call). "
                         "Skips calendar matching so the title you type is the title "
                         "that sticks")
    ap.add_argument("--attendees", default="",
                    help="comma-separated names, used to resolve Speaker 1/2 labels "
                         "in the MOM draft")
    args = ap.parse_args()

    exclude_pids = [int(p) for p in args.video_exclude_pid.split(",") if p.strip()]

    # Answered before load_config(), so a machine whose recorder config is not
    # written yet can still populate a window picker.
    if args.list_windows:
        print(json.dumps(list_windows(exclude_pids)))
        return

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

    # Decided before the marker exists, and written beside it, so the app can
    # read what this capture actually holds the moment it sees the marker.
    # A Mac that falls back to the microphone is a recording of one person;
    # the app reports that instead of letting it pass as a meeting.
    audio_device, audio_route, route_reason, helper = None, None, "", None
    route_file = base + ".route"
    if plat == "macos":
        audio_device, audio_route, route_reason, helper = macos_audio_route(
            machine, args.mic_only)
        if audio_route == "mic_only" and route_reason != "mic_only_requested":
            warn("recording the microphone only, not the other people in the "
                 f"call ({route_reason}).")
        with open(route_file, "w", encoding="utf-8") as fh:
            json.dump({"route": audio_route, "reason": route_reason}, fh)

    # The marker carries this process's pid, not just its existence.
    #
    # The app decides whether a leftover marker is a live capture or the debris of a crash. Without
    # a pid the only way to ask is to scan the whole process table for "recorder.py", which cannot
    # say WHICH recording an answer belongs to: one live capture then protects every stale marker
    # beside it, and on Windows the scan reports its own failure as "alive", which turns the app's
    # Stop button into a silent no-op. One integer removes all of that.
    #
    # Still just a file whose presence is the contract -- an older app reads the same marker, sees
    # it exists, and behaves exactly as it did before.
    with open(marker, "w") as fh:
        fh.write(str(os.getpid()))
    start = datetime.datetime.now(datetime.timezone.utc)
    screen = None
    want_window = args.video_window.strip()
    if args.video or want_window or args.video_whole_screen:
        # Resolved here, once, so the three video flags produce exactly one
        # answer to "what gets captured" and the failure paths below can all
        # say which one they were refusing.
        #
        # `target` is a window title, or None for the whole desktop. Which is
        # the default matters: the whole desktop on two monitors writes ~7 GB
        # an hour and cannot be encoded in H.264 at all (AVC stops at 4096
        # pixels wide), so one window is the default and everything is the
        # deliberate exception.
        target = None
        refuse = None
        if plat != "windows":
            refuse = "--video is Windows-only (gdigrab)"
        elif args.video_whole_screen:
            target = None
        elif want_window:
            target = want_window
            if want_window not in list_windows(exclude_pids):
                # Falling back to the whole desktop would be the
                # friendlier-looking move and the wrong one: picking one window
                # is often a privacy choice, so capturing everything instead is
                # worse than capturing nothing.
                refuse = f'no open window titled "{want_window}"'
        else:
            target = active_window(exclude_pids)
            if target is None:
                refuse = "no window was open to record"

        if refuse:
            warn(f"{refuse}; recording audio only.")
        else:
            screen = ScreenRecorder(base, machine.get("ffmpeg", "ffmpeg"),
                                    window=target)
            try:
                if screen.start():
                    where = f'window "{target}"' if target else "every monitor"
                    print(f"Recording {where} -> " + screen.out)
                else:
                    warn("ffmpeg could not open the screen capture; recording audio only.")
                    screen = None
            except FileNotFoundError:
                warn("ffmpeg not found; recording audio only.")
                screen = None
    try:
        if plat == "windows":
            parts = record_windows(base, args.title, start,
                                   args.mic_only, args.system_only)
        else:
            parts = record_ffmpeg(base, plat, machine,
                                  args.mic_only, args.system_only,
                                  device=audio_device)
        if screen:
            video = screen.stop()
            if video:
                parts = list(parts) + [video]
        write_sidecar(base, args.title, start, parts, plat,
                      ad_hoc=args.ad_hoc,
                      attendees=args.attendees.split(","),
                      audio_route=audio_route)
    finally:
        close_helper(helper)
        if os.path.exists(route_file):
            os.remove(route_file)
        if os.path.exists(marker):
            os.remove(marker)

if __name__ == "__main__":
    main()
