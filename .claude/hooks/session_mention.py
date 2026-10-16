#!/usr/bin/env python3
"""UserPromptSubmit hook: resolve "the other session" / "yang tadi" to Session Cards.

When the prompt points at another session without naming it ("sesi lain", "the other
session", "yang tadi", "lanjutin yang ..."), print ONE line with the top three
matching cards (keyword overlap with the prompt, then recency):

    Possible sessions: f245e1e3 PRD Sold Out (2h ago) | 1a2b3c4d Sweep Slack (5h ago)

The agent confirms with the owner only when the top match is unclear. Silent when the
prompt has no such phrase or no card exists. Stdlib only, exit 0 always.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / ".agent" / "scripts"))

TRIGGER = re.compile(
    r"(sesi lain|session lain|the other session|other session|yang tadi|sesi tadi"
    r"|lanjut(in)? yang|continue the one)", re.I)
STOP = {
    "sesi", "session", "lain", "the", "other", "yang", "tadi", "lanjut", "lanjutin", "continue",
    "one", "dari", "untuk", "dengan", "this", "that", "with", "from", "tolong", "please",
    "dan", "atau", "itu", "ini", "bisa", "dong", "ya", "kamu", "saya", "aku",
}
MAX_CARDS = 3

def words(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if w not in STOP}

def rank(prompt, cards, current_id=""):
    want = words(prompt)
    scored = []
    for i, c in enumerate(cards):  # cards arrive newest first
        if c.get("session_id") == current_id:
            continue
        hay = words(" ".join([str(c.get("spoken_name") or ""), str(c.get("purpose") or ""),
                              " ".join(c.get("nodes") or [])]))
        overlap = len(want & hay)
        # Overlap dominates; recency (list position) breaks ties and fills the rest.
        scored.append((-overlap, i, c))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [c for _, _, c in scored[:MAX_CARDS]]

def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    prompt = str(payload.get("prompt") or "")
    if not TRIGGER.search(prompt):
        return
    import session_coord_common as C
    cards = C.load_cards()
    top = rank(prompt, cards, str(payload.get("session_id") or ""))
    if not top:
        return
    parts = []
    for c in top:
        short = c.get("short_id") or str(c["session_id"])[:8]
        name = c.get("spoken_name") or (c.get("purpose") or "")[:30]
        parts.append("%s %s (%s)" % (short, name, C.human_age(C.age_seconds(c.get("last_active_at")))))
    print("Possible sessions: " + " | ".join(parts))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
