#!/usr/bin/env bash
# Single source of truth for RUNTIME awareness, sibling of detect_platform.sh.
#
# detect_platform.sh answers "what machine am I on".
# This answers "what agent runtime am I in, and what can it actually do".
#
# Run it directly, or (preferred) let .agent/scripts/harness_config.py run and cache it:
#   bash .agent/scripts/detect_runtime.sh
#   python3 .agent/scripts/harness_config.py --show
#
# Prints KEY=VALUE lines, same contract as detect_platform.sh:
#   RUNTIME      claude-code | antigravity | cursor | opencode | unknown
#   ENTRYPOINT   vscode | cli | sdk | unknown
#   MODEL        raw model id as reported by the runtime, or unknown
#                (under claude-code this is the model the session actually ran;
#                 under antigravity `agy models` only lists what is available,
#                 so set AGY_MODEL to pin the one in use)
#   TIER         flagship | mid | cheap | unknown
#   SUBAGENTS    yes | no   -- can the runtime spawn typed subagents
#   HOOKS        yes | no   -- does the runtime have a hook system
#   BACKENDS     comma separated subset of agy,zai,kimi (empty when none)
#
# DESIGN RULE: downstream code must branch on SUBAGENTS / HOOKS / TIER / BACKENDS,
# never on RUNTIME. "Am I Claude" is the wrong question; "can I delegate, can I
# enforce, how strong is my main loop" is the right one. RUNTIME is for logs and
# for the two places that genuinely need a vendor name (transcript path, agy CLI).
#
# Nothing here guesses. Anything that cannot be resolved from a real signal
# stays "unknown" and the caller degrades.

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# ---------- 1. runtime + entrypoint (env only, cheapest signal) ----------

RUNTIME="unknown"
ENTRYPOINT="unknown"

if [ "${CLAUDECODE:-}" = "1" ]; then
  RUNTIME="claude-code"
  # The raw value is a family, not one of our three words: the VS Code
  # extension reports "claude-vscode", the SDK reports "sdk-ts" / "sdk-py".
  # Match on substring, sdk before cli because "sdk-cli" exists.
  case "${CLAUDE_CODE_ENTRYPOINT:-}" in
    *vscode*|*jetbrains*) ENTRYPOINT="vscode"  ;;
    *sdk*)                ENTRYPOINT="sdk"     ;;
    *cli*)                ENTRYPOINT="cli"     ;;
    *)                    ENTRYPOINT="unknown" ;;
  esac
elif [ -n "${CURSOR_TRACE_ID:-}${CURSOR_AGENT:-}${CURSOR_WORKSPACE:-}" ]; then
  RUNTIME="cursor"
  ENTRYPOINT="vscode"
elif [ -n "${OPENCODE:-}${OPENCODE_BIN:-}${OPENCODE_SESSION:-}" ]; then
  RUNTIME="opencode"
  ENTRYPOINT="cli"
elif [ -n "${ANTIGRAVITY_SESSION:-}${AGY_SESSION:-}" ]; then
  RUNTIME="antigravity"
  ENTRYPOINT="cli"
fi

# ---------- 2. capabilities ----------
# Only claim a capability we can point at. Unknown runtimes get the safe answer
# (no), which makes the harness degrade instead of calling a tool that is absent.

case "$RUNTIME" in
  claude-code) SUBAGENTS="yes" ; HOOKS="yes" ;;
  *)           SUBAGENTS="no"  ; HOOKS="no"  ;;
esac

# ---------- 3. model, tier, backends ----------
# Delegated to python3 because all three need real parsing:
#   - MODEL under claude-code comes from the session transcript JSONL
#   - `agy models` needs a timeout that also works on macOS (no GNU `timeout`)
#   - the agy binary probe already exists in agy-bridge/run.py, reuse it
# If python3 is missing we still emit a valid, honest record.

MODEL="unknown"
TIER="unknown"
BACKENDS=""

if command -v python3 >/dev/null 2>&1; then
  _probe="$(RUNTIME="$RUNTIME" REPO_ROOT="$REPO_ROOT" python3 - <<'PY' 2>/dev/null
import json
import os
import re
import subprocess
import sys

RUNTIME = os.environ.get("RUNTIME", "unknown")
REPO_ROOT = os.environ.get("REPO_ROOT", "")

