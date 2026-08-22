"""Guard against the 2026-08-19 silent split reaching Slack again.

A 5000-character reply to Najem Nabhani went out as two messages: Slack chose
the break itself and put it mid-way through a bullet list, while this script
printed one success line and one permalink. Nothing in the output said two
messages existed. The fix blocks over-limit sends unless --allow-split is
passed explicitly.
"""
import importlib.util
import io
import os
import sys
from contextlib import redirect_stderr

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
spec = importlib.util.spec_from_file_location(
    "slack_client",
    os.path.join(REPO, '.agent', 'skills', 'slack-connector', 'scripts', 'slack_client.py'),
)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

fails = []

def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {name} (got {got}, want {want})")
    if not ok:
        fails.append(name)

def run(text, allow_split=False):
    """Returns (allowed, stderr_output)."""
    buf = io.StringIO()
    with redirect_stderr(buf):
        allowed = sc.check_length(text, allow_split=allow_split)
    return allowed, buf.getvalue()

# A message comfortably inside the limit sends, silently.
ok, err = run("Short reply, nothing to see here.")
check("under limit sends", ok, True)
check("under limit stays quiet", err.strip(), "")

# Exactly at the limit is still fine; Slack splits above it, not at it.
ok, _ = run("x" * sc.SLACK_TEXT_LIMIT)
check("exactly at limit sends", ok, True)

# One character over is blocked. This is the real Najem case, scaled down.
ok, err = run("x" * (sc.SLACK_TEXT_LIMIT + 1))
check("one over is blocked", ok, False)
check("blocked message names the count", str(sc.SLACK_TEXT_LIMIT + 1) in err, True)

# The Najem draft was 5000 characters of prose with paragraph breaks. The guard
# should block it AND point at the last paragraph boundary that fits, so the
# split becomes a deliberate choice rather than Slack's.
para = ("A paragraph of roughly sixty characters for the boundary test.\n\n" * 90)
ok, err = run(para)
check("long prose is blocked", ok, False)
check("names a paragraph break", "paragraph break that fits" in err, True)

# --allow-split is the deliberate escape hatch, for output nobody reads as prose.
ok, err = run("x" * 9000, allow_split=True)
check("allow_split sends anyway", ok, True)

print()
if fails:
    print(f"{len(fails)} FAILED: {', '.join(fails)}")
    sys.exit(1)
print("All length-guard tests passed.")
