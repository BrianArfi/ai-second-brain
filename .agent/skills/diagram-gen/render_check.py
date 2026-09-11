#!/usr/bin/env python3
"""
render_check.py - validate a Mermaid diagram renders, and save a preview PNG.

Uses the same renderer stack as scripts/embed_mermaid_in_gdoc.py (kroki.io, then
mermaid.ink), so a pass here means the diagram will render when it is embedded
into a Google Doc.

Usage:
  python3 render_check.py --file diagram.mmd [--out /tmp/diagram_preview.png]
  python3 render_check.py --text "flowchart TB; A-->B" [--out ...]

Exit codes:
  0  valid, PNG saved
  1  the renderer read the source and rejected it: fix the Mermaid
  3  every renderer is down or unreachable: the diagram is NOT the problem
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from mermaid_render import MermaidSyntaxError, RendererUnavailable, render_png
except ImportError as exc:
    print(f"ERROR: {exc} (pip install requests)", file=sys.stderr)
    sys.exit(2)

def main() -> int:
    ap = argparse.ArgumentParser(description="Validate + preview a Mermaid diagram")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="path to a .mmd / Mermaid source file")
    g.add_argument("--text", help="inline Mermaid source")
    ap.add_argument("--out", default="/tmp/diagram_preview.png", help="where to save the preview PNG")
    args = ap.parse_args()

    src = open(args.file, encoding="utf-8").read() if args.file else args.text
    src = src.strip()
    if not src:
        print("ERROR: empty Mermaid source", file=sys.stderr)
        return 2

    try:
        content, renderer = render_png(src)
    except MermaidSyntaxError as exc:
        print("INVALID Mermaid - the renderer read it and rejected it:", file=sys.stderr)
        print(str(exc)[:1500], file=sys.stderr)
        return 1
    except RendererUnavailable as exc:
        print("RENDERER DOWN - this is not a syntax error. Do not edit the diagram.", file=sys.stderr)
        print(str(exc)[:1500], file=sys.stderr)
        return 3

    with open(args.out, "wb") as f:
        f.write(content)
    print(f"OK - renders cleanly via {renderer}. Preview saved: {args.out} ({len(content)} bytes)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