# Substring tiers. Checked cheap -> flagship -> mid so that a cheap variant of a
# strong family ("gemini-3-flash") lands in cheap, not flagship.
# NOTE: "-mini" keeps its dash on purpose; the bare string "mini" is a substring
# of "gemini" and would tier every Gemini model as cheap.
CHEAP = ("haiku", "flash", "lite", "-mini", "turbo", "small")
FLAGSHIP = ("opus", "fable", "gemini-3-pro", "gpt-5.1", "gpt-5-pro")
MID = ("sonnet", "gemini-2.5-pro", "gemini-pro", "glm-5", "glm-4", "kimi",
       "gpt-oss", "gpt-4", "gpt-5")

def tier_of(model):
    m = (model or "").lower()
    if not m or m == "unknown":
        return "unknown"
    for marker in CHEAP:
        if marker in m:
            return "cheap"
    for marker in FLAGSHIP:
        if marker in m:
            return "flagship"
    for marker in MID:
        if marker in m:
            return "mid"
    return "unknown"

def _claude_project_dir(root, repo_root):
    """The project dir by Claude Code's actual rule: every character that is not
    a letter, digit or "-" becomes "-".

    The "/"-only slug below never matched on Windows, where the path carries a
    drive colon and backslashes (C:\\Users\\x\\.foo -> C--Users-x--foo), so every
    Windows session reported MODEL=unknown. Git Bash also hands us the MSYS form
    (/c/Users/...) when CLAUDE_PROJECT_DIR is unset, so that is folded back to
    C:/Users/... first. The drive letter's case is not guaranteed either way,
    hence the case-insensitive comparison on Windows."""
    path = repo_root
    if os.name == "nt":
        m = re.match(r"^/([a-zA-Z])(/|$)", path)
        if m:
            path = m.group(1) + ":" + path[2:]
    slug = re.sub(r"[^A-Za-z0-9-]", "-", path)
    exact = os.path.join(root, slug)
    if os.path.isdir(exact):
        return exact
    if os.name != "nt":
        return None
    try:
        hits = [n for n in os.listdir(root) if n.lower() == slug.lower()]
    except OSError:
        return None
    return os.path.join(root, hits[0]) if len(hits) == 1 else None

def _fuzzy_project_dir(root, repo_root):
    """Locate this repo's project dir when the naive "/"-only slug misses.

    Build a pattern in which each "/", "." or "_" of the repo path is allowed
    to appear as any of those characters or as "-" in the directory name, then
    accept the result ONLY when exactly one directory matches. An ambiguous
    match returns None on purpose: no model beats a model read from someone
    else's checkout, which is the bug this whole function exists to prevent."""
    pattern = "".join("[-./_]" if ch in "/._" else re.escape(ch)
                      for ch in repo_root)
    try:
        rx = re.compile("^" + pattern + "$")
        hits = [n for n in os.listdir(root) if rx.match(n)]
    except (re.error, OSError):
        return None
    if len(hits) != 1:
        return None
    return os.path.join(root, hits[0])

def claude_transcript():
    """Newest transcript JSONL for THIS session, scoped to THIS repo's project dir.

    Claude Code names a project's transcript directory after the absolute repo
    path with "/" replaced by "-" (e.g. /home/u/foo -> -home-u-foo). Scoping to
    that one directory keeps an unrelated project the owner has open elsewhere
    on the machine from ever being read back as "the" session's model.

    The slugifier also flattens punctuation other than "/", so a checkout at
    /home/u/my_repo or /home/u/repo.v2 does not match a naive "/"-only
    translation. Rather than guess the full rule, try the naive slug first and
    then fall back to matching directory names against a pattern where "/", "."
    and "_" may each have become "-". A miss returns None, so detection reports
    MODEL=unknown instead of guessing, which is the failure mode we want.

    Prefer the session-scoped file: the newest file overall is often a subagent
    transcript (a haiku harvester), which would report the wrong main-loop tier.
    Subagent transcripts live under a `subagents/` subdir, so top-level files
    only is the correct fallback."""
    root = os.path.expanduser("~/.claude/projects")
    if not REPO_ROOT:
        return None
    pdir = _claude_project_dir(root, REPO_ROOT)
    if not pdir:
        pdir = os.path.join(root, REPO_ROOT.replace(os.sep, "-"))
    if not os.path.isdir(pdir):
        pdir = _fuzzy_project_dir(root, REPO_ROOT)
    if not pdir or not os.path.isdir(pdir):
        return None
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if sid:
        exact = os.path.join(pdir, sid + ".jsonl")
        if os.path.isfile(exact):
            return exact
    candidates = []
    try:
        for name in os.listdir(pdir):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(pdir, name)
            if os.path.isfile(path):
                candidates.append(path)
    except OSError:
        return None
    if not candidates:
        return None
    try:
        return max(candidates, key=os.path.getmtime)
    except OSError:
        return None

