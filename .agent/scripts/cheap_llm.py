#!/usr/bin/env python3
"""cheap_llm.py - one cheap model call with an explicit fallback chain.

For mechanical judgement calls that run on cron and must not burn Claude quota:
"does this evidence answer this ask", "is this MoM line a real commitment".

The default chain, in order (the owner, 26 Sep 2026: Gemini Flash is not always
reliable, so every call needs a backup):

  1. newest Gemini Flash (High)   agy CLI, flat-rate subscription
  2. glm-5.3-flash                z.ai, subscription
  3. claude haiku                 the last resort, run outside the repo

Not every machine has those three. `build_chain()` keeps the default steps a
machine can actually run, then fills the gaps from a pool of cheap backends that
many people already pay for (Gemini API key, Groq, Kimi, local Ollama), cheapest
and most common first, up to three steps. A step with no credential is left out
before any call, so a missing provider costs nothing. Haiku stays last whenever
the claude CLI exists. To pin an exact chain, set `cheap_chain` in
.agent/skills/agy-bridge/models.local.json (gitignored, one per install):

    "cheap_chain": [{"backend": "agy", "model": "latest-flash"},
                    {"backend": "zai", "model": "glm-5.3-flash"},
                    {"backend": "claude", "model": "haiku"}]

`latest-flash` resolves to the newest "Gemini X.Y Flash (<tier>)" in the known
agy models; the tier is `cheap_flash_tier` in the same file, default High.

    python3 .agent/scripts/cheap_llm.py --show-chain   # what this machine will run

A step counts as a success only when it returns text that parses as JSON with
the keys the caller asked for. An unknown model id, an auth blip, a timeout, or
prose instead of JSON all move to the next step. If all three fail the call
returns (None, meta) and the caller must leave the record for a human or the
weekly audit; it never guesses.

Two traps this module exists to avoid, both hit on 26 Sep 2026:
  * agy refuses a model id missing from models.local.json `known_agy_models`,
    and that list was stale, so the harvest lead silently fell through on every
    call. A step that fails with "unknown-id" is logged loudly.
  * `claude -p` run from the repo root reads CLAUDE.md and the session hooks,
    and haiku answered the hook output instead of the prompt. Haiku runs from a
    temp directory for that reason.

    from cheap_llm import ask_json
    obj, meta = ask_json(prompt, required=("verdict", "quote"), label="ledger-autoclose")
    obj, meta = ask_json(prompt, lines=True)   # JSON-lines answers -> list of dicts
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
AGY_RUN = os.path.join(REPO, ".agent", "skills", "agy-bridge", "run.py")
AI_CALL = os.path.join(REPO, ".agent", "scripts", "ai_call.py")
LOG = os.path.join(REPO, "dashboard-data", "cheap_llm_log.jsonl")

DEFAULT_CHAIN = [
    {"backend": "agy", "model": "latest-flash"},
    {"backend": "zai", "model": "glm-5.3-flash"},
    {"backend": "claude", "model": "haiku"},
]

# Fills a gap left by a default step this machine cannot run. Ordered by price and
# by how many people already hold the subscription or key. `latest-flash` on the
# gemini backend resolves to the API id of the newest Flash, e.g. gemini-3.8-flash.
FALLBACK_POOL = [
    {"backend": "gemini", "model": "latest-flash"},
    {"backend": "groq", "model": "openai/gpt-oss-120b"},
    {"backend": "kimi", "model": "kimi-latest"},
    {"backend": "ollama", "model": "env:OLLAMA_MODEL"},
]

MAX_STEPS = 3
_FLASH_RE = re.compile(r"Gemini (\d+)\.(\d+) Flash \(([A-Za-z]+)\)")

def _bridge():
    """agy-bridge's run.py as a module: config, credential ladder, agy binary."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("agy_bridge_run", AGY_RUN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def _latest_flash(cfg, tier):
    """(agy display name, API id) of the newest Gemini Flash at `tier`, or (None, None)."""
    names = list(cfg.get("known_agy_models") or [])
    best = None
    for n in names:
        m = _FLASH_RE.fullmatch(n.strip())
        if m and m.group(3).lower() == tier.lower():
            ver = (int(m.group(1)), int(m.group(2)))
            if best is None or ver > best[0]:
                best = (ver, n.strip())
    if best is None:
        return None, None
    major, minor = best[0]
    return best[1], f"gemini-{major}.{minor}-flash"

def _step(entry, bridge, cfg, tier):
    """One chain entry -> a runnable step, or None when this machine cannot run it."""
    backend, model = entry.get("backend", "agy"), entry.get("model", "")
    if backend == "claude":
        sys.path.insert(0, os.path.dirname(AI_CALL))
        try:
            import ai_call
            have = bool(ai_call.claude_bin())
        except Exception:
            have = False
        return {"name": f"claude-{model}", "claude": model} if have else None
    if backend == "agy":
        if not (bridge.AGY_BIN and os.path.exists(bridge.AGY_BIN)):
            return None
        if model == "latest-flash":
            model, _ = _latest_flash(cfg, tier)
            if not model:
                return None
        return {"name": model, "argv": ["--model", model]}
    spec = (cfg.get("backends") or {}).get(backend)
    if not spec or spec.get("retired"):
        return None
    if spec.get("no_auth") or backend == "ollama":
        if not bridge.local_router_up(spec):
            return None
    elif not bridge.load_token(cfg, backend):
        return None
    if model == "latest-flash":
        _, model = _latest_flash(cfg, tier)
    elif model.startswith("env:"):
        model = os.environ.get(model[4:], "").strip()
    if not model:
        return None
    return {"name": f"{backend}:{model}", "argv": ["--backend", backend, "--model", model]}

