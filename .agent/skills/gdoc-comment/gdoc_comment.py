#!/usr/bin/env python3
"""Reliably create ANCHORED comments on a native Google Doc by driving the real
editor via Playwright (keyboard-only: Find -> select -> Ctrl+Alt+M -> type -> post).

Why this exists: the Drive/Docs API cannot mint Google Docs' internal `kix.*` comment
anchors, so API-created comments always orphan as "Original content deleted". Going
through the editor mints a real kix anchor every time. See memory
reference_gdoc_anchored_comments.

Auth: a persistent Chromium profile (./profile/<account>) logged into the owner's Google
account. Run `login` ONCE per account (headed window via WSLg); the session persists.

Verification: after posting, reads the comment back via the Drive API and confirms it
carries a `kix.*` anchor + matching quotedFileContent (orphaned == anchor None).

Usage:
  # one-time, opens a window on your desktop; sign in, then it auto-detects & saves
  python3 gdoc_comment.py login --account work

  # post comments (items = JSON list of {"anchor": "<unique text in doc>", "text": "<comment>"})
  python3 gdoc_comment.py comment --doc <DOC_ID> --account work --items items.json [--headed]

  # smoke test on a throwaway doc you own
  python3 gdoc_comment.py comment --doc <DOC_ID> --account work \
      --items - <<< '[{"anchor":"Some unique phrase","text":"test note"}]'
"""
import argparse, json, sys, time, os
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parents[2]

sys.path.insert(0, str(REPO_ROOT / '.agent' / 'scripts'))
from file_utils import assert_drive_result  # Drive Operation Verification (CLAUDE.md)

ACCOUNT_TOKENS = {
    "work": REPO_ROOT / ".agent/skills/work-drive-connector/token.json",
    "personal": REPO_ROOT / ".agent/skills/personal-drive-connector/token.json",
}

def profile_dir(account):
    d = SKILL_DIR / "profile" / account
    d.mkdir(parents=True, exist_ok=True)
    return str(d)

def chromium_executable():
    """Pick a WORKING ms-playwright chromium build (some bundled revisions ship a
    corrupt V8 snapshot and SEGV on launch). Honors $GDOC_CHROMIUM, else picks the
    highest chromium-* build that actually launches. Returns None to use the default."""
    env = os.environ.get("GDOC_CHROMIUM")
    if env and Path(env).exists():
        return env
    if sys.platform == "darwin":
        # The corrupt-snapshot workaround below is Linux-only: it globs
        # ~/.cache/ms-playwright/*/chrome-linux64/chrome, which never exists on
        # macOS (browsers live in ~/Library/Caches/ms-playwright/*/chrome-mac*).
        # Probing with --no-sandbox is also wrong here. Let Playwright resolve it.
        return None
    import glob, subprocess
    cands = sorted(glob.glob(os.path.expanduser(
        "~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")),
        key=lambda p: int(p.split("chromium-")[1].split("/")[0]), reverse=True)
    for c in cands:
        try:
            subprocess.run([c, "--headless", "--no-sandbox", "--disable-gpu",
                            "--dump-dom", "about:blank"],
                           capture_output=True, timeout=25, check=True)
            return c
        except Exception:
            continue
    return None  # fall back to Playwright default

# ----------------------------- login -----------------------------
def cmd_login(args):
    from playwright.sync_api import sync_playwright
    exe = chromium_executable()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile_dir(args.account), headless=False, executable_path=exe,
            args=["--no-first-run", "--no-default-browser-check"],
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://docs.google.com/document/u/0/", wait_until="domcontentloaded")
        print("A browser window opened on your desktop. Sign in to your Google "
              f"({args.account}) account.")
        print("Waiting for sign-in (up to 5 min)...")
        deadline = time.time() + 300
        ok = False
        while time.time() < deadline:
            url = page.url
            # docs.google.com/document home (not the accounts/signin flow) => logged in
            if "accounts.google.com" not in url and "docs.google.com" in url:
                # Confirm via the session cookie, not the DOM. The old check looked
                # for SignOutOptions / "Start a new document" / [aria-label*=Account],
                # none of which match the current Docs home: on 9 Aug 2026 a genuinely
                # successful sign-in was reported as "could not confirm" and burned the
                # full 5-minute timeout, even though the saved profile then opened a
                # doc authenticated on the first try.
                try:
                    if any(c["name"] in ("SID", "__Secure-3PSID")
                           and c["domain"].endswith("google.com")
                           for c in ctx.cookies()):
                        ok = True
                        break
                except Exception:
                    pass
            time.sleep(2)
        ctx.close()
    print("[login] session saved" if ok else
          "[login] could not confirm sign-in; re-run and complete login in the window")
    return 0 if ok else 2

# --------------------------- commenting ---------------------------
def post_one(page, anchor, text):
    """Find `anchor`, select it, open a comment, type `text`, submit. Returns True/False."""
    mod = "Meta" if sys.platform == "darwin" else "Control"
    # focus the editor canvas
    page.keyboard.press(f"{mod}+Home")
    page.wait_for_timeout(300)
    # open Find
    page.keyboard.press(f"{mod}+f")
    page.wait_for_timeout(600)
    page.keyboard.type(anchor, delay=15)
    page.wait_for_timeout(700)
    page.keyboard.press("Enter")      # jump to first match
    page.wait_for_timeout(500)
    page.keyboard.press("Escape")     # close find bar; selection lands on the match
    page.wait_for_timeout(500)
    # open comment on the current selection
    page.keyboard.press(f"{mod}+Alt+m")
    page.wait_for_timeout(1800)
    # Confirm the comment draft box actually opened and holds focus BEFORE typing.
    # Without this the function always returned True, so a failed anchor (or a
    # swallowed shortcut) was still reported as "posted" and the keystrokes went
    # into the document canvas instead.
    focused = page.evaluate("""() => {
        const a = document.activeElement;
        return !!a && (a.getAttribute('contenteditable') === 'true'
                       || a.tagName === 'TEXTAREA');
    }""")
    if not focused:
        return False
    page.keyboard.type(text, delay=8)
    page.wait_for_timeout(400)
    # submit
    page.keyboard.press(f"{mod}+Enter")
    page.wait_for_timeout(2000)
    # the draft box closes on a successful submit; if it is still focused, it did not post
    still_open = page.evaluate("""() => {
        const a = document.activeElement;
        return !!a && a.getAttribute('aria-label') === 'Comment draft';
    }""")
    return not still_open

