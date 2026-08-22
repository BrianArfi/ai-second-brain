#!/usr/bin/env python3
"""Can we record audio from a PulseAudio server on this host?

Every in-browser capture route meetbot tried on 6 Aug 2026 died on the same
wall: Chrome asks its audio service for a device and there is none, so
getDisplayMedia gives NotReadableError and tabCapture gives NotFoundError. The
loopback plan sidesteps the browser entirely -- Chrome plays into a sink, we
record that sink's monitor -- but it only works if a Pulse server is actually
reachable AND lets us record.

There is no pactl/parec on this box and no passwordless sudo to install one, so
this talks to libpulse.so.0 directly through ctypes. It uses the introspection
API (not just pa_simple) because the sink/source NAMES are the thing we need:
the monitor source of whichever sink Chrome ends up playing into is what the
recorder has to open, and guessing it is how this kind of work goes wrong.

Usage:
    python3 tools/pulse_probe.py            # list sinks/sources
    python3 tools/pulse_probe.py <source>   # also record 2s and report peak
"""

import ctypes
import struct
import sys

PA_CONTEXT_READY = 4
PA_CONTEXT_FAILED = 5
PA_CONTEXT_TERMINATED = 6
PA_STREAM_RECORD = 2
PA_SAMPLE_S16LE = 3

pa = ctypes.CDLL("libpulse.so.0")
pa_simple = ctypes.CDLL("libpulse-simple.so.0")

class SampleSpec(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_int),
        ("rate", ctypes.c_uint32),
        ("channels", ctypes.c_uint8),
    ]

class SinkInfo(ctypes.Structure):
    # Only the leading fields are declared: name and index are all we need, and
    # the rest of the struct differs across libpulse versions. Reading past what
    # is declared here is undefined, so do not add fields without checking the
    # header for the installed version.
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("index", ctypes.c_uint32),
        ("description", ctypes.c_char_p),
    ]

class SourceInfo(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("index", ctypes.c_uint32),
        ("description", ctypes.c_char_p),
    ]

SINK_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(SinkInfo),
                           ctypes.c_int, ctypes.c_void_p)
SOURCE_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(SourceInfo),
                             ctypes.c_int, ctypes.c_void_p)

pa.pa_strerror.restype = ctypes.c_char_p
pa.pa_mainloop_new.restype = ctypes.c_void_p
pa.pa_mainloop_get_api.restype = ctypes.c_void_p
pa.pa_mainloop_get_api.argtypes = [ctypes.c_void_p]
pa.pa_context_new.restype = ctypes.c_void_p
pa.pa_context_new.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
pa.pa_context_connect.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                  ctypes.c_int, ctypes.c_void_p]
pa.pa_context_get_state.argtypes = [ctypes.c_void_p]
pa.pa_mainloop_iterate.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                   ctypes.POINTER(ctypes.c_int)]
pa.pa_context_get_sink_info_list.restype = ctypes.c_void_p
pa.pa_context_get_sink_info_list.argtypes = [ctypes.c_void_p, SINK_CB, ctypes.c_void_p]
pa.pa_context_get_source_info_list.restype = ctypes.c_void_p
pa.pa_context_get_source_info_list.argtypes = [ctypes.c_void_p, SOURCE_CB, ctypes.c_void_p]

pa_simple.pa_simple_new.restype = ctypes.c_void_p
pa_simple.pa_simple_new.argtypes = [
    ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
    ctypes.c_char_p, ctypes.POINTER(SampleSpec), ctypes.c_void_p,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
]
pa_simple.pa_simple_read.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_size_t, ctypes.POINTER(ctypes.c_int)]
pa_simple.pa_simple_free.argtypes = [ctypes.c_void_p]
pa_simple.pa_simple_write.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_int)]
pa_simple.pa_simple_drain.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]

