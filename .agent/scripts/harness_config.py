#!/usr/bin/env python3
"""Resolved harness capabilities: the programmatic consumer of the detect scripts.

detect_platform.sh and detect_runtime.sh both print KEY=VALUE lines, but a shell
script nobody calls is decoration -- detect_platform.sh shipped long ago and every
one of its references is prose telling an agent to run it by hand, while
meeting-recorder/common.py kept a second copy of the same table in Python. This
module is the single place that runs both, caches the answer in .agent/harness.json,
and hands the rest of the harness plain booleans and strings.

DESIGN RULE: callers ask capability questions, not vendor questions.

    import harness_config as hc
    if hc.can_spawn_subagents():   # not: if hc.runtime() == "claude-code"
        ...
    if hc.tier() == "cheap":       # split the job into smaller steps
        ...
    if "agy" in hc.backends():     # delegation is available without subagents
        ...

CACHE RULE: capabilities belong to the PROCESS that detected them, not to the
checkout. The cache is keyed by the writer's runtime signature and a process only
trusts the record filed under its OWN signature, so a cron job or an agy / opencode
run can never read back a Claude Code session's hooks and subagents. Platform is
the one process-independent-ish question and has its own cheap path,
platform_facts(), which never triggers runtime detection.

.agent/harness.json holds one record PER SIGNATURE, not one shared record: a
Claude Code session, a cron tick, and an agy invocation on the same checkout each
get their own slot, so alternating between them never evicts another's answer and
pays a full re-detect (an 8s agy timeout, outside claude-code) on every tick. Only
"extra" (user-set answers detection cannot infer) is checkout-wide and shared
across every record. A pre-existing single-record file from before this format is
read transparently and folded in under its own signature, never discarded.

.agent/harness.json is GITIGNORED and regenerated on demand, same as
.agent/glm_mode.flag. It must never be committed: `update-harness` treats new
tracked files under .agent/ as template files and can overwrite them with
`git checkout --theirs`, which would silently replace a machine's real config
with another machine's.

Usage:
  python3 .agent/scripts/harness_config.py --show          # human readable
  python3 .agent/scripts/harness_config.py --json          # raw config
  python3 .agent/scripts/harness_config.py --refresh       # force re-detect
  python3 .agent/scripts/harness_config.py --set key=value # persist an answer
"""
import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time

SCHEMA = 2          # shape of one per-signature detection record
CACHE_SCHEMA = 1    # shape of the file on disk: {cache_schema, extra, records}
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SCRIPT_DIR = os.path.join(REPO_ROOT, ".agent", "scripts")
CONFIG_PATH = os.path.join(REPO_ROOT, ".agent", "harness.json")

# Re-detect after this long. A session can change model between runs, so the cache
# is a cost guard, not a source of truth; a runtime change is caught by the
# signature check rather than by age. Override for cron with
# HARNESS_CONFIG_TTL (seconds); 0 means always re-detect.
DEFAULT_TTL_SECONDS = 3600

# Returned whenever detection cannot run at all. Every value is the safe answer:
# no delegation, no enforcement, unknown tier, so callers degrade rather than
# call a tool that is not there.
FALLBACK = {
    "schema": SCHEMA,
    "detected_at": 0.0,
    "session_id": "",
    "signature": "",
    "runtime": "unknown",
    "entrypoint": "unknown",
    "model": "unknown",
    "tier": "unknown",
    "subagents": False,
    "hooks": False,
    "backends": [],
    "platform": "unknown",
    "repo_root": REPO_ROOT,
    "run_prefix": "",
    "run_suffix": "",
    "extra": {},
}

def _fallback():
    """A private copy of FALLBACK, mutable members included.

    dict(FALLBACK) is shallow: the `backends` list and the `extra` dict would be
    shared by reference with the module default, so one caller mutating what it
    got back would corrupt the safe answer for the whole process."""
    return copy.deepcopy(FALLBACK)

