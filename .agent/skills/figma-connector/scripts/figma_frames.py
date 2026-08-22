#!/usr/bin/env python3
"""List and export frames from a Figma file.

Two modes:

    list    walk the document and print every frame, so you can map frames to flows
    export  render chosen node ids to PNG and download them

    python3 figma_frames.py list --file HSmQTNkLKrVoPSuz2WCEyI
    python3 figma_frames.py list --file HSmQTNkLKrVoPSuz2WCEyI --match order
    python3 figma_frames.py export --file HSmQTNkLKrVoPSuz2WCEyI \
        --nodes 123:456,123:789 --out Clients/Work/Seller\\ Portal/figma --scale 2
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from figma_check import load_token  # noqa: E402

# Node types that are worth rendering as a "screen".
FRAME_TYPES = {"FRAME", "COMPONENT", "COMPONENT_SET", "SECTION"}

def call(path, token):
    req = urllib.request.Request(
        "https://api.figma.com/v1" + path, headers={"X-Figma-Token": token}
    )
    try:
        return 200, json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]

def walk(node, depth, path, out, max_depth):
    t = node.get("type")
    if t in FRAME_TYPES:
        bb = node.get("absoluteBoundingBox") or {}
        out.append({
            "id": node["id"],
            "name": node.get("name", ""),
            "type": t,
            "depth": depth,
            "path": path,
            "w": round(bb.get("width") or 0),
            "h": round(bb.get("height") or 0),
        })
    if depth >= max_depth:
        return
    for c in node.get("children", []) or []:
        walk(c, depth + 1, path + [node.get("name", "")], out, max_depth)

def cmd_list(args, token):
    code, data = call(f"/files/{args.file}?depth={args.depth}", token)
    if code != 200:
        print(f"HTTP {code}: {data}")
        return 1
    print(f"FILE: {data.get('name')}  (modified {data.get('lastModified')})\n")
    frames = []
    for page in data["document"].get("children", []):
        walk(page, 0, [], frames, args.depth)

    needle = (args.match or "").lower()
    shown = 0
    for f in frames:
        if needle and needle not in f["name"].lower():
            continue
        # Skip tiny nodes, they are components not screens.
        if f["w"] < args.min_width:
            continue
        indent = "  " * f["depth"]
        loc = " / ".join(x for x in f["path"] if x)
        print(f"{f['id']:>14}  {f['w']:>5}x{f['h']:<5} {indent}{f['name'][:60]}")
        if loc:
            print(f"{'':>14}  {'':>11} {indent}  in: {loc[:70]}")
        shown += 1
    print(f"\n{shown} frames shown of {len(frames)} found (min width {args.min_width})")
    return 0

def cmd_export(args, token):
    os.makedirs(args.out, exist_ok=True)
    ids = [n.strip() for n in args.nodes.split(",") if n.strip()]
    q = urllib.parse.urlencode({
        "ids": ",".join(ids), "format": args.format, "scale": args.scale,
    })
    code, data = call(f"/images/{args.file}?{q}", token)
    if code != 200:
        print(f"HTTP {code}: {data}")
        return 1
    if data.get("err"):
        print("Figma error:", data["err"])
        return 1

    images = data.get("images", {})
    written = 0
    for nid in ids:
        url = images.get(nid)
        if not url:
            print(f"  {nid}: no image returned")
            continue
        safe = nid.replace(":", "-")
        dest = os.path.join(args.out, f"{safe}.{args.format}")
        with urllib.request.urlopen(url, timeout=180) as r, open(dest, "wb") as fh:
            fh.write(r.read())
        print(f"  {nid} -> {dest} ({os.path.getsize(dest):,} bytes)")
        written += 1
    print(f"\n{written}/{len(ids)} exported into {args.out}")
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token")
    sub = ap.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("list")
    l.add_argument("--file", required=True)
    l.add_argument("--depth", type=int, default=3)
    l.add_argument("--match", help="only show frames whose name contains this")
    l.add_argument("--min-width", type=int, default=300)
    l.set_defaults(func=cmd_list)

    e = sub.add_parser("export")
    e.add_argument("--file", required=True)
    e.add_argument("--nodes", required=True, help="comma separated node ids")
    e.add_argument("--out", required=True)
    e.add_argument("--format", default="png", choices=["png", "svg", "jpg"])
    e.add_argument("--scale", default="2")
    e.set_defaults(func=cmd_export)

    args = ap.parse_args()
    token, source = load_token(args.token)
    if not token:
        print("No Figma token. Run figma_check.py for setup instructions.")
        return 2
    return args.func(args, token)

if __name__ == "__main__":
    sys.exit(main())
