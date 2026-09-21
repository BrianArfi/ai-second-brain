r"""Toggle dictation for Windows: click once to record, click again to transcribe.

The ASB app's own mic button ties capture to window focus, so clicking into
another window stops it and transcribes early. This does not: recording runs in
its own ffmpeg process and only stops when you stop it, so you can click around
while you talk. The text lands on the clipboard, ready to paste with Ctrl+V.

Toggle from anywhere with Ctrl+Alt+D (a real global hotkey, registered with
Windows), or click the button in the small always-on-top window.

Launch with Windows pythonw (no console window):
  pythonw C:/Users/you/.gemini/antigravity/scratch/product-second-brain/meeting-recorder/dictate_win.pyw

Pieces it uses, all already installed:
  ffmpeg     - the copy the ASB app ships, else whatever is on PATH
  whisper.cpp- machines.windows.whispercpp_bin + voice_model/whispercpp_model
  microphone - machines.windows.voice_input (exact dshow name)
"""
import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk

if sys.stdout is None:                      # pythonw has no console
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config              # noqa: E402

CREATE_NO_WINDOW = 0x08000000
BUNDLED_FFMPEG = os.path.expandvars(
    r"%LOCALAPPDATA%\AI Second Brain\ffmpeg.exe")
HOTKEY_ID = 1
MOD_CONTROL, MOD_ALT, MOD_NOREPEAT, VK_D = 0x0002, 0x0001, 0x4000, 0x44

def machine_config():
    """The windows section of meeting-recorder/config.json, with the paths this
    tool needs resolved. Raises RuntimeError naming the missing key."""
    cfg = load_config()
    win = (cfg.get("machines") or {}).get("windows") or {}
    device = win.get("voice_input")
    if not device:
        raise RuntimeError(
            "No `voice_input` in meeting-recorder/config.json (machines.windows).\n"
            "List the exact device name with:\n"
            "  ffmpeg -list_devices true -f dshow -i dummy")
    whisper = win.get("whispercpp_bin")
    if not whisper or not os.path.exists(whisper):
        raise RuntimeError(f"whisper-cli not found: {whisper!r}")
    # voice_model is the small/fast one when it exists; a spoken prompt is
    # waited for, so loading 1.5 GB of turbo costs more than the accuracy buys.
    model = win.get("voice_model") or win.get("whispercpp_model")
    model = os.path.expandvars(os.path.expanduser(model or ""))
    if not os.path.exists(model):
        raise RuntimeError(f"whisper model not found: {model!r}")
    ffmpeg = win.get("ffmpeg")
    if not ffmpeg:
        ffmpeg = BUNDLED_FFMPEG if os.path.exists(BUNDLED_FFMPEG) else "ffmpeg"
    return {"device": device, "ffmpeg": ffmpeg, "whisper": whisper, "model": model}

def transcribe(cfg, wav):
    """Run whisper.cpp over one wav and return the text it heard."""
    proc = subprocess.run(
        [cfg["whisper"], "-m", cfg["model"], "-f", wav,
         "-l", "auto", "-nt", "-np"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "whisper-cli failed").strip()[-400:])
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return " ".join(lines).strip()

class Dictation(tk.Tk):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.proc = None
        self.wav = None
        self.started_at = None
        self.busy = False

        self.title("Dictate")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.geometry("260x96")
        self.configure(bg="#111111")

        self.button = tk.Button(self, text="\u25cf  Record", width=22,
                                font=("Segoe UI", 11, "bold"),
                                bg="#2d6cdf", fg="white",
                                activebackground="#1f4fa8", activeforeground="white",
                                relief="flat", command=self.toggle)
        self.button.pack(pady=(12, 6))
        self.status = tk.Label(self, text="Ctrl+Alt+D from any window",
                               font=("Segoe UI", 8), bg="#111111", fg="#9aa0a6")
        self.status.pack()

        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.register_hotkey()
        self.tick()

    # --- global hotkey -----------------------------------------------------
    def register_hotkey(self):
        """Ctrl+Alt+D anywhere. Its own thread with its own message loop, so a
        blocked Tk mainloop cannot swallow the keypress."""
        def loop():
            user32 = ctypes.windll.user32
            if not user32.RegisterHotKey(None, HOTKEY_ID,
                                         MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_D):
                self.after(0, lambda: self.status.config(
                    text="Ctrl+Alt+D taken - use the button"))
                return
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:              # WM_HOTKEY
                    self.after(0, self.toggle)
        threading.Thread(target=loop, daemon=True).start()

    # --- recording ---------------------------------------------------------
    def toggle(self):
        if self.busy:
            return
        self.stop() if self.proc else self.start()

    def start(self):
        fd, self.wav = tempfile.mkstemp(prefix="dictate_", suffix=".wav")
        os.close(fd)
        try:
            self.proc = subprocess.Popen(
                [self.cfg["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "dshow", "-i", f"audio={self.cfg['device']}",
                 "-ac", "1", "-ar", "16000", self.wav],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            self.fail(str(exc))
            return
        self.started_at = time.time()
        self.button.config(text="\u25a0  Stop", bg="#c5221f", activebackground="#8c1714")

    def stop(self):
        proc, wav, self.proc = self.proc, self.wav, None
        self.busy = True
        self.button.config(text="transcribing...", bg="#5f6368", state="disabled")
        self.status.config(text="")
        try:
            proc.stdin.write(b"q")             # graceful: ffmpeg closes the wav
            proc.stdin.flush()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        threading.Thread(target=self.finish, args=(wav,), daemon=True).start()

    def finish(self, wav):
        """Transcribe off the UI thread, then hand the text back to it."""
        try:
            if not os.path.exists(wav) or os.path.getsize(wav) < 2000:
                raise RuntimeError("nothing recorded - check the mic name")
            text = transcribe(self.cfg, wav)
            self.after(0, self.deliver, text)
        except Exception as exc:
            self.after(0, self.fail, str(exc))
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass

    # --- results -----------------------------------------------------------
    def deliver(self, text):
        self.busy = False
        self.button.config(text="\u25cf  Record", bg="#2d6cdf",
                           activebackground="#1f4fa8", state="normal")
        if not text:
            self.status.config(text="heard nothing", fg="#f28b82")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()                          # keeps it on the clipboard
        words = len(text.split())
        self.status.config(text=f"copied, {words} words - paste with Ctrl+V",
                           fg="#81c995")

    def fail(self, message):
        self.busy = False
        self.proc = None
        self.button.config(text="\u25cf  Record", bg="#2d6cdf",
                           activebackground="#1f4fa8", state="normal")
        self.status.config(text=message[:60], fg="#f28b82")

    def tick(self):
        if self.proc and self.started_at:
            secs = int(time.time() - self.started_at)
            self.status.config(text=f"recording  {secs // 60}:{secs % 60:02d}",
                               fg="#f28b82")
        self.after(500, self.tick)

    def quit_app(self):
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass
        try:
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
        except OSError:
            pass
        self.destroy()

if __name__ == "__main__":
    try:
        config = machine_config()
    except Exception as err:
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showerror("Dictate", str(err))
        sys.exit(1)
    Dictation(config).mainloop()
