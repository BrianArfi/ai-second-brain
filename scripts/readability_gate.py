#!/usr/bin/env python3
"""Readability gate for Work docs, run BEFORE and AFTER publishing.

Catches the failure mode that shipped twice on 16-17 Jul 2026: a doc written as bare
consecutive sentence-lines with no markdown structure, which converts into an
unreadable wall of text in Google Docs.

Two modes:
  --source <file.md>   lint the markdown BEFORE publishing (the real fix point)
  --doc <DOC_ID>       verify the published Google Doc AFTER converting

Exit 0 = clean, exit 1 = blocked. Fix the SOURCE, never the doc by hand.

Four more rules, added 25 Sep 2026 after the OTP PRD/BRD shipped with local
links, a run-on header block, and "[[SCREENSHOT: ...]]" promises with no image:

  R1 links    Every link a reader can click must be an internet link. No local
              paths (C:/, /home/, file://, WSL UNC), no repo-relative .md links,
              no localhost dashboard links. Bare repo paths in the text fail too.
  R2 tables   Published tables need set column widths: no column narrower than
              36pt, no table wider than the page (paged docs), and no 3+ column
              table left at evenly distributed widths (format pass did not run).
  R3 promises Every [[PLACEHOLDER]] must resolve to a Mermaid diagram in
              embed_mermaid_in_gdoc.DIAGRAMS or an image in the --images map.
              Free-text "[[SCREENSHOT: ...]]" never resolves and fails. A
              "Representation of ..." caption needs a placeholder within 4
              lines. After publishing, the doc must hold at least as many
              images as the source promised, each with a real size.
  R4 header   A metadata line carrying 2+ "**Label:**" fields joined by a
              separator (a middle dot, bullet or pipe) fails. One field per
              line, or a table.
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.agent', 'scripts'))
from file_utils import find_ascii_diagrams  # text-drawn diagrams render broken in Docs

WALL_MIN = 2          # 2+ consecutive bare prose lines is a wall block
STRUCT_PREFIX = ('|', '-', '*', '#', '>', '[[', '<!--', '```')

# ---------------------------------------------------------------- R1 links
LOCAL_TARGET = re.compile(
    r'^(?:[A-Za-z]:[\\/]|file:|/home/|/Users/|/mnt/|\\\\|//wsl|'
    r'https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?)', re.I)
BARE_LOCAL = re.compile(
    r'(?:[A-Za-z]:/Users/\S+|~/\S+|localhost:\d+\S*|'
    r'\b(?:Clients|journal|inbox|scripts|\.agent)/[^\s`)|]+\.(?:md|html|py|json|png))')
MD_LINK = re.compile(r'(?<!!)\[([^\]]*)\]\(([^)\s]+)\)')

def _is_internet(target):
    t = target.strip().strip('<>')
    if LOCAL_TARGET.match(t):
        return False
    return bool(re.match(r'^(https?://|mailto:|#)', t, re.I))

def find_local_links(lines):
    """Return (line_no, what) for every link or bare path a reader cannot open."""
    hits = []
    fenced = False
    for i, raw in enumerate(lines, 1):
        if raw.strip().startswith('```'):
            fenced = not fenced
            continue
        if fenced:
            continue
        for _, target in MD_LINK.findall(raw):
            if not _is_internet(target):
                hits.append((i, target[:80]))
        for m in BARE_LOCAL.findall(MD_LINK.sub('', raw)):
            hits.append((i, m[:80]))
    return hits

# ---------------------------------------------------------------- R3 promises
PLACEHOLDER = re.compile(r'\[\[([^\]]+)\]\]')
PROMISE_CAPTION = re.compile(r'^\**\s*Representation of\b', re.I)

def _known_diagrams():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from embed_mermaid_in_gdoc import DIAGRAMS  # noqa: E402
        return set(DIAGRAMS)
    except Exception:
        return None

def _image_map(images_path):
    if not images_path:
        return {}
    with open(images_path, encoding='utf-8') as f:
        m = json.load(f)
    base = os.path.dirname(os.path.abspath(images_path))
    return {k: (v if os.path.isabs(v) else os.path.join(base, v)) for k, v in m.items()}

def find_broken_promises(lines, images_path=None):
    """Placeholders that will not turn into a picture, and captions with none."""
    diagrams = _known_diagrams()
    images = _image_map(images_path)
    fails, promised = [], 0
    for i, raw in enumerate(lines, 1):
        for tok in PLACEHOLDER.findall(raw):
            ph = '[[' + tok + ']]'
            promised += 1
            if ':' in tok or ' ' in tok:
                fails.append(f"line {i}: free-text placeholder {ph[:70]} has no image behind it. "
                             f"Make the screen, add it to an --images map, use a [[TOKEN]]")
            elif ph in images:
                if not os.path.isfile(images[ph]):
                    fails.append(f"line {i}: {ph} maps to a missing file {images[ph]}")
            elif diagrams is not None and ph not in diagrams:
                fails.append(f"line {i}: {ph} is not in DIAGRAMS and not in the --images map")
        if PROMISE_CAPTION.match(raw.strip()):
            window = ' '.join(lines[i:i + 4])
            if not PLACEHOLDER.search(window):
                fails.append(f"line {i}: caption promises a picture but no placeholder follows it")
    return fails, promised

# ---------------------------------------------------------------- R4 header
HEADER_FIELD = re.compile(r'\*\*[^*]{2,40}:\*\*')

def find_runon_headers(lines):
    hits = []
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if s.startswith('|'):
            continue
        if len(HEADER_FIELD.findall(s)) >= 2 and re.search(r'\s[\u00b7|\u2022]\s', s):
            hits.append(i)
    return hits

def lint_source(path: str, images_path: str = None) -> int:
    lines = open(path, encoding='utf-8').read().splitlines()
    fenced = False
    walls, cur, start = [], 0, 0
    missing_blank_table, missing_blank_list = [], []
    emdash = []

    for i, raw in enumerate(lines):
        s = raw.strip()
        if s.startswith('```'):
            fenced = not fenced
            if cur >= WALL_MIN:
                walls.append((start, cur))
            cur = 0
            continue
        if fenced:
            continue

        if '—' in raw:
            emdash.append(i + 1)

        prev = lines[i - 1].strip() if i else ''
        # A table/list must not sit directly under a PARAGRAPH line. Headings and
        # blockquotes are block-level, so the converter handles those fine.
        prev_is_block = (not prev) or prev.startswith(('#', '>', '<!--'))
        if s.startswith('|') and not prev_is_block and not prev.startswith('|'):
            missing_blank_table.append(i + 1)
        if re.match(r'^([-*]|\d+\.)\s', s) and not prev_is_block \
                and not re.match(r'^([-*]|\d+\.)\s', prev):
            missing_blank_list.append(i + 1)

        is_struct = (not s) or s.startswith(STRUCT_PREFIX) or bool(re.match(r'^\d+\.\s', s))
        if is_struct:
            if cur >= WALL_MIN:
                walls.append((start, cur))
            cur = 0
        else:
            if cur == 0:
                start = i + 1
            cur += 1
    if cur >= WALL_MIN:
        walls.append((start, cur))

    fails = []
    if walls:
        total = sum(n for _, n in walls)
        fails.append(
            f"{len(walls)} wall-of-text block(s), {total} bare prose lines. "
            f"Biggest at line {max(walls, key=lambda w: w[1])[0]} "
            f"({max(n for _, n in walls)} lines).\n"
            f"      Bare consecutive sentence-lines are NOT a list. Make each block a "
            f"table, real bullets, or one joined paragraph.\n"
            f"      Lines: {', '.join(str(s) for s, _ in walls[:12])}"
        )
    if missing_blank_table:
        fails.append(f"table(s) with no blank line before, at line(s): {missing_blank_table[:12]}")
    if missing_blank_list:
        fails.append(f"list(s) with no blank line before, at line(s): {missing_blank_list[:12]}")
    if emdash:
        fails.append(f"em-dash character at line(s): {emdash[:12]}")
    ascii_hits = find_ascii_diagrams('\n'.join(lines))
    if ascii_hits:
        fails.append(
            f"text-drawn diagram or raw mermaid fence at line(s): {[n for n, _ in ascii_hits][:12]}. "
            f"Redraw as Mermaid behind a [[PLACEHOLDER]] (.agent/skills/diagram-gen/SKILL.md)."
        )

    local = find_local_links(lines)
    if local:
        fails.append(
            f"R1 {len(local)} local link(s) or path(s) a reader cannot open: "
            + '; '.join(f"line {n}: {t}" for n, t in local[:8])
            + ". Link the Drive/Jira/Slack/Fathom URL instead, or drop the link.")
    broken, _ = find_broken_promises(lines, images_path)
    for b in broken[:10]:
        fails.append(f"R3 {b}")
    runon = find_runon_headers(lines)
    if runon:
        fails.append(f"R4 header line(s) {runon[:8]} pack several **Label:** fields on one line. "
                     f"Put each field on its own line, or use a table.")

    if fails:
        print(f"[GATE FAIL] {path}")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"[GATE PASS] {path}")
    return 0

def _doc_links(html):
    out = []
    for href in re.findall(r'href="([^"]+)"', html):
        href = href.replace('&amp;', '&')
        u = urlparse(href)
        if u.netloc.endswith('google.com') and u.path == '/url':
            href = unquote(parse_qs(u.query).get('q', [href])[0])
        out.append(href)
    return out

def check_tables(docs_api, doc_id):
    doc = docs_api.documents().get(documentId=doc_id).execute()
    style = doc.get('documentStyle', {})
    pageless = style.get('documentFormat', {}).get('documentMode') == 'PAGELESS'
    width = (style.get('pageSize', {}).get('width', {}).get('magnitude', 612)
             - style.get('marginLeft', {}).get('magnitude', 72)
             - style.get('marginRight', {}).get('magnitude', 72))
    problems, n = [], 0
    for el in doc.get('body', {}).get('content', []):
        t = el.get('table')
        if not t:
            continue
        n += 1
        cols = t.get('tableStyle', {}).get('tableColumnProperties', [])
        fixed = [c.get('width', {}).get('magnitude', 0) for c in cols
                 if c.get('widthType') == 'FIXED_WIDTH']
        if t.get('columns', 0) >= 3 and not fixed:
            problems.append(f"table {n}: {t['columns']} columns at even widths, format pass did not size it")
        narrow = [round(w) for w in fixed if 0 < w < 36]
        if narrow:
            problems.append(f"table {n}: column(s) narrower than 36pt ({narrow})")
        if not pageless and fixed and sum(fixed) > width + 1:
            problems.append(f"table {n}: {round(sum(fixed))}pt wide on a {round(width)}pt page")
    return problems, doc

def check_images(doc):
    bad = 0
    for obj in doc.get('inlineObjects', {}).values():
        emb = obj.get('inlineObjectProperties', {}).get('embeddedObject', {})
        size = emb.get('size', {})
        w = size.get('width', {}).get('magnitude', 0)
        h = size.get('height', {}).get('magnitude', 0)
        if not emb.get('imageProperties', {}).get('contentUri') or w < 20 or h < 20:
            bad += 1
    return len(doc.get('inlineObjects', {})), bad

def verify_doc(doc_id: str, account: str, allow_public: bool = False,
               source: str = None, images_path: str = None) -> int:
    sys.path.insert(0, '.agent/skills/gdocs-create')
    from gdocs_create import authenticate  # noqa: E402
    from googleapiclient.discovery import build  # noqa: E402

    drive = build('drive', 'v3', credentials=authenticate(account))
    html = drive.files().export(fileId=doc_id, mimeType='text/html').execute().decode()

    tables = len(re.findall(r'<table', html))
    li = len(re.findall(r'<li', html))
    img = len(re.findall(r'<img', html))
    leftover = html.count('[[') + html.count(':---') + html.count('```')

    perms = drive.permissions().list(
        fileId=doc_id, fields='permissions(type,role,domain,emailAddress)').execute()['permissions']
    public = any(p['type'] == 'anyone' for p in perms)

    print(f"  tables={tables} li={li} img={img} leftover={leftover} public={public}")

    fails = []
    if leftover:
        fails.append(f"{leftover} unconverted marker(s): raw [[placeholder]], :--- or ``` survived the convert")

    # R1: every link in the doc must open for a reader on another machine.
    local = [h for h in _doc_links(html) if not _is_internet(h)]
    bare = BARE_LOCAL.findall(re.sub(r'<[^>]+>', ' ', html))
    if local or bare:
        fails.append(f"R1 {len(local)} local link(s) and {len(bare)} bare local path(s) in the doc: "
                     + '; '.join((local + bare)[:6]))

    # R2 + R3: tables sized, images real, every promised picture present.
    docs_api = build('docs', 'v1', credentials=authenticate(account))
    tprobs, doc = check_tables(docs_api, doc_id)
    for t in tprobs[:8]:
        fails.append(f"R2 {t}")
    n_img, bad_img = check_images(doc)
    if bad_img:
        fails.append(f"R3 {bad_img} image(s) have no content or a near-zero size: they did not render")
    if source:
        _, promised = find_broken_promises(open(source, encoding='utf-8').read().splitlines(), images_path)
        if n_img < promised:
            fails.append(f"R3 source promises {promised} diagram(s)/screenshot(s), doc holds {n_img} image(s)")
    if public and not allow_public:
        fails.append("doc is PUBLIC (anyone with link). Run drive_permissions.py restrict LAST")
    if tables == 0 and li == 0:
        fails.append("doc has zero tables and zero list items: it is a wall of text")

    if fails:
        print(f"[GATE FAIL] doc {doc_id}")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"[GATE PASS] doc {doc_id}")
    return 0

def main():
    ap = argparse.ArgumentParser(description='Readability gate for Work docs')
    ap.add_argument('--source', help='markdown file to lint before publishing')
    ap.add_argument('--doc', help='Google Doc ID to verify after publishing')
    ap.add_argument('--account', default='work', choices=['work', 'personal', 'secondary'])
    ap.add_argument('--images', help='JSON map placeholder -> PNG, for placeholders that are images, not Mermaid')
    ap.add_argument('--allow-public', action='store_true',
                     help='skip the public-permission check (doc intentionally left public)')
    args = ap.parse_args()

    if not args.source and not args.doc:
        ap.error('pass --source and/or --doc')

    rc = 0
    if args.source:
        rc |= lint_source(args.source, args.images)
    if args.doc:
        rc |= verify_doc(args.doc, args.account, allow_public=args.allow_public,
                         source=args.source, images_path=args.images)
    return rc

if __name__ == '__main__':
    sys.exit(main())
