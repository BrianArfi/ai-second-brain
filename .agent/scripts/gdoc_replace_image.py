#!/usr/bin/env python3
"""
gdoc_replace_image.py -- swap an image already embedded in a Google Doc, in place.

Companion to gdoc_insert_images.py. That one fills [[TOKEN]] placeholders on a
first pass; this one replaces an image that is already there, which is what you
need once a mockup gets redrawn. Uses the Docs API replaceImage request, so the
image object keeps its position and nothing around it moves. Comment anchors and
surrounding text are untouched.

Images are identified by the caption that FOLLOWS them, because that is stable
and human-readable, unlike the generated object id.

Usage:
    python3 .agent/scripts/gdoc_replace_image.py --doc-id <ID> \
        --swap "Screen 5.=Clients/.../assets/saib_journeys/D3_outlet_code.png" \
        --swap "Figure 4.=Clients/.../assets/saib_journeys/D12_cashier_card.png" \
        [--apply]

The match is a prefix of the caption paragraph and must hit exactly one image.
"""

import argparse
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONNECTOR = os.path.join(BASE_DIR, ".agent", "skills", "work-drive-connector")
SCOPES = ["https://www.googleapis.com/auth/drive"]

def _creds():
    from google.oauth2.credentials import Credentials  # type: ignore
    from google.auth.transport.requests import Request  # type: ignore

    tok = os.path.join(CONNECTOR, "token.json")
    creds = Credentials.from_authorized_user_file(tok, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(tok, "w") as fh:
            fh.write(creds.to_json())
    return creds

def para_text(para):
    return "".join(r.get("textRun", {}).get("content", "")
                   for r in para.get("elements", []))

def index_images(doc):
    """[(objectId, caption_text)] in document order.

    The caption is the next non-empty paragraph after the image.
    """
    content = doc.get("body", {}).get("content", [])
    out = []
    for i, el in enumerate(content):
        para = el.get("paragraph")
        if not para:
            continue
        ids = [e["inlineObjectElement"]["inlineObjectId"]
               for e in para.get("elements", []) if "inlineObjectElement" in e]
        if not ids:
            continue
        caption = ""
        for j in range(i + 1, min(i + 4, len(content))):
            nxt = content[j].get("paragraph")
            if not nxt:
                continue
            t = " ".join(para_text(nxt).split())
            if t:
                caption = t
                break
        for oid in ids:
            out.append((oid, caption))
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc-id", required=True)
    ap.add_argument("--swap", action="append", required=True,
                    help='"<caption prefix>=<path to new png>"')
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from googleapiclient.discovery import build  # type: ignore
    from googleapiclient.http import MediaFileUpload  # type: ignore

    creds = _creds()
    docs = build("docs", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    doc = docs.documents().get(documentId=args.doc_id).execute()
    images = index_images(doc)
    print("Images in doc: %d" % len(images))

    plan, problems = [], []
    for spec in args.swap:
        if "=" not in spec:
            ap.error('--swap looks like "Screen 5.=path/to.png"')
        prefix, path = spec.split("=", 1)
        prefix = prefix.strip()
        full = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
        if not os.path.isfile(full):
            problems.append("no such file: %s" % path)
            continue
        hits = [(oid, cap) for oid, cap in images if cap.startswith(prefix)]
        if len(hits) != 1:
            problems.append("%r matched %d images (need exactly 1)" % (prefix, len(hits)))
            continue
        plan.append((prefix, hits[0][0], full, hits[0][1]))

    for p in problems:
        print("  PROBLEM: %s" % p)
    for prefix, oid, full, cap in plan:
        print("  %-12s -> %s" % (prefix, os.path.basename(full)))
        print("      caption: %s" % cap[:88])
    if problems:
        print("\nRefusing to run.")
        return 1
    if not args.apply:
        print("\nDRY RUN. Re-run with --apply.")
        return 0

    temp = []
    try:
        for prefix, oid, full, cap in plan:
            meta = drive.files().create(
                body={"name": "tmp_swap_%d" % int(time.time() * 1000)},
                media_body=MediaFileUpload(full, mimetype="image/png"),
                fields="id").execute()
            fid = meta["id"]
            temp.append(fid)
            drive.permissions().create(
                fileId=fid, body={"role": "reader", "type": "anyone"}).execute()
            docs.documents().batchUpdate(
                documentId=args.doc_id,
                body={"requests": [{"replaceImage": {
                    "imageObjectId": oid,
                    "uri": "https://drive.google.com/uc?export=download&id=%s" % fid,
                }}]}).execute()
            print("  [ok] replaced %s (%s)" % (prefix, os.path.basename(full)))
    finally:
        for fid in temp:
            try:
                drive.files().delete(fileId=fid).execute()
            except Exception:
                print("  [warn] leftover temp Drive file %s" % fid)

    link = "https://docs.google.com/document/d/%s/edit" % args.doc_id
    print("\nFile updated: %s -> %s" % (args.doc_id, link))
    return 0

if __name__ == "__main__":
    sys.exit(main())
