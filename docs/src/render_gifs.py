#!/usr/bin/env python3
"""Render the README's explainer GIFs from their HTML sources, in one command.

Usage:  python docs/src/render_gifs.py            # all three
        python docs/src/render_gifs.py hero       # just one: hero | before-after | how-it-works
Needs:  pip install playwright && python -m playwright install chromium; ffmpeg on PATH.
Fonts come from Google Fonts, so render online for the intended look.

The real dashboard recording is separate: python docs/src/record_inbox_demo.py
The static hero still (docs/hero.png) comes from docs/src/render.py.
"""
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
DOCS = SRC.parent

# name: (html, gif, width, height, seconds)
JOBS = {
    'hero': ('hero-anim.html', 'hero.gif', 1600, 800, 9),
    'before-after': ('before-after.html', 'before-after.gif', 1200, 680, 12),
    'how-it-works': ('how-it-works.html', 'how-it-works.gif', 1200, 660, 12),
}

def main():
    names = sys.argv[1:] or list(JOBS)
    for name in names:
        html, gif, w, h, dur = JOBS[name]
        subprocess.run([sys.executable, str(SRC / 'record_html.py'), str(SRC / html), str(DOCS / gif),
                        '--w', str(w), '--h', str(h), '--dur', str(dur), '--fps', '12',
                        '--colors', '128'], check=True)

if __name__ == '__main__':
    main()
