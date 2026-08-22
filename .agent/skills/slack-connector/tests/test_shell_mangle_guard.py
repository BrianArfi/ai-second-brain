"""Guard against the 2026-08-07 mangled-currency send reaching Slack again."""
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

fails = []

def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}: {name} (got {got}, want {want})")
    if not ok:
        fails.append(name)

# The exact text that went out today.
BROKEN = ("Cost premium over staying on Jira is ~,763/yr at steady state, "
          "noise against Work's ~0k/yr existing Atlassian bill.")
FIXED = ("Cost premium over staying on Jira is ~$1,763/yr at steady state, "
         "noise against Work's ~$50k/yr existing Atlassian bill.")

check("catches the real mangled message", sc.warn_shell_mangled_text(BROKEN), True)
check("passes the corrected message", sc.warn_shell_mangled_text(FIXED), False)

# False-positive guards: ordinary text must not trip it.
for name, sample in [
    ("plain prose", "Happy to take either one on a call if you would rather talk it through."),
    ("normal thousands", "The ramp reaches 130 seats and $24,960 per year."),
    ("bare number list", "Seats: 35, 55, 100, 130."),
    ("decimal", "Weighted 4.35 vs 3.70, a lead of 0.65."),
    ("date-like", "Delivered 2026-08-09 at 09:00."),
    ("percent", "Storefront is 66.7% complete, 17 of 30 tickets."),
]:
    check(f"no false positive: {name}", sc.warn_shell_mangled_text(sample), False)

# Other shapes of the same bug.
check("catches $50,000 -> ,000", sc.warn_shell_mangled_text("an Atlassian bill of roughly ,000 per year"), True)

print()
print("ALL PASS" if not fails else f"FAILURES: {fails}")
sys.exit(1 if fails else 0)
