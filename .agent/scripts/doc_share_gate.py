#!/usr/bin/env python3
"""
doc_share_gate.py -- block a client-facing doc from being shared until it is actually shareable.

Written 5 Aug 2026 after the SAIB BRD was found sitting on Drive with
`{"role": "commenter", "type": "anyone"}` (public to anyone with the link) while
carrying 19 unresolved [[IMG_*]] placeholder tokens instead of its 19 mockups,
and a paragraph describing SAIB's internal politics.

Four gates, any one of which fails the run:

  1. PLACEHOLDERS  unresolved [[TOKEN]] markers in the local source
  2. ASSETS        every [[TOKEN]] maps to a file that exists on disk
  3. LINKS         malformed URLs (a local path glued onto a docs.google.com host)
  4. SHARING       Drive permission entries with type=anyone

Usage:
    python3 .agent/scripts/doc_share_gate.py \
        --source Clients/Work/Marketplace/BRD_SAIB_Offer_Redemption.md \
        --assets Clients/Work/Marketplace/assets/saib_journeys \
        --doc-id <YOUR_DRIVE_ID>

    # local checks only, no network
    python3 .agent/scripts/doc_share_gate.py --source <path> --assets <dir>

Exit codes: 0 all gates pass, 1 one or more gates failed, 2 bad invocation.
"""

import argparse
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PLACEHOLDER_RE = re.compile(r"\[\[([A-Z0-9_]+)\]\]")

# A docs.google.com (or drive.google.com) host followed by something that is
# obviously a repo path rather than a document id.
BAD_LINK_RE = re.compile(
    r"https?://(?:docs|drive)\.google\.com/(?!document/|spreadsheets/|presentation/|file/|drive/|forms/)[^)\s]*"
)

# Diagram tokens do not follow the screen naming, so they are mapped by hand.
ALIASES = {
    "DIAG_BASELINE": "flow_baseline",
    "DIAG_D_FLOW": "flow_optiond",
    "DIAG_D_SEQ": "flow_sequence",
    "IMG_BM": "bm_patterns",
    "IMG_C1": "c1_show_confirm",
}

# Token stem -> filename stem. Tokens are named for the screen, files are named
# for the screen plus a human label, so match on the stem and accept one suffix.
def _candidates(token):
    """Filename stems that could satisfy this token, most specific first."""
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

def gate_placeholders(text):
    return sorted(set(PLACEHOLDER_RE.findall(text)))

def gate_assets(tokens, assets_dir):
    """Return (resolved, missing). resolved maps token -> filename."""
    if not os.path.isdir(assets_dir):
        return {}, list(tokens)
    files = os.listdir(assets_dir)
    stems = {os.path.splitext(f)[0].lower(): f for f in files}
    resolved, missing = {}, []
    for tok in tokens:
        hit = None
        for cand in _candidates(tok):
            if cand in stems:
                hit = stems[cand]
                break
            # allow file stem to start with the candidate, e.g. d3 -> d3_outlet_code
            pref = [v for k, v in stems.items() if k.startswith(cand + "_")]
            if pref:
                hit = sorted(pref)[0]
                break
        if hit:
            resolved[tok] = hit
        else:
            missing.append(tok)
    return resolved, missing

def gate_links(text):
    return sorted(set(BAD_LINK_RE.findall(text)))

def gate_doc_placeholders(doc_id):
    """Unresolved [[TOKEN]] markers left in the DOC itself.

    The local markdown uses [[TOKEN]] as its legitimate image marker, so finding
    them there says nothing about whether the published doc is complete. What
    matters is whether the substitution reached the doc. Returns (tokens, error).
    """
    sys.path.insert(0, os.path.join(BASE_DIR, ".agent", "scripts"))
    try:
        from google.oauth2.credentials import Credentials  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
    except Exception as exc:
        return None, "google client libraries unavailable: %s" % exc
    tok = os.path.join(BASE_DIR, ".agent", "skills", "work-drive-connector", "token.json")
    try:
        creds = Credentials.from_authorized_user_file(
            tok, ["https://www.googleapis.com/auth/drive"])
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        doc = build("docs", "v1", credentials=creds).documents().get(
            documentId=doc_id).execute()
    except Exception as exc:
        return None, "Docs lookup failed: %s" % exc
    text = []
    for el in doc.get("body", {}).get("content", []):
        para = el.get("paragraph")
        if para:
            text.append("".join(r.get("textRun", {}).get("content", "")
                                for r in para.get("elements", [])))
    return sorted(set(PLACEHOLDER_RE.findall("\n".join(text)))), None

