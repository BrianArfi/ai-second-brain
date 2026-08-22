"""Reproduce the 2026-08-07 morning-harvest failure and prove the fix.

Before the fix: an IncompleteRead on any users.list page propagated out of
_build_user_name_map, killed list_joined_channels, and lost all 269 channels.
"""
import http.client
import importlib.util
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
spec = importlib.util.spec_from_file_location(
    "slack_client",
    os.path.join(REPO, '.agent', 'skills', 'slack-connector', 'scripts', 'slack_client.py'),
)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

sc.BASE_BACKOFF_SECONDS = 0  # no real sleeping in the test

failures = []

def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}: {name} {detail}")
    if not cond:
        failures.append(name)

# --- 1. Permanent transport failure degrades, never raises, never exits ------
calls = {"n": 0}

def always_incomplete(endpoint, token, params=None, retry_count=0):
    calls["n"] += 1
    raise http.client.IncompleteRead(b"x" * 10, 36724)

orig = sc.make_slack_request
sc.make_slack_request = always_incomplete
try:
    names = sc._build_user_name_map("xoxp-fake")
    check("permanent users.list failure does not raise", True)
    check("falls back to cached names", len(names) > 0, f"({len(names)} cached names)")
except SystemExit as e:
    check("permanent users.list failure does not sys.exit", False, f"(exited {e.code})")
except Exception as e:
    check("permanent users.list failure does not raise", False, f"({type(e).__name__})")
finally:
    sc.make_slack_request = orig

# --- 2. The cache file was not clobbered by the failed sweep ----------------
after = sc._load_cached_user_names()
check("cache not overwritten on total failure", len(after) > 0, f"({len(after)} entries)")

# --- 3. Transient failure retries, then succeeds ---------------------------
state = {"n": 0}
real_urlopen = sc.urllib.request.urlopen

class FakeResp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

def flaky_urlopen(req, timeout=None):
    state["n"] += 1
    if state["n"] <= 2:
        raise http.client.IncompleteRead(b"partial", 100)
    return FakeResp(b'{"ok": true, "members": [{"id": "U1", "profile": {"display_name": "Test User"}}]}')

sc.urllib.request.urlopen = flaky_urlopen
try:
    resp = sc.make_slack_request("users.list", "xoxp-fake", {"limit": 200})
    check("retries transient error then succeeds", resp.get("ok") is True, f"(after {state['n']} attempts)")
    check("retried exactly twice before success", state["n"] == 3, f"(attempts={state['n']})")
finally:
    sc.urllib.request.urlopen = real_urlopen

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
