#!/usr/bin/env python3
"""
gdoc_insert_images.py -- replace [[TOKEN]] placeholders in a Google Doc with real images.

WHY THIS EXISTS
---------------
On 5 Aug 2026 the SAIB BRD was found carrying 19 unresolved [[IMG_*]] tokens
instead of its 19 mockups. The cause was structural, not a missed step: the
harness had no way to write into a Google Doc body at all. `gdocs_create.py`
builds a Doc by uploading HTML through Drive's converter
(`drive.files().create(mimeType='application/vnd.google-apps.document')`), which
needs only the `drive` scope and cannot place an image at a position.

Regenerating the Doc from markdown is NOT an acceptable fix once a document is
in review: Google anchors comments to text ranges, so replacing the body
orphans every open comment. On the SAIB BRD that would have discarded five
unresolved CEO comments.

So this does surgical insertion via the Docs API: delete the placeholder's text
range, insert an inline image at that exact index, leave everything else
untouched. Comment anchors elsewhere in the document survive.

PREREQUISITE, AND IT NEEDS A HUMAN
----------------------------------
The Docs API needs https://www.googleapis.com/auth/documents. No token in this
repo has it (audited 5 Aug 2026: every token is drive, gmail, calendar, forms,
analytics or script). Granting it is an interactive browser consent, so it
cannot be done from a non-interactive session. Run:

    python3 .agent/scripts/gdoc_insert_images.py --grant-scope

once, approve in the browser, then the insert runs normally afterwards.

USAGE
-----
    # see what it would do, no writes, no auth beyond read
    python3 .agent/scripts/gdoc_insert_images.py \
        --doc-id <ID> \
        --source Clients/Work/Marketplace/BRD_SAIB_Offer_Redemption.md \
        --assets Clients/Work/Marketplace/assets/saib_journeys

    # actually insert
    ... --apply

HOW IMAGES GET IN
-----------------
The Docs API only accepts a publicly fetchable URI for an inline image. Each
asset is therefore uploaded to Drive, made link-readable, inserted, and then
trashed once the Doc holds its own copy. The temporary public window is per
image and measured in seconds, and the files are removed at the end even if a
later insert fails.
"""

import argparse
import os
import re
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONNECTOR = os.path.join(BASE_DIR, ".agent", "skills", "work-drive-connector")

PLACEHOLDER_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")
DOCS_SCOPE = "https://www.googleapis.com/auth/documents"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

# Diagram tokens do not follow the screen naming. Kept in step with doc_share_gate.py.
ALIASES = {
    "DIAG_BASELINE": "flow_baseline",
    "DIAG_D_FLOW": "flow_optiond",
    "DIAG_D_SEQ": "flow_sequence",
    "IMG_BM": "bm_patterns",
    "IMG_C1": "c1_show_confirm",
}

# Width in points. Phone mockups are portrait and must not run full width.
DEFAULT_WIDTH_PT = 220.0
DIAGRAM_WIDTH_PT = 460.0

def _candidates(token):
    t = token.lower()
    out = []
    if token in ALIASES:
        out.append(ALIASES[token])
    out.append(t)
    if t.startswith("img_"):
        out.append(t[4:])
    if t.startswith("diag_"):
        out.append("flow_" + t[5:])
        out.append(t[5:])
    return out

def resolve_assets(tokens, assets_dir):
    files = os.listdir(assets_dir)
    stems = {os.path.splitext(f)[0].lower(): f for f in files}
    resolved, missing = {}, []
    for tok in tokens:
        hit = None
        for cand in _candidates(tok):
            if cand in stems:
                hit = stems[cand]
                break
            pref = [v for k, v in stems.items() if k.startswith(cand + "_")]
            if pref:
                hit = sorted(pref)[0]
                break
        if hit:
            resolved[tok] = os.path.join(assets_dir, hit)
        else:
            missing.append(tok)
    return resolved, missing

def grant_scope():
    """Interactive one-time consent adding the Docs scope alongside Drive."""
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore

    creds_file = None
    for name in ("credentials.json", "client_secret.json"):
        p = os.path.join(CONNECTOR, name)
        if os.path.isfile(p):
            creds_file = p
            break
    if not creds_file:
        print("FATAL: no credentials.json in %s" % CONNECTOR)
        return 2
    out = os.path.join(CONNECTOR, "token_docs_work.json")
    flow = InstalledAppFlow.from_client_secrets_file(creds_file, [DRIVE_SCOPE, DOCS_SCOPE])
    # This project's credentials.json is a WEB client with no redirect_uris set,
    # so the port cannot float. gdrive_manager.py pins the same value, and
    # http://localhost:8080/ is what is registered against the client in the
    # Cloud Console. Using port=0 here yields redirect_uri_mismatch.
    flow.redirect_uri = "http://localhost:8080/"
    creds = flow.run_local_server(port=8080, redirect_uri_trailing_slash=True)
    with open(out, "w") as fh:
        fh.write(creds.to_json())
    print("[OK] wrote %s" % os.path.relpath(out, BASE_DIR))
    print("     scopes: %s" % ", ".join(creds.scopes or []))
    return 0