def enumerate_devices():
    """Returns (sinks, sources) as lists of (name, description)."""
    loop = pa.pa_mainloop_new()
    api = pa.pa_mainloop_get_api(loop)
    ctx = pa.pa_context_new(api, b"meetbot-pulse-probe")
    if pa.pa_context_connect(ctx, None, 0, None) < 0:
        raise SystemExit("pa_context_connect failed outright")

    ret = ctypes.c_int(0)
    for _ in range(500):
        state = pa.pa_context_get_state(ctx)
        if state == PA_CONTEXT_READY:
            break
        if state in (PA_CONTEXT_FAILED, PA_CONTEXT_TERMINATED):
            raise SystemExit(f"context failed (state={state}); no reachable Pulse server")
        pa.pa_mainloop_iterate(loop, 1, ctypes.byref(ret))
    else:
        raise SystemExit("context never became ready")

    sinks, sources = [], []
    done = {"sink": False, "source": False}

    def on_sink(_c, info, eol, _u):
        if eol:
            done["sink"] = True
            return
        sinks.append((info[0].name.decode(), (info[0].description or b"").decode()))

    def on_source(_c, info, eol, _u):
        if eol:
            done["source"] = True
            return
        sources.append((info[0].name.decode(), (info[0].description or b"").decode()))

    cb_sink = SINK_CB(on_sink)
    cb_source = SOURCE_CB(on_source)
    pa.pa_context_get_sink_info_list(ctx, cb_sink, None)
    pa.pa_context_get_source_info_list(ctx, cb_source, None)
    for _ in range(500):
        if done["sink"] and done["source"]:
            break
        pa.pa_mainloop_iterate(loop, 1, ctypes.byref(ret))
    return sinks, sources

def record(source, seconds=2.0, rate=16000):
    """Records mono s16 from `source` and returns (bytes_read, peak 0..1)."""
    spec = SampleSpec(format=PA_SAMPLE_S16LE, rate=rate, channels=1)
    err = ctypes.c_int(0)
    s = pa_simple.pa_simple_new(
        None, b"meetbot-pulse-probe", PA_STREAM_RECORD,
        source.encode() if source else None,
        b"probe", ctypes.byref(spec), None, None, ctypes.byref(err),
    )
    if not s:
        raise SystemExit(f"pa_simple_new failed: {pa.pa_strerror(err).decode()}")

    total = int(rate * seconds) * 2
    buf = (ctypes.c_char * total)()
    if pa_simple.pa_simple_read(s, buf, total, ctypes.byref(err)) < 0:
        pa_simple.pa_simple_free(s)
        raise SystemExit(f"pa_simple_read failed: {pa.pa_strerror(err).decode()}")
    pa_simple.pa_simple_free(s)

    raw = bytes(buf)
    peak = 0
    for (v,) in struct.iter_unpack("<h", raw):
        a = abs(v)
        if a > peak:
            peak = a
    return len(raw), peak / 32768.0

def play(sink, seconds=3.0, rate=16000, freq=440.0, gain=0.4):
    """Plays a tone into `sink`. The control for a monitor recording.

    Without this, a silent monitor is ambiguous: it could mean the app under
    test is not producing audio, or that recording the monitor does not work at
    all. Running a known tone through the same server settles which.
    """
    import math

    spec = SampleSpec(format=PA_SAMPLE_S16LE, rate=rate, channels=1)
    err = ctypes.c_int(0)
    s = pa_simple.pa_simple_new(
        None, b"meetbot-pulse-probe-play", 1,  # PA_STREAM_PLAYBACK
        sink.encode() if sink else None,
        b"tone", ctypes.byref(spec), None, None, ctypes.byref(err),
    )
    if not s:
        raise SystemExit(f"pa_simple_new(playback) failed: {pa.pa_strerror(err).decode()}")
    n = int(rate * seconds)
    samples = bytearray()
    for i in range(n):
        v = int(gain * 32767 * math.sin(2 * math.pi * freq * i / rate))
        samples += struct.pack("<h", v)
    buf = (ctypes.c_char * len(samples)).from_buffer_copy(bytes(samples))
    if pa_simple.pa_simple_write(s, buf, len(samples), ctypes.byref(err)) < 0:
        pa_simple.pa_simple_free(s)
        raise SystemExit(f"pa_simple_write failed: {pa.pa_strerror(err).decode()}")
    pa_simple.pa_simple_drain(s, ctypes.byref(err))
    pa_simple.pa_simple_free(s)
    return n

if __name__ == "__main__":
    sinks, sources = enumerate_devices()
    print("SINKS:")
    for name, desc in sinks:
        print(f"  {name}   [{desc}]")
    print("SOURCES:")
    for name, desc in sources:
        print(f"  {name}   [{desc}]")
    if len(sys.argv) > 2 and sys.argv[1] == "play":
        print(f"\nplaying tone into {sys.argv[2]!r}")
        play(sys.argv[2])
        raise SystemExit(0)
    if len(sys.argv) > 1:
        n, peak = record(sys.argv[1])
        print(f"\nrecorded {n} bytes from {sys.argv[1]!r}, peak={peak:.4f}")
