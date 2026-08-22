#!/usr/bin/env python3
"""
gdoc_reply_comments.py -- reply to EXISTING comment threads on a Google Doc.

Distinct from .agent/skills/gdoc-comment/gdoc_comment.py, which mints NEW anchored
comments and has to drive the real editor via Playwright because the API cannot
create kix.* anchors. That limitation does not apply here: a reply attaches to a
thread that already carries its anchor, so the plain Drive API is safe and there
is no orphaning risk.

Reply bodies are read from a markdown draft file, one section per thread, so the
text that was reviewed is the text that gets posted. Markdown emphasis is
flattened because Google Docs comments render plain text.

Usage:
    # show what would be posted, matched to threads, without writing
    python3 .agent/scripts/gdoc_reply_comments.py --doc-id <ID> \
        --draft journal/reply_drafts/2026-08-05-gdoc-comments-and-share-requests.md \
        --map A="not possible" B="follow ExampleClient" C="give phones to a cashier" \
              D="card-linked offer" E="unclear"

    # post them
    ... --apply
"""

import argparse
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONNECTOR = os.path.join(BASE_DIR, ".agent", "skills", "work-drive-connector")
SCOPES = ["https://www.googleapis.com/auth/drive"]

def _service():
    from google.oauth2.credentials import Credentials  # type: ignore
    from google.auth.transport.requests import Request  # type: ignore
    from googleapiclient.discovery import build  # type: ignore

    tok = os.path.join(CONNECTOR, "token.json")
    creds = Credentials.from_authorized_user_file(tok, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(tok, "w") as fh:
            fh.write(creds.to_json())
    return build("drive", "v3", credentials=creds)

def flatten(md):
    """Google Docs comments are plain text. Strip emphasis, keep the words."""
    md = re.sub(r"\*\*(.+?)\*\*", r"\1", md, flags=re.S)
    md = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", md, flags=re.S)
    md = re.sub(r"`([^`]+)`", r"\1", md)
    md = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", md)
    return md.strip()

def load_sections(draft_path):
    """Split the draft's '### X. ...' blocks into {letter: body}."""
    text = open(draft_path, encoding="utf-8").read()
    out = {}
    pattern = re.compile(r"^### ([A-Z])\.\s*(.*)$", re.M)
    marks = list(pattern.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end]
        body = re.split(r"^---\s*$", body, maxsplit=1, flags=re.M)[0]
        out[m.group(1)] = flatten(body)
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--doc-id", required=True)
    ap.add_argument("--draft", required=True)
    ap.add_argument("--map", nargs="+", required=True,
                    help="LETTER=<substring of the comment being replied to>")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--skip-answered", action="store_true",
                    help="skip threads this account has already replied to, for safe re-runs")
    ap.add_argument("--me", default="Your Name",
                    help="display name used by --skip-answered")
    args = ap.parse_args()
    me = args.me

    draft = args.draft if os.path.isabs(args.draft) else os.path.join(BASE_DIR, args.draft)
    sections = load_sections(draft)

    pairs = []
    for item in args.map:
        if "=" not in item:
            ap.error("--map entries look like A=\"some text\"")
        letter, needle = item.split("=", 1)
        pairs.append((letter.strip(), needle.strip().lower()))

    svc = _service()
    threads = svc.comments().list(
        fileId=args.doc_id,
        # replies(author) is load-bearing: --skip-answered silently never matches
        # without it, which double-posted a reply on 5 Aug 2026.
        fields=("comments(id,author(displayName),content,resolved,"
                "quotedFileContent(value),replies(id,createdTime,author(displayName),content))"),
        includeDeleted=False,
    ).execute().get("comments", [])

    def norm(s):
        # Google Docs sprinkles non-breaking spaces into comment text, which
        # silently defeats substring matching. Collapse all whitespace.
        return " ".join((s or "").replace("\xa0", " ").split()).lower()

    plan, problems = [], []
    for letter, needle in pairs:
        if letter not in sections:
            problems.append("draft has no section %s" % letter)
            continue
        hits = [c for c in threads if norm(needle) in norm(c.get("content"))]
        if len(hits) != 1:
            problems.append("section %s: %d threads match %r (need exactly 1)"
                            % (letter, len(hits), needle))
            continue
        body = sections[letter]
        # Drive rejects a reply over 4096 bytes with commentLengthLimitExceeded.
        # Fail before posting anything rather than half-populating the thread.
        nbytes = len(body.encode("utf-8"))
        if nbytes > 4096:
            problems.append("section %s is %d bytes, over the 4096-byte comment limit. "
                            "Shorten it, or move the detail into the document."
                            % (letter, nbytes))
            continue
        # Idempotency: this script may be re-run after a partial failure.
        thread = hits[0]
        already = any(r.get("author", {}).get("displayName") == me
                      for r in (thread.get("replies") or []))
        if args.skip_answered and already:
            print("  [skip] section %s: %s already replied in that thread" % (letter, me))
            continue
        plan.append((letter, thread, body))

    print("Doc: %s" % args.doc_id)
    print("Threads on doc: %d, planned replies: %d" % (len(threads), len(plan)))
    for p in problems:
        print("  PROBLEM: %s" % p)
    if problems:
        print("\nRefusing to post while any thread is ambiguous.")
        return 1

    for letter, c, body in plan:
        who = c["author"]["displayName"]
        print("\n--- %s -> thread by %s ---" % (letter, who))
        print("    their comment: %s" % " ".join((c.get("content") or "").split())[:90])
        print("    reply length : %d chars, %d existing replies"
              % (len(body), len(c.get("replies") or [])))
        print("    first line   : %s" % body.split("\n")[0][:90])

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to post.")
        return 0

    posted = 0
    for letter, c, body in plan:
        svc.replies().create(
            fileId=args.doc_id,
            commentId=c["id"],
            body={"content": body},
            fields="id",
        ).execute()
        posted += 1
        print("  [ok] posted reply %s to thread %s" % (letter, c["id"]))

    link = "https://docs.google.com/document/d/%s/edit" % args.doc_id
    print("\nFile updated: %s -> %s" % (args.doc_id, link))
    print("Posted %d of %d replies." % (posted, len(plan)))
    return 0 if posted == len(plan) else 1

if __name__ == "__main__":
    sys.exit(main())