# Detect-script keys whose value is passed through exactly as printed. Everything
# else is a bare token where surrounding whitespace is noise.
VERBATIM_KEYS = ("RUN_PREFIX", "RUN_SUFFIX")

# ---------- process identity ----------

def _runtime_signature():
    """Identify the PROCESS a cached config belongs to, mostly from env.

    Mirrors section 1 of detect_runtime.sh, which is pure env lookups and costs
    nothing. It exists for one
    reason: capabilities are a property of the process that detected them, not
    of the checkout. A Claude Code session writes runtime=claude-code,
    subagents=yes, hooks=yes into the shared cache, and a cron job or an agy /
    opencode invocation reading that back would believe it has a hook system
    and typed subagents that are absent from its own process. That is the
    silent-degradation class this harness keeps getting burned by, so the
    signature is compared exactly and any difference means re-detect."""
    if os.environ.get("CLAUDECODE") == "1":
        who = "claude-code"
    elif (os.environ.get("CURSOR_TRACE_ID") or os.environ.get("CURSOR_AGENT")
            or os.environ.get("CURSOR_WORKSPACE")):
        who = "cursor"
    elif (os.environ.get("OPENCODE") or os.environ.get("OPENCODE_BIN")
            or os.environ.get("OPENCODE_SESSION")):
        who = "opencode"
    elif os.environ.get("ANTIGRAVITY_SESSION") or os.environ.get("AGY_SESSION"):
        who = "antigravity"
    else:
        # Everything left over, a bare cron tick or an agy invocation that
        # exported no session marker, shares one bucket deliberately.
        # An earlier version split them on os.path.exists("~/.gemini/
        # antigravity-cli"), but that stat is a fact about the MACHINE, not
        # about this process: on any machine with the CLI installed it labels
        # every cron tick "agy", which is just wrong. No per-process signal
        # separates the two, and both resolve to the same capabilities (no
        # subagents, no hooks), so one shared record is the correct answer
        # here rather than a missing feature. Do not reintroduce the split
        # without a signal that actually comes from the running process.
        who = "unknown"
    # The session id rides along so a new Claude Code session, which can be a
    # different model, also invalidates the cache.
    return who + ":" + (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")

# ---------- detection ----------

def _run_detect(script, timeout=30):
    """Run a detect script and parse its KEY=VALUE lines into a dict.

    Returns {} on any failure. A missing or broken detect script degrades the
    config, it does not take the caller down with it."""
    path = os.path.join(SCRIPT_DIR, script)
    if not os.path.isfile(path):
        return {}
    # Resolve bash through PATH explicitly. A bare "bash" on Windows goes through
    # CreateProcess, which searches System32 BEFORE PATH and so finds WSL's
    # bash.exe: the script then runs inside Linux against a Windows path, exits
    # nonzero, and every field of the config silently becomes "unknown". That is
    # why the SessionStart routing hook printed "Session model: unknown" on every
    # Windows session. shutil.which follows PATH order, which finds Git Bash.
    bash = shutil.which("bash") or "bash"
    try:
        out = subprocess.run([bash, path], capture_output=True, text=True,
                             timeout=timeout, cwd=REPO_ROOT)
    except Exception:
        return {}
    if out.returncode != 0:
        return {}
    parsed = {}
    for line in (out.stdout or "").splitlines():
        key, sep, value = line.partition("=")
        if not sep or not key.strip():
            continue
        key = key.strip()
        value = value.rstrip("\r")           # CRLF only, never real content
        # RUN_PREFIX ends in a load-bearing trailing space on Windows
        # ('wsl.exe bash -c "cd <root> && '), so stripping it silently breaks
        # the command detect_platform.sh asked us to build.
        parsed[key] = value if key in VERBATIM_KEYS else value.strip()
    return parsed

_PLATFORM_FACTS = None

def platform_facts():
    """Platform answers only, memoised for the process.

    Split out from detect() because platform and runtime are separate questions
    and only one of them is expensive: detect_platform.sh is pure shell and
    returns in milliseconds, while detect_runtime.sh probes `agy models` behind
    an 8s subprocess timeout whenever the runtime is not claude-code. Anything
    that just wants the OS name must come through here, never through load().

    Deliberately does not read the shared cache either. Native Windows and WSL
    share one checkout and therefore one harness.json, but they are two different
    platforms, so a cached answer can belong to the other one."""
    global _PLATFORM_FACTS
    if _PLATFORM_FACTS is not None:
        return dict(_PLATFORM_FACTS)
    facts = {
        "platform": "unknown",
        "repo_root": REPO_ROOT,
        "run_prefix": "",
        "run_suffix": "",
    }
    plat = _run_detect("detect_platform.sh")
    if plat:
        facts["platform"] = plat.get("PLATFORM") or "unknown"
        facts["repo_root"] = plat.get("REPO_ROOT") or REPO_ROOT
        facts["run_prefix"] = plat.get("RUN_PREFIX") or ""
        facts["run_suffix"] = plat.get("RUN_SUFFIX") or ""
    _PLATFORM_FACTS = facts
    return dict(facts)

def detect(write=True):
    """Run both detect scripts, merge, and (by default) write .agent/harness.json.

    Always returns a complete config dict, never raises."""
    cfg = _fallback()
    cfg["extra"] = dict(_read_container().get("extra") or {})   # user answers survive re-detect
    cfg.update(platform_facts())

    rt = _run_detect("detect_runtime.sh")
    if rt:
        cfg["runtime"] = rt.get("RUNTIME") or "unknown"
        cfg["entrypoint"] = rt.get("ENTRYPOINT") or "unknown"
        cfg["model"] = rt.get("MODEL") or "unknown"
        cfg["tier"] = rt.get("TIER") or "unknown"
        cfg["subagents"] = (rt.get("SUBAGENTS") == "yes")
        cfg["hooks"] = (rt.get("HOOKS") == "yes")
        cfg["backends"] = [b for b in (rt.get("BACKENDS") or "").split(",") if b]

    cfg["detected_at"] = time.time()
    cfg["session_id"] = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    cfg["signature"] = _runtime_signature()
    if write:
        _write_record(cfg)
    return cfg

# ---------- cache ----------
# .agent/harness.json shape: {"cache_schema": CACHE_SCHEMA, "extra": {...},
# "records": {"<signature>": {...one FALLBACK-shaped record, minus "extra"...}}}.
# "extra" is checkout-wide and shared by every signature (it is persisted user
# answers, a property of the checkout, not of the detecting process). "records"
# is keyed per writer signature so a Claude Code session, a cron tick, and an
# agy invocation on the same checkout each keep their own slot instead of one
# shared record where alternating runtimes evict each other every tick.

# Records older than this are dropped on write so the file cannot grow forever:
# Claude Code hands out a fresh session id (and therefore a fresh signature)
# every session. Purely hygiene -- trust is still decided by _is_stale/_mine
# against DEFAULT_TTL_SECONDS / HARNESS_CONFIG_TTL, never by this constant.
RECORD_PRUNE_AGE_SECONDS = 7 * 24 * 3600

def _empty_container():
    return {"cache_schema": CACHE_SCHEMA, "extra": {}, "records": {}}

def _read_container():
    """Read .agent/harness.json as {cache_schema, extra, records}.

    Transparently upgrades a pre-existing single-record file (the format this
    module used before per-signature caching: a flat dict with a top-level
    "signature" key and no "records" key) by folding it in as one record under
    its own signature, so an older harness.json keeps working instead of being
    silently discarded or crashing the reader."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return _empty_container()
    if not isinstance(data, dict):
        return _empty_container()
    if "records" not in data and "signature" in data:
        legacy = dict(data)
        sig = legacy.get("signature") or ""
        extra = legacy.pop("extra", None)
        return {
            "cache_schema": CACHE_SCHEMA,
            "extra": extra if isinstance(extra, dict) else {},
            "records": {sig: legacy} if sig else {},
        }
    extra = data.get("extra")
    records = data.get("records")
    return {
        "cache_schema": data.get("cache_schema", CACHE_SCHEMA),
        "extra": extra if isinstance(extra, dict) else {},
        "records": records if isinstance(records, dict) else {},
    }

def _write_container(container):
    """Write the cache file. A read-only or full disk is not fatal -- the caller
    already has the config it asked for, it just will not be cached."""
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(container, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass

def _write_record(cfg):
    """Merge one process's detection record into the shared container.

    Re-reads the container first so this only touches the caller's own
    signature slot: a concurrent write from a different signature (a cron tick
    firing while a Claude Code session is also mid-detect) is merged in, not
    clobbered, which is the whole point of per-signature records."""
    sig = cfg.get("signature") or ""
    container = _read_container()
    if sig:
        container["records"][sig] = {k: v for k, v in cfg.items() if k != "extra"}
    container["extra"] = dict(cfg.get("extra") or {})
    now = time.time()
    for key, rec in list(container["records"].items()):
        if key == sig:
            continue
        try:
            age = now - float((rec or {}).get("detected_at") or 0)
        except (TypeError, ValueError):
            age = RECORD_PRUNE_AGE_SECONDS + 1
        if age > RECORD_PRUNE_AGE_SECONDS:
            del container["records"][key]
    container["cache_schema"] = CACHE_SCHEMA
    _write_container(container)

def _mine(cfg):
    """True when this process is the kind of process that wrote the cache.

    The old check was `if sid and cfg["session_id"] != sid`, which skipped itself
    entirely whenever the CURRENT process had no CLAUDE_CODE_SESSION_ID -- every
    cron job, every agy or opencode run. Those processes then read a Claude Code
    session's cache back as fresh and believed they had hooks and subagents. An
    absent session is a mismatch, not a free pass. Kept as a schema-validity
    gate even now that load() fetches a record by signature key directly: a
    record filed under the right key but from a stale pre-fix schema still
    should not be trusted blind."""
    return cfg.get("schema") == SCHEMA and cfg.get("signature") == _runtime_signature()

def _is_stale(cfg, max_age):
    # A cached config is only trusted by a process whose own runtime signature
    # matches the one that wrote it. When in doubt re-detect: detection is cheap
    # under claude-code and a wrong capability answer is not recoverable
    # downstream.
    if not _mine(cfg):
        return True
    if max_age <= 0:
        return True
    try:
        return (time.time() - float(cfg.get("detected_at") or 0)) > max_age
    except (TypeError, ValueError):
        return True

def load(max_age=None, refresh=True):
    """Return the resolved config, re-detecting when the cache is stale.

    refresh=False reads the cache only and falls back to safe defaults, for
    hot paths that must not shell out."""
    if max_age is None:
        try:
            max_age = float(os.environ.get("HARNESS_CONFIG_TTL", DEFAULT_TTL_SECONDS))
        except (TypeError, ValueError):
            max_age = DEFAULT_TTL_SECONDS
    container = _read_container()
    sig = _runtime_signature()
    record = container["records"].get(sig)
    cfg = dict(record) if isinstance(record, dict) else None
    if cfg is not None:
        cfg["extra"] = dict(container.get("extra") or {})
    if cfg and not _is_stale(cfg, max_age):
        merged = _fallback()
        merged.update(cfg)
        return merged
    if not refresh:
        merged = _fallback()
        merged["extra"] = dict(container.get("extra") or {})
        if cfg and _mine(cfg):
            # Only age-stale, and it is this process's own record (found under
            # its own signature key): best effort beats no answer on a hot path.
            merged.update(cfg)
        return merged
    return detect()

# ---------- convenience predicates ----------
# Branch on these, not on runtime().

def can_spawn_subagents():
    """True when the runtime can spawn typed subagents. When False, delegation
    goes through agy-bridge and the main loop works inline."""
    return bool(load().get("subagents"))

def has_hooks():
    """True when the runtime has a hook system. When False, Python-side gates
    are the ONLY enforcement layer and must not be skipped."""
    return bool(load().get("hooks"))

def tier():
    """Main-loop strength: flagship | mid | cheap | unknown."""
    return load().get("tier") or "unknown"

def backends():
    """Non-main-loop model backends available for delegation, e.g. ['agy', 'zai']."""
    val = load().get("backends")
    return list(val) if isinstance(val, list) else []

def runtime():
    """Vendor name. For logging and for the two places that genuinely need it
    (transcript path, agy CLI). Do not branch capability on this."""
    return load().get("runtime") or "unknown"

def entrypoint():
    return load().get("entrypoint") or "unknown"

def model():
    return load().get("model") or "unknown"

def platform():
    """macos | wsl | windows | unknown, from detect_platform.sh.

    Goes through platform_facts(), not load(), so asking for the OS name never
    triggers runtime detection."""
    return platform_facts().get("platform") or "unknown"

def repo_root():
    return platform_facts().get("repo_root") or REPO_ROOT

def run_prefix():
    return platform_facts().get("run_prefix") or ""

def run_suffix():
    return platform_facts().get("run_suffix") or ""

def extra(key=None, default=None):
    """Answers that detection cannot infer, persisted by setup. Survives re-detect.

    Returns a copy, same as backends(): the whole-dict form used to hand out the
    live object, so a caller mutating it corrupted the module default."""
    ex = load().get("extra")
    ex = ex if isinstance(ex, dict) else {}
    return dict(ex) if key is None else ex.get(key, default)

def set_extra(key, value):
    """Persist one answer into harness.json without re-running detection."""
    cfg = load()
    ex = cfg.get("extra")
    cfg["extra"] = dict(ex) if isinstance(ex, dict) else {}
    cfg["extra"][key] = value
    _write_record(cfg)
    return cfg

# ---------- CLI ----------

def _show(cfg):
    order = ("runtime", "entrypoint", "model", "tier", "platform",
             "subagents", "hooks", "backends", "repo_root")
    width = max(len(k) for k in order)
    print("Harness config  (%s)" % CONFIG_PATH)
    for key in order:
        val = cfg.get(key)
        if isinstance(val, bool):
            val = "yes" if val else "no"
        elif isinstance(val, list):
            val = ", ".join(val) or "(none)"
        print("  %-*s  %s" % (width, key, val if val not in (None, "") else "(unset)"))
    ex = cfg.get("extra") or {}
    for key in sorted(ex):
        print("  %-*s  %s" % (width, key, ex[key]))
    print("")
    print("  delegation      %s" % ("typed subagents" if cfg.get("subagents")
                                    else ("agy-bridge only" if cfg.get("backends")
                                          else "none, main loop works inline")))
    print("  enforcement     %s" % ("hooks + python gates" if cfg.get("hooks")
                                    else "python gates only"))

def main():
    ap = argparse.ArgumentParser(description="Resolved harness capabilities.")
    ap.add_argument("--show", action="store_true", help="print the resolved config")
    ap.add_argument("--json", action="store_true", help="print the raw config as JSON")
    ap.add_argument("--refresh", action="store_true", help="force re-detection")
    ap.add_argument("--set", metavar="KEY=VALUE", action="append", default=[],
                    help="persist an answer detection cannot infer (repeatable)")
    args = ap.parse_args()

    cfg = detect() if args.refresh else load()
    for pair in args.set:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            print("skipped (expected KEY=VALUE): %s" % pair, file=sys.stderr)
            continue
        cfg = set_extra(key.strip(), value.strip())

    if args.json:
        print(json.dumps(cfg, indent=2, sort_keys=True))
    else:
        _show(cfg)
    return 0

if __name__ == "__main__":
    sys.exit(main())