def model_from_transcript(path, tail_bytes=400000):
    """Last non-synthetic message.model in the transcript.

    Same approach as token-tracker/scripts/token_usage.py: cheap substring gate
    before json.loads, and skip the '<synthetic>' sentinel Claude Code writes
    for locally generated rows."""
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()  # drop the partial line the seek landed in
            lines = fh.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"model"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message")
        if not isinstance(msg, dict):
            continue
        model = msg.get("model")
        if model and model != "<synthetic>":
            return model
    return None

def agy_bin():
    """Reuse agy-bridge's binary probe instead of writing a third copy of it."""
    bridge = os.path.join(REPO_ROOT, ".agent", "skills", "agy-bridge")
    if os.path.isdir(bridge):
        sys.path.insert(0, bridge)
        try:
            import run as agy_run  # noqa: F401  (module-level work is import-safe)
            return agy_run.resolve_agy_bin()
        except Exception:
            pass
        finally:
            if sys.path and sys.path[0] == bridge:
                sys.path.pop(0)
    for p in (os.path.expanduser("~/.local/bin/agy"), "/usr/local/bin/agy", "/usr/bin/agy"):
        if os.path.exists(p):
            return p
    return None

def agy_available(binpath):
    return bool(binpath) and os.path.exists(os.path.expanduser("~/.gemini/antigravity-cli")) \
        and (os.path.exists(binpath) or os.path.sep not in binpath)

def model_from_agy(binpath):
    """`agy models` lists the AVAILABLE ids, not the selected one, so the first
    entry is a best effort and AGY_MODEL overrides it. The call can hang when the
    CLI wants an auth handshake, hence a hard timeout and a silent give-up.
    subprocess timeout rather than the `timeout` binary, which macOS lacks."""
    override = os.environ.get("AGY_MODEL") or os.environ.get("ANTIGRAVITY_MODEL")
    if override:
        return override
    if not binpath:
        return None
    try:
        out = subprocess.run([binpath, "models"], capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    for line in (out.stdout or "").splitlines():
        tok = line.strip().lstrip("*-• ").split()
        if not tok:
            continue
        cand = tok[0]
        if "-" in cand and " " not in cand and not cand.endswith(":"):
            return cand
    return None

def has_token(env_key):
    if (os.environ.get(env_key) or "").strip():
        return True
    tok = os.path.join(REPO_ROOT, ".agent", "skills", "agy-bridge", "token.env")
    try:
        with open(tok, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(env_key + "="):
                    return bool(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return False

binpath = agy_bin()
have_agy = agy_available(binpath)

model = None
if RUNTIME == "claude-code":
    path = claude_transcript()
    if path:
        model = model_from_transcript(path)
elif RUNTIME == "antigravity" or (RUNTIME == "unknown" and have_agy):
    model = model_from_agy(binpath)

backends = []
if have_agy:
    backends.append("agy")
if has_token("ZAI_API_TOKEN"):
    backends.append("zai")
if has_token("KIMI_CODE_TOKEN"):
    backends.append("kimi")

# An unresolved runtime that clearly has Antigravity installed IS antigravity.
# This is evidence, not a guess: the CLI dir plus the binary plus a model id.
runtime_out = RUNTIME
if RUNTIME == "unknown" and have_agy and model:
    runtime_out = "antigravity"

print(runtime_out)
print(model or "unknown")
print(tier_of(model))
print(",".join(backends))
PY
)"
  if [ -n "$_probe" ]; then
    RUNTIME="$(printf '%s\n' "$_probe" | sed -n 1p)"
    MODEL="$(printf '%s\n' "$_probe"  | sed -n 2p)"
    TIER="$(printf '%s\n' "$_probe"   | sed -n 3p)"
    BACKENDS="$(printf '%s\n' "$_probe" | sed -n 4p)"
  fi
fi

echo "RUNTIME=$RUNTIME"
echo "ENTRYPOINT=$ENTRYPOINT"
echo "MODEL=$MODEL"
echo "TIER=$TIER"
echo "SUBAGENTS=$SUBAGENTS"
echo "HOOKS=$HOOKS"
echo "BACKENDS=$BACKENDS"
