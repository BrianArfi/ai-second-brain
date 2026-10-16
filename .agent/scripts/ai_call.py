#!/usr/bin/env python3
"""ai_call.py -- one backend-agnostic way to ask a model for text.

Three places in this harness used to shell out to `claude` directly, each with
its own copy of a `_claude_bin()` that RAISED when the binary was missing:
command_queue.py (triage + worker spawn), dashboard/server.py (/api/ai-task),
and evals/run_behavioral.py. On a machine without Claude Code installed --
which is now a supported configuration, see .agent/scripts/harness_config.py --
those three died instead of degrading, and two of them run on cron.

This module is the single resolver. It does NOT invent a protocol: it reuses the
fallback contract .agent/skills/agy-bridge/run.py already defines and nine
callers already honour.

    exit 0  -> stdout is the answer
    exit 3  -> stdout is {"status":"fallback_to_claude","claude_fallback":"<tier>"}
               and the caller MUST honour it (retry on Claude, or report clearly)

Routing, cheapest correct answer first:
  1. A usable claude binary -> run Claude. This is today's behaviour, byte for
     byte, so a machine that has Claude sees no change at all.
  2. No Claude, agy-bridge present -> run agy-bridge, which walks its own backend
     chain and emits the same sentinel if every backend fails.
  3. Neither -> emit the sentinel ourselves and exit 3. Never a bare exception.

Two shapes, because the callers need two different things:
  plan()  returns the argv to run, for callers that spawn DETACHED workers and
          need to build their own `sh -c` sentinel wrapper.
  run()   executes synchronously and hands back (ok, text, meta).

Usage:
  python3 .agent/scripts/ai_call.py --task ping                  # smoke test
  python3 .agent/scripts/ai_call.py --task harvest --prompt-file x.txt
  python3 .agent/scripts/ai_call.py --task draft --prompt "..." --plan
"""
import argparse
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SCRIPT_DIR = os.path.join(REPO_ROOT, ".agent", "scripts")
AGY_RUN = os.path.join(REPO_ROOT, ".agent", "skills", "agy-bridge", "run.py")
AGY_MODELS = os.path.join(REPO_ROOT, ".agent", "skills", "agy-bridge", "models.json")

# The agy-bridge fallback contract. Do not renumber: nine callers already branch
# on returncode == 3.
FALLBACK_EXIT = 3

# Extra wall-clock a parent must allow on top of `timeout` before killing a child.
# Correct for the Claude backend, which is ONE process making ONE call.
DEFAULT_GRACE = 15

# agy-bridge is not one call. `--timeout` is PER CANDIDATE: run.py's main() walks
# the whole capability chain (models.json capabilities -> N entries), calling
# run_entry once per entry, and retrying an agy entry once more on an auth blip
# (run.py main, the `reason == "auth"` retry). Each agy attempt is
# subprocess.run(..., timeout=timeout + 15) inside run_agy, so the bridge's real
# worst-case wall clock is attempts * (timeout + 15) plus the tail it needs to
# write_summary() and print the exit-3 claude_fallback sentinel.
#
# timeout + DEFAULT_GRACE is therefore NOT enough headroom for the agy route: the
# parent kills the bridge somewhere in the middle of the chain, the sentinel is
# never printed, and every caller's FALLBACK_EXIT branch becomes unreachable while
# a real fallback masquerades as a plain timeout. parent_timeout() below computes
# the real number from the chain instead of guessing a constant.
AGY_INTERNAL_GRACE = 15   # run_agy: subprocess.run(..., timeout=timeout + 15)
AGY_TAIL_MARGIN = 10      # write_summary + printing the sentinel after the last entry
AGY_FALLBACK_ATTEMPTS = 6  # only when models.json is unreadable: the longest chain
                           # today is cross-lineage (4 entries, 2 on the agy backend
                           # -> 4 + 2 retries = 6 attempts)

# Tasks agy-bridge knows (models.json "tasks"). Anything else is mapped below.
AGY_TASKS = ("harvest", "critic", "research", "draft")

