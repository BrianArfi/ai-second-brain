#!/usr/bin/env python3
"""cheap_llm.py - one cheap model call with an explicit fallback chain.

For mechanical judgement calls that run on cron and must not burn Claude quota:
"does this evidence answer this ask", "is this MoM line a real commitment".

The chain, in order (the owner, 26 Sep 2026: Gemini Flash is not always reliable, so
every call needs a backup):

  1. Gemini 3.8 Flash (High)   agy CLI, flat-rate subscription
  2. glm-5.3-flash             z.ai, subscription
  3. claude haiku              the last resort, run outside the repo

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

CHAIN = [
    {"name": "gemini-3.8-flash", "argv": ["--model", "Gemini 3.8 Flash (High)"]},
    {"name": "glm-5.3-flash", "argv": ["--backend", "zai", "--model", "glm-5.3-flash"]},
    {"name": "claude-haiku", "argv": None},
]

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
    if step["argv"] is None:
        cmd = [sys.executable, AI_CALL, "--model", "haiku", "--prompt-file", prompt_path,
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
        for step in chain or CHAIN:
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
    obj, meta = ask_json('Reply with exactly this JSON and nothing else: {"ok": true}',
                         required=("ok",), label="cheap-llm-selftest")
    print(json.dumps({"answer": obj, "meta": meta}, indent=1))
    sys.exit(0 if obj else 1)
