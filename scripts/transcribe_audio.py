#!/usr/bin/env python3
"""Transcribe a local audio file with faster-whisper (CPU, no API key).

Reusable wrapper for the owner's Windows "Sound Recordings" m4a/wav/mp3 files.
Run with the dedicated venv: ~/.venvs/whisper/bin/python

Usage:
  ~/.venvs/whisper/bin/python scripts/transcribe_audio.py \
      --in "/mnt/c/Users/you/Documents/Sound Recordings/<file>.m4a" \
      --out scratch/<name>.md \
      [--model small|medium|large-v3] [--lang en|id|auto]

Outputs a markdown transcript with [mm:ss] timestamps. Also writes a .txt
plain transcript alongside the .md.
"""
import argparse
import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count() or 4))

def fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default="en", help="en|id|auto (auto = detect)")
    ap.add_argument("--beam", type=int, default=5)
    args = ap.parse_args()

    if not os.path.isfile(args.inp):
        sys.exit(f"ERROR: input not found: {args.inp}")

    from faster_whisper import WhisperModel

    lang = None if args.lang == "auto" else args.lang
    print(f"[{time.strftime('%H:%M:%S')}] loading model '{args.model}' (cpu/int8)...", flush=True)
    model = WhisperModel(args.model, device="cpu", compute_type="int8")

    print(f"[{time.strftime('%H:%M:%S')}] transcribing {args.inp} ...", flush=True)
    t0 = time.time()
    segments, info = model.transcribe(
        args.inp,
        language=lang,
        beam_size=args.beam,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        condition_on_previous_text=True,
    )
    print(f"[{time.strftime('%H:%M:%S')}] detected language: {info.language} "
          f"(p={info.language_probability:.2f}), duration={fmt_ts(info.duration)}", flush=True)

    md_lines, txt_lines = [], []
    for seg in segments:
        text = seg.text.strip()
        md_lines.append(f"**[{fmt_ts(seg.start)}]** {text}")
        txt_lines.append(text)
        # progress heartbeat to stderr
        print(f"  [{fmt_ts(seg.start)}] {text[:80]}", file=sys.stderr, flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    header = (f"# Transcript: {os.path.basename(args.inp)}\n\n"
              f"- Model: faster-whisper `{args.model}` (cpu/int8, beam={args.beam}, VAD on)\n"
              f"- Detected language: {info.language} (p={info.language_probability:.2f})\n"
              f"- Audio duration: {fmt_ts(info.duration)}\n\n---\n\n")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(header + "\n\n".join(md_lines) + "\n")
    txt_out = os.path.splitext(args.out)[0] + ".txt"
    with open(txt_out, "w", encoding="utf-8") as f:
        f.write(" ".join(txt_lines) + "\n")

    dt = time.time() - t0
    print(f"[{time.strftime('%H:%M:%S')}] DONE in {dt/60:.1f} min -> {args.out} (+ {txt_out})", flush=True)

if __name__ == "__main__":
    main()