# Claude model alias -> agy-bridge task, for callers that think in Claude tiers.
MODEL_TO_TASK = {
    "haiku": "harvest",
    "sonnet": "draft",
    "opus": "research",
    "fable": "research",
}
# The reverse, for callers that think in tasks and need a Claude model. The
# non-agy names mirror the subagent routing table in CLAUDE.md, so a caller can
# pass its own category ('lookup', 'synthesize') and still land on the right tier
# instead of silently defaulting to the cheapest one.
TASK_TO_MODEL = {
    "harvest": "haiku",
    "lookup": "haiku",
    "ping": "haiku",
    "draft": "sonnet",
    "critic": "sonnet",
    "review": "sonnet",
    "research": "opus",
    "synthesize": "opus",
    "strategize": "opus",
}
DEFAULT_TASK = "harvest"
DEFAULT_MODEL = "sonnet"

# `--task ping` with no prompt: the cheapest end-to-end proof that SOME backend
# answers on this machine.
PING_PROMPT = "Reply with exactly the word pong."

# ---------- backend resolution ----------

def claude_bin():
    """Path to a usable claude binary, or None.

    NEVER raises. A machine without Claude is a supported configuration now, so
    the caller routes elsewhere instead of dying.

    Prefers a WSL-native install and refuses the Windows binary on /mnt/c that
    `which claude` returns inside WSL: it reads Windows-side config and reports
    loggedIn:false, so headless auth fails silently rather than loudly. See
    [[reference_headless_claude_auth_gotcha]]. AI_CALL_CLAUDE_BIN overrides the
    whole search for machines that install it somewhere else."""
    override = os.environ.get("AI_CALL_CLAUDE_BIN", "").strip()
    if override:
        return override if os.path.exists(override) else None
    for c in (os.path.expanduser("~/.npm-global/bin/claude"),
              os.path.expanduser("~/.local/bin/claude"),
              "/usr/local/bin/claude"):
        if os.path.exists(c):
            return c
    found = shutil.which("claude")
    if found and not found.startswith("/mnt/"):
        return found
    # Last resort: the binary the VS Code / Cursor extension ships. The WSL host
    # had no standalone install from early October, so every unattended Claude
    # job resolved to agy-bridge and the Saturday ledger audit skipped itself
    # ("tools-unavailable", 10 Oct 2026). The extension binary shares
    # ~/.claude credentials, and its folder name changes on every update, so
    # take the newest one.
    pattern = "anthropic.claude-code-*/resources/native-binary/claude"
    hits = []
    for root in ("~/.vscode-server/extensions", "~/.vscode/extensions",
                 "~/.cursor-server/extensions"):
        hits += glob.glob(os.path.join(os.path.expanduser(root), pattern))
    hits = [h for h in hits if os.access(h, os.X_OK)]
    if hits:
        return max(hits, key=os.path.getmtime)
    return None

def agy_available():
    """True when agy-bridge is worth invoking.

    We only check that the bridge script exists. Whether any individual backend
    is authenticated is the bridge's own question, and it already answers it by
    emitting the fallback sentinel, so probing here would just duplicate that
    logic in a second place that can drift."""
    return os.path.isfile(AGY_RUN)