def cmd_comment(args):
    items = (json.load(sys.stdin) if args.items == "-"
             else json.load(open(args.items)))
    assert isinstance(items, list) and items, "items must be a non-empty JSON list"

    from playwright.sync_api import sync_playwright
    exe = chromium_executable()
    posted = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile_dir(args.account), headless=not args.headed, executable_path=exe,
            args=["--no-first-run", "--no-default-browser-check"],
            viewport={"width": 1400, "height": 1000},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"https://docs.google.com/document/d/{args.doc}/edit",
                  wait_until="domcontentloaded")
        if "accounts.google.com" in page.url:
            ctx.close()
            print("[comment] NOT logged in for this account. Run: login --account "
                  + args.account, file=sys.stderr)
            return 2
        page.wait_for_timeout(6000)  # let the editor fully load
        # click into the document body to ensure the canvas has focus
        try:
            page.mouse.click(700, 500)
            page.wait_for_timeout(400)
        except Exception:
            pass
        for it in items:
            try:
                if post_one(page, it["anchor"], it["text"]):
                    posted += 1
                    print(f"[comment] posted on anchor: {it['anchor'][:50]!r}")
                else:
                    print(f"[comment] NOT posted (comment box never opened or never "
                          f"closed): {it['anchor'][:50]!r}", file=sys.stderr)
            except Exception as e:
                print(f"[comment] FAILED on {it['anchor'][:50]!r}: {e}", file=sys.stderr)
            page.wait_for_timeout(800)
        page.wait_for_timeout(1500)
        ctx.close()

    verify(args.doc, args.account, items)
    # Drive Operation Verification: a comment "write" here means every item
    # posted successfully. args.doc is a known-good id on success; None forces
    # assert_drive_result to fail loudly (with the posted/total count) instead
    # of exiting 0 while comments silently failed to anchor.
    ok_id = args.doc if posted == len(items) else None
    assert_drive_result(ok_id, f"gdoc_comment post ({posted}/{len(items)} items posted)")
    return 0

def verify(doc_id, account, items):
    """Read comments back via Drive API; confirm kix anchors + matching quoted text."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except Exception as e:
        print(f"[verify] skipped (google libs unavailable): {e}")
        return
    creds = Credentials.from_authorized_user_file(str(ACCOUNT_TOKENS[account]))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    d = build("drive", "v3", credentials=creds)
    # An Office file opened in the Docs editor keeps its comments INSIDE the file
    # (word/comments.xml), not in Drive's comment store, so comments().list always
    # returns [] and every item below would be reported "NOT ANCHORED" even when it
    # posted fine. Seen on the Project Wolf .docx, 9 Aug 2026. Say so instead of lying.
    mime = d.files().get(fileId=doc_id, fields="mimeType").execute().get("mimeType", "")
    if mime != "application/vnd.google-apps.document":
        print("\n=== verification SKIPPED ===")
        print(f"  {doc_id} is {mime},")
        print("  not a native Google Doc. Drive's comments API cannot see comments on")
        print("  Office files. Verify by downloading the file and reading")
        print("  word/comments.xml, or ask the owner to convert it to a Google Doc.")
        return
    cs = d.comments().list(
        fileId=doc_id,
        fields="comments(id,anchor,resolved,quotedFileContent(value),content)"
    ).execute().get("comments", [])
    print("\n=== verification (Drive API read-back) ===")
    import html
    for it in items:
        snippet = it["anchor"][:30].lower()
        match = None
        for c in cs:
            q = html.unescape((c.get("quotedFileContent") or {}).get("value", "")).lower()
            if snippet and snippet in q and (c.get("anchor") or "").startswith("kix."):
                match = c
                break
        if match:
            print(f"  ANCHORED OK  kix={match['anchor']:<22} <- {it['anchor'][:45]!r}")
        else:
            print(f"  NOT ANCHORED (orphaned/failed)         <- {it['anchor'][:45]!r}")

def main():
    ap = argparse.ArgumentParser(description="Create anchored comments on a Google Doc via the editor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("login", help="one-time interactive sign-in (headed)")
    lp.add_argument("--account", choices=list(ACCOUNT_TOKENS), default="work")
    lp.set_defaults(func=cmd_login)

    cp = sub.add_parser("comment", help="post anchored comments")
    cp.add_argument("--doc", required=True, help="Google Doc ID")
    cp.add_argument("--account", choices=list(ACCOUNT_TOKENS), default="work")
    cp.add_argument("--items", required=True, help="JSON file of [{anchor,text}], or - for stdin")
    cp.add_argument("--headed", action="store_true", help="show the browser (debug)")
    cp.set_defaults(func=cmd_comment)

    args = ap.parse_args()
    sys.exit(args.func(args))

if __name__ == "__main__":
    main()
