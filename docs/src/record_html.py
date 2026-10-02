"""Render an animated HTML page to a looping GIF, frame by frame (deterministic, no screen recording).

The page animates with CSS animations/transitions or the Web Animations API. This script pauses
every animation, seeks them all to t = 0, 1/fps, 2/fps ... and screenshots each frame, then
builds a palette GIF with ffmpeg. Same input, same GIF, every time.

Usage:
    python record_html.py page.html out.gif --w 1200 --h 600 --dur 8 --fps 15 [--scale 1] [--colors 128]

Page contract:
    - Fixed-size body (width/height = --w/--h CSS px).
    - Every animation lives on the document timeline and has a finite duration. Use
      `animation-iteration-count: 1` + `animation-fill-mode: both` and choreograph with
      `animation-delay`, so the whole piece runs once in --dur seconds and the GIF loops it.
    - Optional: window.__seek(ms) if the page drives something by JS (called every frame
      after the CSS seek, before the screenshot).
    - Fonts: Archivo + JetBrains Mono from Google Fonts are fine; the script waits for fonts.

Needs: pip install playwright && playwright install chromium; ffmpeg on PATH.
"""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

SEEK = """(ms) => {
  for (const a of document.getAnimations()) { a.pause(); a.currentTime = ms; }
  if (window.__seek) window.__seek(ms);
}"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('html')
    ap.add_argument('out')
    ap.add_argument('--w', type=int, default=1200)
    ap.add_argument('--h', type=int, default=600)
    ap.add_argument('--dur', type=float, default=8.0, help='seconds')
    ap.add_argument('--fps', type=int, default=15)
    ap.add_argument('--scale', type=float, default=1.0, help='device scale factor (2 = retina, bigger file)')
    ap.add_argument('--colors', type=int, default=128)
    ap.add_argument('--hold', type=float, default=0.0, help='extra seconds to hold the last frame')
    a = ap.parse_args()

    src = Path(a.html).resolve()
    out = Path(a.out).resolve()
    work = Path(tempfile.mkdtemp(prefix='rec-'))
    n = int(round(a.dur * a.fps))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': a.w, 'height': a.h}, device_scale_factor=a.scale)
        pg.goto(src.as_uri())
        pg.wait_for_load_state('networkidle')
        pg.evaluate('document.fonts.ready')
        pg.wait_for_timeout(300)
        for i in range(n):
            pg.evaluate(SEEK, i * 1000.0 / a.fps)
            pg.screenshot(path=str(work / f'f{i:05d}.png'))
        b.close()

    w = int(a.w * a.scale)
    hold = f',tpad=stop_mode=clone:stop_duration={a.hold}' if a.hold else ''
    fc = (f'[0:v]fps={a.fps},scale={w}:-1:flags=lanczos{hold},split[x][y];'
          f'[x]palettegen=max_colors={a.colors}:stats_mode=full[pl];'
          '[y][pl]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(a.fps),
                    '-i', str(work / 'f%05d.png'), '-filter_complex', fc, '-loop', '0', str(out)], check=True)
    shutil.rmtree(work, ignore_errors=True)
    print('wrote', out, round(out.stat().st_size / 1e6, 2), 'MB,', n, 'frames')

if __name__ == '__main__':
    main()