def agy_attempts(task):
    """Worst-case number of model calls agy-bridge makes for `task`.

    Mirrors run.py: chain_for_task() resolves tasks[task]["chain"] if present, else
    capabilities[tasks[task]["capability"]]; main() then calls run_entry once per
    entry and once MORE for an agy entry that failed with reason 'auth'. So the
    upper bound is len(chain) + (number of agy-backend entries).

    Never raises: an unreadable or restructured models.json falls back to
    AGY_FALLBACK_ATTEMPTS rather than silently pretending the chain is length 1,
    which would put us straight back to an under-sized parent ceiling."""
    try:
        with open(AGY_MODELS, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        spec = (cfg.get("tasks") or {})[task]
        chain = spec.get("chain")
        if not chain:
            chain = (cfg.get("capabilities") or {})[spec["capability"]]
        if not isinstance(chain, list) or not chain:
            return AGY_FALLBACK_ATTEMPTS
        # normalize_entry in run.py: a bare string is the agy backend, a dict names it
        agy = sum(1 for e in chain
                  if not isinstance(e, dict) or e.get("backend", "agy") == "agy")
        return len(chain) + agy
    except Exception:
        return AGY_FALLBACK_ATTEMPTS

def parent_timeout(backend, timeout, task=None):
    """Wall-clock a parent must allow before killing this backend's process.

    Claude is one call, so timeout + DEFAULT_GRACE. agy-bridge walks a chain and
    owns its own per-attempt ceiling, so the parent has to cover every attempt plus
    the tail in which it writes the exit-3 sentinel. Callers that build their own
    `sh -c` wrapper or pass `timeout=` to subprocess MUST use this, never
    `timeout + DEFAULT_GRACE`, or they kill the bridge mid-chain."""
    timeout = int(timeout)
    if backend != "agy":
        return timeout + DEFAULT_GRACE
    n = agy_attempts(task or DEFAULT_TASK)
    return n * (timeout + AGY_INTERNAL_GRACE) + AGY_TAIL_MARGIN

def _configured_backends():
    """Non-Claude backends harness_config detected, or [] if it cannot answer.

    Advisory only: used to write a more specific 'nothing is installed' message,
    never to block a route."""
    try:
        if SCRIPT_DIR not in sys.path:
            sys.path.insert(0, SCRIPT_DIR)
        import harness_config
        return harness_config.backends()
    except Exception:
        return []

def child_env(base=None):
    """Env for a spawned model run: strip the parent Claude-Code session markers
    so the child is not seen as a nested subagent of whatever session started us.
    Everything else (PATH, HOME, tokens) passes through untouched.

    Callers that need a long-lived headless token re-inject it AFTER this call --
    CLAUDE_CODE_OAUTH_TOKEN starts with CLAUDE_CODE_ and is stripped here."""
    env = dict(os.environ if base is None else base)
    for k in list(env):
        if k == "CLAUDECODE" or k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_",
                                              "CLAUDE_EFFORT", "CLAUDE_AUTOCOMPACT")):
            env.pop(k, None)
    return env

def _normalize(task, model):
    """Resolve the (agy task, claude model) pair from whatever the caller gave.

    Callers name whichever side they think in: a Claude model alias, an
    agy-bridge task, a Claude-side category like 'ping' or 'synthesize', or
    nothing. Everything resolves down to one valid pair."""
    if task not in AGY_TASKS:
        if not model and task in TASK_TO_MODEL:
            model = TASK_TO_MODEL[task]
        task = MODEL_TO_TASK.get(model or "", DEFAULT_TASK)
    if not model:
        model = TASK_TO_MODEL.get(task, DEFAULT_MODEL)
    return task, model

# ---------- planning ----------