def gate_sharing(doc_id):
    """Return (public_entries, error). Requires Drive credentials."""
    sys.path.insert(0, os.path.join(BASE_DIR, ".agent", "scripts"))
    try:
        from drive_permissions import _service, list_perms
    except Exception as exc:
        return None, "could not import drive_permissions: %s" % exc
    try:
        svc = _service()
        perms = list_perms(svc, doc_id)
    except Exception as exc:
        return None, "Drive lookup failed: %s" % exc
    # list_perms is written for humans and its return shape varies: a dict
    # envelope, a flat list, or a list-per-file. Flatten to dicts before testing.
    if isinstance(perms, dict):
        perms = perms.get("permissions", perms.get("items", []))
    flat = []
    for entry in (perms or []):
        if isinstance(entry, dict):
            flat.append(entry)
        elif isinstance(entry, (list, tuple)):
            flat.extend(p for p in entry if isinstance(p, dict))
    public = [p for p in flat if p.get("type") == "anyone"]
    return public, None

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="local markdown source of the doc")
    ap.add_argument("--assets", default=None, help="directory holding the images the tokens refer to")
    ap.add_argument("--doc-id", default=None, help="Google Doc id, enables the sharing gate")
    ap.add_argument("--allow-public", action="store_true",
                    help="the doc is INTENDED to be public; skips gate 4. Use only for published artifacts.")
    args = ap.parse_args()

    src = args.source if os.path.isabs(args.source) else os.path.join(BASE_DIR, args.source)
    if not os.path.isfile(src):
        print("FATAL: source not found: %s" % src)
        return 2
    text = open(src, encoding="utf-8").read()

    failures = []
    print("Share gate: %s" % os.path.relpath(src, BASE_DIR))
    print("=" * 68)

    # Gate 1 + 2. When a doc id is given, the doc is the authority on whether
    # substitution happened; the local markdown's tokens are only the manifest.
    tokens = gate_placeholders(text)
    if args.doc_id:
        doc_tokens, err = gate_doc_placeholders(args.doc_id)
        if err:
            failures.append("placeholders-unknown")
            print("\n[FAIL] Gate 1 PLACEHOLDERS: could not read the doc. %s" % err)
        elif doc_tokens:
            failures.append("placeholders")
            print("\n[FAIL] Gate 1 PLACEHOLDERS: %d token(s) still unresolved IN THE DOC"
                  % len(doc_tokens))
            for t in doc_tokens:
                print("         [[%s]]" % t)
            print("\n         Fix:")
            print("         python3 .agent/scripts/gdoc_insert_images.py --doc-id %s \\"
                  % args.doc_id)
            print("             --source %s --assets %s --apply" % (args.source, args.assets))
        else:
            print("\n[ok]   Gate 1 PLACEHOLDERS: none left in the doc.")
            print("         (%d token(s) in the local source are its image manifest.)"
                  % len(tokens))
    if tokens and not args.doc_id:
        failures.append("placeholders")
        print("\n[FAIL] Gate 1 PLACEHOLDERS: %d unresolved token(s) in the source" % len(tokens))
        for t in tokens:
            print("         [[%s]]" % t)
    if tokens:
        if args.assets:
            adir = args.assets if os.path.isabs(args.assets) else os.path.join(BASE_DIR, args.assets)
            resolved, missing = gate_assets(tokens, adir)
            if missing:
                failures.append("assets")
                print("\n[FAIL] Gate 2 ASSETS: %d token(s) have no matching file in %s"
                      % (len(missing), args.assets))
                for t in missing:
                    print("         [[%s]] -> NOT FOUND" % t)
            else:
                print("\n[ok]   Gate 2 ASSETS: all %d token(s) map to a file on disk." % len(resolved))
                print("         The images exist. They were never inserted into the doc.")
                for t in tokens:
                    print("         [[%s]] -> %s" % (t, resolved[t]))
    else:
        print("\n[ok]   Gate 1 PLACEHOLDERS: none.")
        print("[ok]   Gate 2 ASSETS: nothing to resolve.")

    # Gate 3
    bad = gate_links(text)
    if bad:
        failures.append("links")
        print("\n[FAIL] Gate 3 LINKS: %d malformed Google URL(s)" % len(bad))
        for b in bad:
            print("         %s" % b)
    else:
        print("\n[ok]   Gate 3 LINKS: no malformed Google URLs.")

    # Gate 4
    if args.doc_id and not args.allow_public:
        public, err = gate_sharing(args.doc_id)
        if err:
            failures.append("sharing-unknown")
            print("\n[FAIL] Gate 4 SHARING: could not verify. %s" % err)
            print("         Treat as unsafe. Check sharing by hand before sending.")
        elif public:
            failures.append("sharing")
            print("\n[FAIL] Gate 4 SHARING: doc is PUBLIC to anyone with the link.")
            for p in public:
                print("         role=%s type=%s" % (p.get("role"), p.get("type")))
            print("\n         Fix:")
            print("         python3 .agent/scripts/drive_permissions.py restrict \\")
            print("             %s --domain yourcompany.com --apply" % args.doc_id)
        else:
            print("\n[ok]   Gate 4 SHARING: no anyone-with-link access.")
    elif args.allow_public:
        print("\n[skip] Gate 4 SHARING: --allow-public set.")
    else:
        print("\n[skip] Gate 4 SHARING: no --doc-id given.")

    print("\n" + "=" * 68)
    if failures:
        print("RESULT: BLOCKED (%s)" % ", ".join(sorted(set(failures))))
        print("Do not share this document until every gate passes.")
        return 1
    print("RESULT: CLEAR to share.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