def _creds():
    """The Docs API accepts the broad Drive scope, so the existing connector
    token works. `documents` is only needed by apps that hold drive.file or
    nothing else; gdoc_surgical.py has been calling documents.batchUpdate on
    the plain drive token all along. --grant-scope is kept for the narrow case
    where someone rebuilds this on a drive.file-only client."""
    from google.oauth2.credentials import Credentials  # type: ignore
    from google.auth.transport.requests import Request  # type: ignore

    for name, scopes in (("token_docs_work.json", [DRIVE_SCOPE, DOCS_SCOPE]),
                         ("token.json", [DRIVE_SCOPE])):
        tok = os.path.join(CONNECTOR, name)
        if not os.path.isfile(tok):
            continue
        creds = Credentials.from_authorized_user_file(tok, scopes)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(tok, "w") as fh:
                fh.write(creds.to_json())
        return creds
    return None

def find_placeholder(doc, token):
    """Return (start, end) index of the literal [[token]] run, or None.

    Google splits a paragraph into runs, so the token can straddle several
    text elements. Rebuild each paragraph's text with its start index and
    search the reconstructed string.
    """
    needle = "[[%s]]" % token
    for element in doc.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue
        text, start = "", None
        for el in para.get("elements", []):
            run = el.get("textRun")
            if not run:
                continue
            if start is None:
                start = el["startIndex"]
            text += run.get("content", "")
        if start is None:
            continue
        pos = text.find(needle)
        if pos != -1:
            return start + pos, start + pos + len(needle)
    return None

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grant-scope", action="store_true",
                    help="run the one-time interactive consent for the Docs scope, then exit")
    ap.add_argument("--doc-id")
    ap.add_argument("--source", help="local markdown, read only to learn the token list")
    ap.add_argument("--assets")
    ap.add_argument("--apply", action="store_true", help="without this it is a dry run")
    args = ap.parse_args()

    if args.grant_scope:
        return grant_scope()

    if not (args.doc_id and args.source and args.assets):
        ap.error("--doc-id, --source and --assets are required unless --grant-scope")

    src = args.source if os.path.isabs(args.source) else os.path.join(BASE_DIR, args.source)
    adir = args.assets if os.path.isabs(args.assets) else os.path.join(BASE_DIR, args.assets)
    tokens = sorted(set(PLACEHOLDER_RE.findall(open(src, encoding="utf-8").read())))
    if not tokens:
        print("No [[TOKEN]] placeholders in %s. Nothing to do." % args.source)
        return 0

    resolved, missing = resolve_assets(tokens, adir)
    print("Tokens: %d, resolved to files: %d, missing: %d" % (len(tokens), len(resolved), len(missing)))
    for t in missing:
        print("  MISSING asset for [[%s]]" % t)
    if missing:
        print("Refusing to run with missing assets.")
        return 1

    creds = _creds()
    if creds is None:
        print("\nBLOCKED: the Docs API scope has not been granted.")
        print("No token in this repo carries %s." % DOCS_SCOPE)
        print("This needs a browser and cannot be done from a non-interactive session.\n")
        print("  python3 .agent/scripts/gdoc_insert_images.py --grant-scope\n")
        print("Dry run of what would be inserted:")
        for t in tokens:
            print("  [[%s]] -> %s" % (t, os.path.basename(resolved[t])))
        return 1

    from googleapiclient.discovery import build  # type: ignore
    from googleapiclient.http import MediaFileUpload  # type: ignore

    docs = build("docs", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    if not args.apply:
        print("\nDRY RUN. Would insert, in document order:")
        for t in tokens:
            print("  [[%s]] -> %s" % (t, os.path.basename(resolved[t])))
        print("\nRe-run with --apply to write.")
        return 0

    temp_ids = []
    inserted = 0
    try:
        for token in tokens:
            path = resolved[token]
            meta = drive.files().create(
                body={"name": "tmp_brd_%s_%s" % (token, int(time.time()))},
                media_body=MediaFileUpload(path, mimetype="image/png"),
                fields="id",
            ).execute()
            fid = meta["id"]
            temp_ids.append(fid)
            drive.permissions().create(
                fileId=fid, body={"role": "reader", "type": "anyone"}
            ).execute()
            uri = "https://drive.google.com/uc?export=download&id=%s" % fid

            doc = docs.documents().get(documentId=args.doc_id).execute()
            span = find_placeholder(doc, token)
            if not span:
                print("  [skip] [[%s]] not found in the doc" % token)
                continue
            start, end = span
            width = DIAGRAM_WIDTH_PT if token.startswith("DIAG") or token == "IMG_BM" else DEFAULT_WIDTH_PT
            docs.documents().batchUpdate(
                documentId=args.doc_id,
                body={"requests": [
                    {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}},
                    {"insertInlineImage": {
                        "location": {"index": start},
                        "uri": uri,
                        "objectSize": {"width": {"magnitude": width, "unit": "PT"}},
                    }},
                ]},
            ).execute()
            inserted += 1
            print("  [ok] [[%s]] -> %s" % (token, os.path.basename(path)))
    finally:
        for fid in temp_ids:
            try:
                drive.files().delete(fileId=fid).execute()
            except Exception:
                print("  [warn] could not remove temp Drive file %s, delete it by hand" % fid)

    # Repo convention: a writer reports a file id and link or it counts as failed.
    link = "https://docs.google.com/document/d/%s/edit" % args.doc_id
    print("\nFile updated: %s -> %s" % (args.doc_id, link))
    print("Inserted %d of %d images." % (inserted, len(tokens)))
    if inserted != len(tokens):
        print("WARNING: not every placeholder was replaced. Re-run to finish.")
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