def plan(prompt, task=None, model=None, output_format=None, allowed_tools=None,
         timeout=180, pin_model=True, require_tools=False):
    """Resolve which backend runs this prompt and with what argv.

    Returns a dict, never raises:
      backend  "claude" | "agy" | "none"
      argv     the command to run ([] when backend is "none")
      model    the claude model alias the caller asked for
      task     the agy-bridge task the model maps to
      reason   short machine-readable code, set whenever backend is "none"
      note     human-readable reason, always set for "none"
      parent_timeout
               seconds a parent must wait before killing this argv. NOT
               timeout + DEFAULT_GRACE under agy: the bridge walks a whole chain at
               `timeout` EACH, so a caller that spawns argv itself has to use this
               number or it kills the bridge before the exit-3 sentinel is printed.
               See parent_timeout().

    Callers that spawn detached workers use argv and build their own sentinel
    wrapper; callers that just want an answer use run() below.

    allowed_tools is a Claude-only concept. Under agy the bridge runs the model
    with --sandbox and NO tool access at all (see run_agy in
    .agent/skills/agy-bridge/run.py, which always appends --sandbox). For a prompt
    that merely wants tools RESTRICTED that is strictly safer, so it is dropped
    silently and the call proceeds.

    require_tools=True secondarys that judgement: it says the prompt cannot be
    satisfied WITHOUT tools, typically because it instructs the model to write its
    output to a file. Dropping allowed_tools there does not make the run safer, it
    makes it impossible, and the model still exits 0 having produced nothing. That
    combination resolves to backend "none" so the caller refuses it at plan time
    instead of dispatching a worker that can only fail silently. There is no
    tool-capable non-Claude backend to fall back to today; when one is added, route
    to it here rather than relaxing this gate.

    pin_model=False omits the --model flag so Claude runs on whatever the user's
    own default is. Callers that are measuring the harness itself (evals) want
    that; callers picking a tier for cost reasons do not."""
    task, model = _normalize(task, model)

    cbin = claude_bin()
    if cbin:
        argv = [cbin, "-p", prompt]
        if pin_model:
            argv += ["--model", model]
        if output_format:
            argv += ["--output-format", output_format]
        if allowed_tools:
            argv += ["--allowedTools", allowed_tools]
        return {"backend": "claude", "argv": argv, "model": model, "task": task,
                "parent_timeout": parent_timeout("claude", timeout, task),
                "note": "claude binary at %s" % cbin}

    if agy_available():
        if require_tools:
            # Loud on purpose. agy-bridge runs every backend with --sandbox and no
            # tool access, so a prompt that must write a file cannot be satisfied
            # here. Dispatching anyway yields exit 0 and an empty result path,
            # which reads as success and alarms nobody.
            return {"backend": "none", "argv": [], "model": model, "task": task,
                    "reason": "tools-unavailable",
                    "parent_timeout": timeout + DEFAULT_GRACE,
                    "note": "task requires tools (%s) but the only backend is "
                            "agy-bridge, which runs sandboxed with no tool access; "
                            "refusing to dispatch a run that cannot produce output"
                            % (allowed_tools or "unspecified")}
        # agy-bridge speaks --task/--prompt and owns its own chain walk; if every
        # backend it knows fails it exits 3 with the sentinel, which is exactly
        # what this function's contract promises anyway.
        argv = [sys.executable, AGY_RUN, "--task", task, "--prompt", prompt,
                "--timeout", str(int(timeout))]
        return {"backend": "agy", "argv": argv, "model": model, "task": task,
                "parent_timeout": parent_timeout("agy", timeout, task),
                "note": "no claude binary; routing via agy-bridge"}

    found = _configured_backends()
    detail = "no claude binary, and no agy-bridge at %s" % AGY_RUN
    if found:
        # Detection says these backends exist but the bridge that drives them is
        # gone, which is a broken install rather than a bare machine. Say which.
        detail += " (harness_config detected: %s)" % ", ".join(found)
    return {"backend": "none", "argv": [], "model": model, "task": task,
            "reason": "no-backend", "parent_timeout": timeout + DEFAULT_GRACE,
            "note": detail}

def sentinel(task, claude_fallback, note="", tried=None):
    """The agy-bridge fallback payload, so every producer of it looks the same."""
    payload = {"status": "fallback_to_claude", "task": task,
               "claude_fallback": claude_fallback, "tried": list(tried or [])}
    if note:
        payload["note"] = note
    return payload

# ---------- execution ----------