def build_chain():
    """The chain this machine can run: pinned `cheap_chain` if set, else the default
    steps that are available, topped up from FALLBACK_POOL, Haiku kept last."""
    try:
        bridge = _bridge()
        cfg = bridge.load_config()
    except Exception as e:
        print(f"[cheap_llm] agy-bridge config unreadable ({e}); using Claude only",
              file=sys.stderr)
        return [{"name": "claude-haiku", "claude": "haiku"}]
    tier = cfg.get("cheap_flash_tier") or "High"
    pinned = cfg.get("cheap_chain")
    if pinned:
        return [s for s in (_step(e, bridge, cfg, tier) for e in pinned) if s]
    steps = [s for s in (_step(e, bridge, cfg, tier) for e in DEFAULT_CHAIN) if s]
    last = steps.pop() if steps and "claude" in steps[-1] else None
    seen = {s["name"] for s in steps}
    for e in FALLBACK_POOL:
        if len(steps) >= MAX_STEPS - (1 if last else 0):
            break
        s = _step(e, bridge, cfg, tier)
        if s and s["name"] not in seen:
            steps.append(s)
            seen.add(s["name"])
    if last:
        steps.append(last)
    return steps

def _strip(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return m.group(1).strip() if m else text

def _parse(text, required, lines):
    body = _strip(text)
    if lines:
        rows = []
        for ln in body.splitlines():
            ln = ln.strip().rstrip(",")
            if not ln or ln in ("[", "]"):
                continue
            try:
                obj = json.loads(ln)
            except ValueError:
                continue
            if isinstance(obj, dict) and all(k in obj for k in required):
                rows.append(obj)
        if not rows:
            # some models return one JSON array instead of lines
            try:
                arr = json.loads(body)
                if isinstance(arr, list):
                    rows = [o for o in arr if isinstance(o, dict) and all(k in o for k in required)]
            except ValueError:
                pass
        return rows or None
    try:
        obj = json.loads(body)
    except ValueError:
        m = re.search(r"\{.*\}", body, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return None
    if not isinstance(obj, dict) or not all(k in obj for k in required):
        return None
    return obj

def _run_step(step, prompt_path, timeout, label):
    if "claude" in step:
        cmd = [sys.executable, AI_CALL, "--model", step["claude"], "--prompt-file", prompt_path,
               "--timeout", str(timeout)]
        cwd = tempfile.gettempdir()
    else:
        cmd = [sys.executable, AGY_RUN, "--task", "harvest", "--label", label,
               "--prompt-file", prompt_path, "--timeout", str(timeout)] + step["argv"]
        cwd = REPO
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout + 60)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if r.returncode != 0:
        note = (r.stdout + r.stderr).strip()[-300:]
        return None, f"rc={r.returncode} {note}"
    return r.stdout, None

def _log(row):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass

def ask_json(prompt, required=(), lines=False, label="cheap-llm", timeout=120, chain=None):
    """Return (parsed, meta). parsed is a dict, a list of dicts when lines=True,
    or None when every step failed. meta = {"model": ..., "tried": [...]}."""
    tried = []
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(prompt)
        prompt_path = fh.name
    try:
        steps = chain or build_chain()
        if not steps:
            _log({"ts": time.time(), "label": label, "model": None, "ok": False,
                  "why": "no cheap backend available on this machine", "secs": 0})
            return None, {"model": None, "tried": [], "why": "no backend available"}
        for step in steps:
            t0 = time.time()
            text, err = _run_step(step, prompt_path, timeout, label)
            parsed = None if err else _parse(text, required, lines)
            ok = parsed is not None
            why = err or (None if ok else "no valid JSON with the required keys")
            if why and "unknown-id" in why:
                print(f"[cheap_llm] {step['name']} is not in known_agy_models; refresh "
                      f".agent/skills/agy-bridge/models.local.json from `agy models`",
                      file=sys.stderr)
            tried.append({"model": step["name"], "ok": ok, "why": why,
                          "secs": round(time.time() - t0, 1)})
            _log({"ts": time.time(), "label": label, "model": step["name"], "ok": ok,
                  "why": (why or "")[:200], "secs": round(time.time() - t0, 1)})
            if ok:
                return parsed, {"model": step["name"], "tried": tried}
        return None, {"model": None, "tried": tried}
    finally:
        try:
            os.unlink(prompt_path)
        except OSError:
            pass

if __name__ == "__main__":
    if "--show-chain" in sys.argv:
        for i, s in enumerate(build_chain(), 1):
            print(f"{i}. {s['name']}")
        sys.exit(0)
    obj, meta = ask_json('Reply with exactly this JSON and nothing else: {"ok": true}',
                         required=("ok",), label="cheap-llm-selftest")
    print(json.dumps({"answer": obj, "meta": meta}, indent=1))
    sys.exit(0 if obj else 1)