def run(prompt, task=None, model=None, timeout=180, output_format=None,
        allowed_tools=None, cwd=None, env=None, pin_model=True,
        grace=DEFAULT_GRACE, require_tools=False):
    """Run one call synchronously. Returns (ok, text, meta); never raises.

    On ok the text is the model's answer (still wrapped when output_format is
    'json' -- unwrapping is the caller's job, same as before). On failure meta
    carries 'reason', and 'fallback' when the backend handed back the
    claude_fallback sentinel.

    `grace` is extra wall-clock on top of `timeout` before we kill the child. It is
    a FLOOR, not the answer: under agy-bridge the real ceiling is the whole chain
    walk (see parent_timeout), and we wait for whichever is larger so the bridge
    always gets to print its exit-3 sentinel. Pass grace=0 when `timeout` must be
    the hard ceiling regardless (evals do, so a scenario cannot run long).

    require_tools=True refuses a tool-less backend outright; see plan()."""
    p = plan(prompt, task=task, model=model, output_format=output_format,
             allowed_tools=allowed_tools, timeout=timeout, pin_model=pin_model,
             require_tools=require_tools)
    meta = {"backend": p["backend"], "model": p["model"], "task": p["task"],
            "note": p["note"], "returncode": None}
    if p["backend"] == "none":
        meta["reason"] = p.get("reason", "no-backend")
        meta["fallback"] = sentinel(p["task"], p["model"], p["note"])
        return False, "", meta

    ceiling = timeout if grace == 0 else max(timeout + grace,
                                             p.get("parent_timeout") or 0)
    meta["ceiling"] = ceiling
    try:
        r = subprocess.run(p["argv"], cwd=cwd or REPO_ROOT, capture_output=True,
                           text=True, timeout=ceiling,
                           env=env if env is not None else child_env())
    except subprocess.TimeoutExpired:
        meta["reason"] = "timeout after %ss (ceiling for backend %s)" % (ceiling, p["backend"])
        return False, "", meta
    except (FileNotFoundError, OSError) as e:
        # The binary vanished between resolution and exec (cron PATH, uninstall).
        # Degrade to the sentinel rather than take the caller down.
        meta["reason"] = "backend unrunnable: %s" % e
        meta["fallback"] = sentinel(p["task"], p["model"], str(e))
        return False, "", meta

    meta["returncode"] = r.returncode
    meta["stderr"] = (r.stderr or "").strip()[-2000:]
    out = (r.stdout or "").strip()
    if r.returncode == FALLBACK_EXIT:
        meta["reason"] = "fallback_to_claude"
        try:
            meta["fallback"] = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            meta["fallback"] = sentinel(p["task"], p["model"], out[:200])
        return False, "", meta
    if r.returncode != 0:
        meta["reason"] = "exit %s" % r.returncode
        return False, out, meta
    if not out:
        meta["reason"] = "empty output"
        return False, "", meta
    return True, out, meta

# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(
        description="Backend-agnostic AI call: Claude when present, agy-bridge otherwise.")
    ap.add_argument("--task", default="ping",
                    help="agy-bridge task (harvest/critic/research/draft) or 'ping'")
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--model", help="claude model alias (haiku/sonnet/opus)")
    ap.add_argument("--output-format", help="passed to claude only")
    ap.add_argument("--allowed-tools", help="passed to claude only")
    ap.add_argument("--require-tools", action="store_true",
                    help="the prompt cannot be satisfied without tools (e.g. it must "
                         "write a file); refuse a sandboxed tool-less backend")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--plan", action="store_true",
                    help="print the resolved backend + argv, run nothing")
    args = ap.parse_args()

    if args.prompt_file:
        try:
            with open(args.prompt_file, "r", encoding="utf-8") as fh:
                prompt = fh.read()
        except OSError as e:
            sys.stderr.write("[ai-call] cannot read --prompt-file: %s\n" % e)
            return 2
    elif args.prompt:
        prompt = args.prompt
    elif args.task == "ping":
        prompt = PING_PROMPT
    else:
        ap.error("provide --prompt or --prompt-file")

    if args.plan:
        p = plan(prompt, task=args.task, model=args.model,
                 output_format=args.output_format, allowed_tools=args.allowed_tools,
                 timeout=args.timeout, require_tools=args.require_tools)
        print("backend=%s task=%s model=%s parent_timeout=%ss"
              % (p["backend"], p["task"], p["model"], p.get("parent_timeout")))
        print("note=%s" % p["note"])
        print("argv=%s" % (shlex.join(p["argv"]) if p["argv"] else "(none)"))
        return 0

    ok, text, meta = run(prompt, task=args.task, model=args.model,
                         timeout=args.timeout, output_format=args.output_format,
                         allowed_tools=args.allowed_tools,
                         require_tools=args.require_tools)
    if ok:
        sys.stderr.write("[ai-call] answered by %s (%s)\n" % (meta["backend"], meta["model"]))
        print(text)
        return 0

    fb = meta.get("fallback") or sentinel(meta["task"], meta["model"],
                                          meta.get("reason", "call failed"))
    sys.stderr.write("[ai-call] %s backend failed: %s\n"
                     % (meta["backend"], meta.get("reason", "unknown")))
    print(json.dumps(fb))
    return FALLBACK_EXIT

if __name__ == "__main__":
    sys.exit(main())
