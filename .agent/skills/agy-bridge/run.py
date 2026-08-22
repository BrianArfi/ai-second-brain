#!/usr/bin/env python3
"""agy-bridge: call non-Claude models (Gemini / GPT-OSS via the Antigravity `agy` CLI,
GLM via the z.ai GLM Coding Plan) as a co-processor for a Claude Code agent, and PROVE
the cost savings.

Why this exists: Claude Code subagents can only run Claude tiers in `model:` frontmatter,
and pointing the whole session at z.ai would turn every call into GLM. This bridge calls
each backend in a scoped subprocess/request, so the main session stays on real Claude.

Three layers:
  1. COST TELEMETRY (primary): every attempt is logged with tokens + latency + per-Mtok
     cost + the Claude counterfactual (what the same work would cost on the task's
     claude_fallback tier). Powers `--report` and the localhost:3737 "Cost / Savings" tab.
  2. CAPABILITY routing: a task resolves a capability (bulk-cheap / reasoning /
     cross-lineage / long-context) -> ordered candidate models in models.json.
  3. TIME routing (measured, phased): `time_routing` off|advisory|on. 'advisory' logs the
     reorder it WOULD do (unverified peak_wib seed) but applies nothing; secondary to 'on' after
     `--analyze` confirms peak windows from the SAME telemetry log.

Cost contract: subscription backends are NOT free. Every model has a per-Mtok rate in
models.json `model_prices`; cost = tokens x rate for ALL backends.
Fallback contract: exit 0 = stdout is the model answer; exit 3 = stdout is a
{"status":"fallback_to_claude","claude_fallback":"<tier>"} sentinel the caller MUST honor.

Usage:
  run.py --task harvest --prompt-file x.txt        # run, logging cost
  run.py --task critic  --prompt "..."             # cross-model critic
  run.py --task research --model glm-5.2 --backend zai --prompt "..."   # force one model
  run.py --task harvest --list                     # show resolved (+reordered) chain
  run.py --report                                  # cost / savings summary by task
  run.py --analyze                                 # latency + error rate per backend x WIB hour
  run.py --doctor                                  # auth, prices, chains, routing status
  run.py --setup [--write]                         # what is connected + the fix for each gap

Backends are config, not code: `type` (cli | anthropic-compatible | openai-compatible),
`base_url`, and either `token_env` or `no_auth`. Credentials resolve through the shared
harness ladder (environment -> workspace .env/secrets.env -> the skill's token.env), so a
key pasted once is visible to every skill.

models.json ships neutral defaults; models.local.json (gitignored) holds what belongs to ONE
install -- subscriptions, measured peak_wib, known_agy_models -- and deep-merges over it.
"""
import argparse
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "models.json")
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

# Shared credential ladder (environment -> workspace .env/secrets.env -> a skill's
# own token file). Named harness_secrets so it cannot shadow the stdlib `secrets`.
sys.path.append(os.path.join(REPO_ROOT, ".agent", "scripts"))
import harness_secrets  # noqa: E402
DATA_DIR = os.environ.get("AGY_BRIDGE_DATA_DIR", os.path.join(REPO_ROOT, "dashboard-data"))
LOG_PATH = os.path.join(DATA_DIR, "agy_usage_log.jsonl")
SUMMARY_PATH = os.path.join(DATA_DIR, "agy_cost_summary.json")
WIB = timezone(timedelta(hours=7))

def resolve_agy_bin():
    """Locate the `agy` CLI. cron runs with a minimal PATH that omits
    ~/.local/bin, so shutil.which alone returns None under cron even though the
    binary is installed -- probe the common user install dirs as a fallback."""
    found = shutil.which("agy")
    if found:
        return found
    for p in (os.path.expanduser("~/.local/bin/agy"),
              "/usr/local/bin/agy", "/usr/bin/agy"):
        if os.path.exists(p):
            return p
    return "agy"  # last resort; run_agy handles the FileNotFoundError gracefully

AGY_BIN = resolve_agy_bin()

AUTH_MARKERS = (
    "authentication required", "please sign in", "please visit the url to log in",
    "authentication timed out", "waiting for authentication",
)
UNAVAIL_MARKERS = (
    "model not found", "unknown model", "not available", "unsupported model", "invalid model",
)

# ---------- config + time ----------

LOCAL_CONFIG = os.path.join(HERE, "models.local.json")

def _deep_merge(base, over):
    """Dict keys merge recursively; anything else is replaced outright.

    Lists replace rather than concatenate on purpose: a chain is an ORDER, and a
    local file that wants a different order must be able to state it fully
    instead of having its entries appended to the shipped ones."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def load_config(merge_local=True):
    """models.json holds shipped defaults; models.local.json holds THIS install's
    facts and is gitignored.

    The split exists because the shipped file used to carry one person's account
    data as if it were configuration -- their subscription costs, their quota
    windows measured against their own vendor dashboard, their model ids. A new
    user could not tell which numbers described the software and which described
    someone else, so they left all of it alone and the routing was tuned for a
    machine that was not theirs."""
    with open(CONFIG, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    if merge_local and os.path.exists(LOCAL_CONFIG):
        try:
            with open(LOCAL_CONFIG, "r", encoding="utf-8") as fh:
                cfg = _deep_merge(cfg, json.load(fh))
        except (OSError, ValueError) as e:
            print(f"[agy-bridge] ignoring unreadable models.local.json: {e}", file=sys.stderr)
    return cfg

def wib_now():
    """Return (hour:int, iso_ts:str) in WIB. Honors AGY_BRIDGE_FAKE_WIB_HOUR for tests."""
    try:
        now = datetime.now(WIB)
    except Exception:  # pragma: no cover
        out = subprocess.run(["date", "+%H|%Y-%m-%dT%H:%M:%S"], capture_output=True, text=True,
                             env={**os.environ, "TZ": "Asia/Jakarta"})
        h, _, ts = out.stdout.strip().partition("|")
        return int(h or 0), ts
    hour = now.hour
    fake = os.environ.get("AGY_BRIDGE_FAKE_WIB_HOUR")
    if fake not in (None, ""):
        hour = int(fake) % 24
    return hour, now.isoformat(timespec="seconds")

# ---------- pricing ----------

def model_price(model, cfg):
    """Return ([in_per_mtok, out_per_mtok], estimated:bool) for a ran model."""
    mp = cfg.get("model_prices", {})
    by_model = mp.get("by_model", {})
    by_family = mp.get("by_family", {})
    est_keys = mp.get("estimated_models", [])
    if model in by_model:
        return by_model[model], (model in est_keys)
    for fam, price in by_family.items():
        if fam.lower() in model.lower():
            return price, (fam in est_keys or model in est_keys)
    return None, True  # unknown -> flagged, treated as 0 cost

def claude_tier_price(tier, cfg):
    tiers = cfg.get("model_prices", {}).get("claude_tiers", {})
    return tiers.get(tier) or tiers.get("main-loop") or [0, 0]

def is_flat_rate(backend, cfg):
    """True for a subscription backend whose marginal actual_usd is $0 (flat monthly fee,
    no per-call quota to burn). Only agy is flagged today; zai/kimi are subscriptions too but
    meter against a real quota (zai_quota_mult, promo windows) so they stay metered here."""
    return bool(cfg.get("backends", {}).get(backend, {}).get("flat_rate", False))

def compute_cost(in_tok, out_tok, ran_model, fallback_tier, backend, cfg):
    """Return dict of actual / counterfactual / saving USD.
    Metered backends: actual_usd = tokens x the ran model's $/Mtok, same as before.
    Flat-rate backends (see is_flat_rate): actual_usd is the true marginal cost, $0 -- the
    per-Mtok equivalent is kept in metered_equiv_usd so the figure isn't lost, and saving_usd
    becomes the full counterfactual (what the same work would have cost on Claude)."""
    ap, est = model_price(ran_model, cfg)
    ap = ap or [0, 0]
    cp = claude_tier_price(fallback_tier, cfg)
    metered = (in_tok * ap[0] + out_tok * ap[1]) / 1_000_000.0
    counter = (in_tok * cp[0] + out_tok * cp[1]) / 1_000_000.0
    flat = is_flat_rate(backend, cfg)
    actual = 0.0 if flat else metered
    return {
        "actual_usd": round(actual, 6),
        "counterfactual_usd": round(counter, 6),
        "saving_usd": round(counter - actual, 6),
        "price_estimated": est,
        "cost_model": "subscription_flat" if flat else "metered",
        "metered_equiv_usd": round(metered, 6),
    }

# ---------- chain resolution + time routing ----------

def local_router_up(spec, timeout=0.15):
    """Cheap TCP connect, not an HTTP round trip. A router bound to loopback
    answers in microseconds or not at all, so this stays inside the 'fast check'
    budget while still telling the truth about a daemon that is not running."""
    try:
        parts = urllib.parse.urlsplit(spec.get("base_url") or "")
        host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
        if not host:
            return False
        with socket.create_connection((host, port), timeout):
            return True
    except Exception:
        return False

def no_backends_configured(cfg):
    """Fast, no-network check: is there ANY usable backend at all? Used to short-circuit
    straight to the claude_fallback sentinel for Claude-only users instead of walking (and
    failing) the whole chain per-backend.

    Enumerates the config rather than naming backends, so a backend added to
    models.json counts immediately. The old version hard-coded zai and kimi, which
    meant a user whose only credential was GEMINI_API_KEY was still told there were
    no backends and was routed to Claude without the bridge ever being tried."""
    if shutil.which("agy") is not None or os.path.exists(AGY_BIN):
        return False
    for name, spec in (cfg.get("backends") or {}).items():
        if spec.get("retired"):
            continue
        if spec.get("no_auth"):
            # Credential-free, so the only question is whether it is running.
            if local_router_up(spec):
                return False
            continue
        if load_token(cfg, name):
            return False
    return True

def resolve_task(cfg, task):
    tasks = cfg.get("tasks", {})
    if task not in tasks:
        sys.stderr.write(f"[agy-bridge] unknown task '{task}'. Known: {', '.join(tasks)}\n")
        sys.exit(2)
    return tasks[task]

def chain_for_task(spec, cfg):
    """Explicit chain wins (back-compat); else resolve from the task's capability."""
    if spec.get("chain"):
        return list(spec["chain"])
    cap = spec.get("capability")
    caps = cfg.get("capabilities", {})
    entry = caps.get(cap)
    if isinstance(entry, list):
        return list(entry)
    sys.stderr.write(f"[agy-bridge] task has no chain and capability '{cap}' is not a list\n")
    sys.exit(2)

def normalize_entry(entry):
    if isinstance(entry, dict):
        return entry.get("backend", "agy"), entry["model"]
    return "agy", entry

def in_peak(backend, hour, cfg):
    for lo, hi in cfg.get("peak_wib", {}).get(backend, []):
        if lo <= hour < hi:
            return True
    return False

def zai_quota_mult(backend, hour, ts, cfg):
    """z.ai GLM quota multiplier: 3x in peak, else 1x (promo through promo_until) / 2x after."""
    if backend != "zai":
        return 1
    q = cfg.get("backends", {}).get("zai", {}).get("quota", {})
    if not q:
        return 1
    if in_peak("zai", hour, cfg):
        return q.get("peak_mult", 3)
    date_str = (ts or "")[:10]
    promo = q.get("promo_until", "")
    if promo and date_str and date_str <= promo:
        return q.get("offpeak_mult", 1)
    return q.get("offpeak_mult_after_promo", 2)

def apply_time_routing(chain, cfg, mode, hour):
    """Stable-sort so off-peak backends float to the front. Returns (chain_to_run, note)."""
    if mode == "off":
        return chain, None
    decorated = []
    for i, entry in enumerate(chain):
        backend, model = normalize_entry(entry)
        decorated.append((1 if in_peak(backend, hour, cfg) else 0, i, entry, backend, model))
    reordered = [d[2] for d in sorted(decorated, key=lambda d: (d[0], d[1]))]
    peaked = [f"{m}[{b}]" for p, _, _, b, m in decorated if p]
    if not peaked or reordered == chain:
        note = None
    else:
        order = " -> ".join(f"{m}[{b}]" for b, m in (normalize_entry(e) for e in reordered))
        verb = "would-run (advisory, not applied)" if mode == "advisory" else "reordered"
        note = f"WIB {hour}h: in-peak demoted [{', '.join(peaked)}]; {verb}: {order}"
    if mode == "advisory":
        return chain, note  # log only, apply nothing
    return reordered, note  # "on"

# ---------- backends ----------

def _token_files(spec):
    """This skill's own token.env, kept as the LAST rung so an existing install
    keeps working after credentials moved to the shared workspace files."""
    tf = spec.get("token_file")
    if not tf:
        return []
    return [tf if os.path.isabs(tf) else os.path.join(HERE, tf)]

def load_token(cfg, backend):
    """Environment, then the workspace .env / secrets.env, then this skill's
    token.env.

    Shared with the meeting recorder through .agent/scripts/harness_secrets.py:
    a key pasted once is visible to every skill. Before this, agy-bridge looked
    only inside its own folder, so a user who put GEMINI_API_KEY in .env to get
    transcription working found the bridge still could not see it."""
    spec = cfg.get("backends", {}).get(backend, {})
    env = spec.get("token_env")
    if not env:
        return None
    return harness_secrets.load_secret(env, extra_files=_token_files(spec))

def token_source(cfg, backend):
    """Which file supplied it, for --doctor / --setup."""
    spec = cfg.get("backends", {}).get(backend, {})
    env = spec.get("token_env")
    if not env:
        return None
    return harness_secrets.where_found(env, extra_files=_token_files(spec))

def run_agy(model, prompt, timeout, known):
    """Returns (ok, text, reason, meta). agy has no usage field -> tokens estimated."""
    meta = {"latency_ms": 0, "in_tok": 0, "out_tok": 0, "tokens_estimated": True}
    if known and model not in known:
        return False, "unknown-id (not in known_agy_models)", "unknown-id", meta
    cmd = [AGY_BIN, "-p", prompt, "--model", model, "--print-timeout", f"{timeout}s", "--sandbox"]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return False, f"timeout after {timeout}s", "timeout", meta
    except (FileNotFoundError, OSError) as e:
        # agy CLI missing/unrunnable (e.g. not on cron PATH). Degrade to the next
        # backend + claude_fallback instead of crashing the whole bridge.
        meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return False, f"agy CLI unavailable: {e}", "unavailable", meta
    meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
    out = (proc.stdout or "").strip()
    low = (out + "\n" + (proc.stderr or "")).lower()
    if any(m in low for m in AUTH_MARKERS):
        return False, "transient auth blip", "auth", meta
    if any(m in low for m in UNAVAIL_MARKERS):
        return False, "model unavailable", "unavailable", meta
    if proc.returncode != 0 and not out:
        return False, (proc.stderr or "non-zero exit").strip(), "error", meta
    if not out:
        return False, "empty output", "empty", meta
    meta["in_tok"] = max(1, len(prompt) // 4)   # estimate
    meta["out_tok"] = max(1, len(out) // 4)
    return True, out, "ok", meta

def run_anthropic_compatible(backend, model, prompt, timeout, cfg):
    """Generic Anthropic-compatible caller (z.ai GLM, Moonshot/Kimi, ...).
    Returns (ok, text, reason, meta). Real servers return EXACT usage."""
    meta = {"latency_ms": 0, "in_tok": 0, "out_tok": 0, "tokens_estimated": False}
    spec = cfg.get("backends", {}).get(backend, {})
    base = (spec.get("base_url") or "").rstrip("/")
    token = load_token(cfg, backend)
    hint = spec.get("credential_hint", f"set {spec.get('token_env','TOKEN')} or token.env")
    if not base:
        return False, f"no {backend} base_url", "error", meta
    if not token:
        return False, f"no {backend} token ({hint})", "no-credential", meta
    body = json.dumps({
        "model": model,
        "max_tokens": int(spec.get("max_tokens", 2048)),
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(base + "/v1/messages", data=body, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("x-api-key", token)
    req.add_header("authorization", f"Bearer {token}")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        if e.code in (401, 403):
            return False, f"{backend} auth {e.code}: {detail}", "auth", meta
        if e.code in (400, 404):
            return False, f"{backend} model/request {e.code}: {detail}", "unavailable", meta
        return False, f"{backend} http {e.code}: {detail}", "error", meta
    except Exception as e:
        meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return False, f"{backend} request failed: {e}", "error", meta
    meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
    parts = data.get("content") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    usage = data.get("usage") or {}
    meta["in_tok"] = int(usage.get("input_tokens", 0)) or max(1, len(prompt) // 4)
    meta["out_tok"] = int(usage.get("output_tokens", 0)) or max(1, len(text) // 4)
    meta["tokens_estimated"] = not usage
    if not text:
        return False, f"{backend} empty content", "empty", meta
    return True, text, "ok", meta

def run_openai_compatible(backend, model, prompt, timeout, cfg):
    """Generic OpenAI-compatible caller (local routers, vLLM, LM Studio, ...).

    Two differences from the Anthropic path that are not cosmetic:
      - `"stream": false` is mandatory. A local router may stream by default and
        answer with SSE `data:` frames, which json.loads cannot read at all.
      - the credential is optional. A router bound to 127.0.0.1 usually holds the
        upstream subscription itself and wants no token from the caller, so
        `no_auth: true` skips the check rather than failing with 'no token'.

    NOTE: text only. Do not route audio through a proxy without proving it
    forwards the audio -- see meeting-recorder/transcribe.py's verify_provider,
    written after a router silently dropped audio and returned invented text.
    """
    meta = {"latency_ms": 0, "in_tok": 0, "out_tok": 0, "tokens_estimated": False}
    spec = cfg.get("backends", {}).get(backend, {})
    base = (spec.get("base_url") or "").rstrip("/")
    if not base:
        return False, f"no {backend} base_url", "error", meta
    token = load_token(cfg, backend)
    if not token and not spec.get("no_auth"):
        hint = spec.get("credential_hint", f"set {spec.get('token_env','TOKEN')} or token.env")
        return False, f"no {backend} token ({hint})", "no-credential", meta

    body = json.dumps({
        "model": model,
        "max_tokens": int(spec.get("max_tokens", 2048)),
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST")
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        if e.code in (401, 403):
            return False, f"{backend} auth {e.code}: {detail}", "auth", meta
        if e.code in (400, 404):
            return False, f"{backend} model/request {e.code}: {detail}", "unavailable", meta
        return False, f"{backend} http {e.code}: {detail}", "error", meta
    except Exception as e:
        meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return False, f"{backend} request failed: {e}", "error", meta
    meta["latency_ms"] = int((time.monotonic() - t0) * 1000)

    try:
        data = json.loads(raw)
    except ValueError:
        # Streamed anyway despite stream:false. Rebuild the answer from the
        # frames instead of failing, since the content is all there.
        text, usage = "", {}
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                frame = json.loads(payload)
            except ValueError:
                continue
            for ch in frame.get("choices") or []:
                text += (ch.get("delta") or {}).get("content") or ""
            usage = frame.get("usage") or usage
        data = {"choices": [{"message": {"content": text}}], "usage": usage}

    choices = data.get("choices") or []
    msg = (choices[0].get("message") or {}) if choices else {}
    text = (msg.get("content") or "").strip()
    usage = data.get("usage") or {}
    meta["in_tok"] = int(usage.get("prompt_tokens", 0)) or max(1, len(prompt) // 4)
    meta["out_tok"] = int(usage.get("completion_tokens", 0)) or max(1, len(text) // 4)
    meta["tokens_estimated"] = not usage
    if not text:
        return False, f"{backend} empty content", "empty", meta
    return True, text, "ok", meta

def run_entry(backend, model, prompt, timeout, cfg, known):
    if backend == "agy":
        return run_agy(model, prompt, timeout, known)
    btype = cfg.get("backends", {}).get(backend, {}).get("type")
    if backend == "zai" or btype == "anthropic-compatible":
        return run_anthropic_compatible(backend, model, prompt, timeout, cfg)
    if btype == "openai-compatible":
        return run_openai_compatible(backend, model, prompt, timeout, cfg)
    return False, f"unknown backend '{backend}'", "error", {"latency_ms": 0, "in_tok": 0, "out_tok": 0, "tokens_estimated": True}

# ---------- telemetry ----------

def log_call(row):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

def read_log():
    rows = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return rows

def aggregate(rows, cfg):
    """Cost/usage summary from log rows (ok answers only carry tokens/cost).
    Flat-rate backends (is_flat_rate) are reinterpreted at read time from `backend`, not from the
    stored cost_model -- so rows logged before this fix (actual_usd = metered, no cost_model field)
    still roll up correctly instead of showing agy as a per-call loss."""
    by_task, by_model, by_day, by_backend = {}, {}, {}, {}
    totals = {"calls": 0, "answers": 0, "in_tok": 0, "out_tok": 0,
              "actual_usd": 0.0, "counterfactual_usd": 0.0, "saving_usd": 0.0,
              "metered_equiv_usd": 0.0, "claude_quota_saved_usd": 0.0}
    for r in rows:
        if r.get("task") == "probe":
            continue  # probe rows feed --analyze (latency) only, never the cost report
        totals["calls"] += 1
        if not r.get("ok"):
            continue
        totals["answers"] += 1
        backend = r.get("backend", "?")
        flat = is_flat_rate(backend, cfg)
        c = r.get("counterfactual_usd", 0)
        metered = r.get("metered_equiv_usd")
        if metered is None:
            metered = r.get("actual_usd", 0)  # pre-fix rows: actual_usd WAS the metered figure
        a = 0.0 if flat else r.get("actual_usd", metered)
        s = c - a
        t = r.get("task", "?"); m = f"{r.get('model','?')}[{backend}]"; d = (r.get("ts_wib", "")[:10] or "?")
        it, ot = r.get("input_tokens", 0), r.get("output_tokens", 0)
        for bucket, key in ((by_task, t), (by_model, m), (by_day, d), (by_backend, backend)):
            b = bucket.setdefault(key, {"answers": 0, "in_tok": 0, "out_tok": 0, "actual_usd": 0.0,
                                        "counterfactual_usd": 0.0, "saving_usd": 0.0,
                                        "metered_equiv_usd": 0.0, "flat_rate": flat})
            b["answers"] += 1; b["in_tok"] += it; b["out_tok"] += ot
            b["actual_usd"] += a; b["counterfactual_usd"] += c; b["saving_usd"] += s
            b["metered_equiv_usd"] += metered
        totals["in_tok"] += it; totals["out_tok"] += ot
        totals["actual_usd"] += a; totals["counterfactual_usd"] += c; totals["saving_usd"] += s
        totals["metered_equiv_usd"] += metered
        if flat:
            totals["claude_quota_saved_usd"] += c
    for bucket in (by_task, by_model, by_day, by_backend):
        for b in bucket.values():
            for k in ("actual_usd", "counterfactual_usd", "saving_usd", "metered_equiv_usd"):
                b[k] = round(b[k], 4)
            b["saving_pct"] = round(100 * b["saving_usd"] / b["counterfactual_usd"], 1) if b["counterfactual_usd"] else 0.0
    for k in ("actual_usd", "counterfactual_usd", "saving_usd", "metered_equiv_usd", "claude_quota_saved_usd"):
        totals[k] = round(totals[k], 4)
    totals["saving_pct"] = round(100 * totals["saving_usd"] / totals["counterfactual_usd"], 1) if totals["counterfactual_usd"] else 0.0
    return {"totals": totals, "by_task": by_task, "by_model": by_model, "by_day": by_day, "by_backend": by_backend}

def write_summary(cfg):
    summary = aggregate(read_log(), cfg)
    summary["subscriptions"] = {k: v for k, v in cfg.get("subscriptions", {}).items() if not k.startswith("_")}
    _, ts = wib_now()
    summary["generated_wib"] = ts
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary

# ---------- reports ----------

def cmd_report(cfg):
    s = write_summary(cfg)
    t = s["totals"]
    print("=== agy-bridge cost / savings ===")
    print(f"calls={t['calls']} answers={t['answers']}  tokens in/out={t['in_tok']}/{t['out_tok']}")
    print(f"actual ${t['actual_usd']}  vs  Claude-counterfactual ${t['counterfactual_usd']}  ->  SAVED ${t['saving_usd']} ({t['saving_pct']}%)")
    print(f"Claude-quota saved via flat-rate backends: ${t['claude_quota_saved_usd']}  "
          f"(metered-equivalent if billed per-token: ${t['metered_equiv_usd']})")
    print("\nby backend:")
    for k, b in sorted(s["by_backend"].items()):
        label = "flat-rate (subscription, $0/call)" if b["flat_rate"] else "metered"
        print(f"  {k:6s} [{label}]  answers={b['answers']:4d}  actual ${b['actual_usd']:<9} "
              f"counter ${b['counterfactual_usd']:<9} saved ${b['saving_usd']} ({b['saving_pct']}%)"
              + (f"  metered-equiv ${b['metered_equiv_usd']}" if b["flat_rate"] else ""))
    print("\nby task:")
    for k, b in sorted(s["by_task"].items()):
        print(f"  {k:9s} answers={b['answers']:4d}  actual ${b['actual_usd']:<9} counter ${b['counterfactual_usd']:<9} saved ${b['saving_usd']} ({b['saving_pct']}%)")
    print("\nby model:")
    for k, b in sorted(s["by_model"].items(), key=lambda kv: -kv[1]["saving_usd"]):
        flag = " [flat-rate]" if b["flat_rate"] else ""
        print(f"  {k:34s}{flag} answers={b['answers']:4d}  saved ${b['saving_usd']} ({b['saving_pct']}%)")
    subs = s.get("subscriptions", {})
    if subs:
        total = sum(subs.values())
        print(f"\nflat subscriptions (context, not per-call): {subs}  = ${total}/mo")
        print(f"net vs subscriptions this log: saved ${t['saving_usd']} - ${total}/mo fees")
    print(f"\nlog: {LOG_PATH}")

def cmd_analyze(cfg):
    rows = read_log()
    if not rows:
        print("no telemetry yet. Run some --task calls (or probe.py) first.")
        return
    econ = aggregate(rows, cfg)
    backends_seen = sorted({r.get("backend", "?") for r in rows})
    print("=== backend cost model ===")
    for b in backends_seen:
        flat = is_flat_rate(b, cfg)
        label = "flat-rate (subscription, marginal $0/call)" if flat else "metered (per-token)"
        print(f"  {b:6s} {label}")
    t = econ["totals"]
    print(f"\nClaude-quota saved by routing to flat-rate backends: ${t['claude_quota_saved_usd']}  "
          f"(sum of counterfactual_usd for calls that would otherwise have run on Claude)")
    print(f"metered-equivalent of that same flat-rate usage, if it had been billed per-token: "
          f"${t['metered_equiv_usd']}  -- NOT a real cost, kept for reference only")
    cell = {}  # (backend, hour) -> {lat:[], err:int, n:int}
    for r in rows:
        b = r.get("backend", "?"); h = r.get("wib_hour", -1)
        c = cell.setdefault((b, h), {"lat": [], "err": 0, "n": 0})
        c["n"] += 1
        if r.get("ok"):
            c["lat"].append(r.get("latency_ms", 0))
        else:
            c["err"] += 1
    print("=== latency (median ms) + error-rate per backend x WIB hour ===")
    backends = sorted({b for b, _ in cell})
    for b in backends:
        print(f"\n{b}:")
        lats = []
        for h in range(24):
            c = cell.get((b, h))
            if not c:
                continue
            med = int(statistics.median(c["lat"])) if c["lat"] else None
            if med is not None:
                lats.append(med)
            errpct = round(100 * c["err"] / c["n"]) if c["n"] else 0
            print(f"  {h:02d}h  n={c['n']:3d}  median={med if med is not None else '-':>6}ms  err={errpct}%")
        if lats:
            base = statistics.median(lats)
            hot = []
            for h in range(24):
                c = cell.get((b, h))
                if c and c["lat"] and statistics.median(c["lat"]) > 1.5 * base:
                    hot.append(h)
            print(f"  baseline median={int(base)}ms; suggested peak hours (>1.5x): {hot or 'none yet'}")
    print("\n(Once these stabilize, copy peak hours into peak_wib in models.json and set time_routing:'on'.)")

# ---------- setup ----------

def list_remote_models(name, spec, cfg, timeout=6):
    """Ask an OpenAI-compatible backend what its key can actually see.

    Model ids move, especially at the fast-moving vendors, and a stale id in
    models.json fails as an opaque 400. Asking beats guessing."""
    base = (spec.get("base_url") or "").rstrip("/")
    token = load_token(cfg, name)
    if not base or (not token and not spec.get("no_auth")):
        return None
    req = urllib.request.Request(base + "/models")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("id") for m in (data.get("data") or []) if m.get("id")]
    except Exception as e:
        return f"error: {e}"

def refresh_agy_models(cfg, write=False):
    """`agy models` is the only authority on what THIS account can run.

    run.py refuses any id not in known_agy_models, because agy silently routes an
    unknown id to a default. That guard is right, but shipping one person's list
    as the gate means another account's perfectly valid model is rejected with no
    explanation. So the list is refreshed from the CLI rather than assumed."""
    if not (shutil.which("agy") or os.path.exists(AGY_BIN)):
        return None, "agy CLI not installed"
    try:
        out = subprocess.run([AGY_BIN, "models"], capture_output=True, text=True, timeout=60)
    except Exception as e:
        return None, f"agy models failed: {e}"
    if out.returncode != 0:
        return None, f"agy models exited {out.returncode}: {(out.stderr or '')[:200]}"

    # `agy models` prints a header line, then one TAB-separated row per model:
    #   gemini-3.5-flash-high<TAB>Gemini 3.5 Flash (High)
    # run_agy matches on the DISPLAY name, so take the last column. Splitting on
    # whitespace instead would keep the slug and the tab, and writing that back
    # would make every id fail the very guard this list exists to enforce.
    found = []
    for line in (out.stdout or "").splitlines():
        line = line.rstrip()
        if not line or "\t" not in line:
            continue
        display = line.split("\t")[-1].strip()
        if display and "(" in display and ")" in display and len(display) < 80:
            found.append(display)
    if not found:
        return None, "could not parse `agy models` output"

    if write and sorted(found) != sorted(cfg.get("known_agy_models") or []):
        # models.local.json, never the shipped file: this list describes ONE
        # account. Writing it back into models.json is exactly how it became
        # everyone's problem the first time.
        local = {}
        if os.path.exists(LOCAL_CONFIG):
            try:
                with open(LOCAL_CONFIG, encoding="utf-8") as fh:
                    local = json.load(fh)
            except (OSError, ValueError):
                local = {}
        local.setdefault("_comment", "This install's own facts. Gitignored.")
        local["known_agy_models"] = found
        with open(LOCAL_CONFIG, "w", encoding="utf-8") as fh:
            json.dump(local, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        cfg["known_agy_models"] = found
    return found, None

def ensure_known_models(cfg):
    """Populate known_agy_models on first real use.

    The shipped list is empty by design, and the guard in run_agy treats empty as
    'no gate'. That is the safe direction for a new install, but it also means the
    protection against agy silently substituting a default model is off until
    somebody runs --setup. So the first call that actually needs the list asks the
    CLI once and caches it locally, costing about seven seconds exactly once."""
    if cfg.get("known_agy_models"):
        return
    if not (shutil.which("agy") or os.path.exists(AGY_BIN)):
        return
    found, err = refresh_agy_models(cfg, write=True)
    if found:
        print(f"[agy-bridge] learned {len(found)} agy model id(s) for this account "
              f"-> models.local.json", file=sys.stderr)

def setup(cfg, write=False):
    """Say what is connected, and give the single command that fixes each gap.

    --doctor answers "what is the state". This answers "what do I type next",
    which is the question a new user actually has."""
    print("\n=== agy-bridge setup ===\n")
    print("This bridge is OPTIONAL. With nothing configured every call falls back to")
    print("Claude, which is correct but costs more. Each backend below is a cost saver.\n")

    ready = []

    # 1. agy CLI
    has_agy = shutil.which("agy") or os.path.exists(AGY_BIN)
    print("agy CLI (Antigravity subscription)")
    if has_agy:
        models, err = refresh_agy_models(cfg, write=write)
        if models:
            ready.append("agy")
            changed = sorted(models) != sorted(cfg.get("known_agy_models") or [])
            print(f"  connected, {len(models)} model(s) visible")
            if changed:
                print("  known_agy_models in models.json does NOT match this account"
                      + (" -> updated" if write else " -> rerun with --write to fix"))
        else:
            print(f"  installed but not usable: {err}")
            print("  -> run `agy` once interactively to authenticate")
    else:
        print("  not installed -> optional; skip unless you have an Antigravity subscription")
    print()

    # 2. Everything credential-based or credential-free over HTTP
    for name, spec in (cfg.get("backends") or {}).items():
        if name == "agy" or spec.get("retired"):
            continue
        print(f"{name} ({spec.get('type')})")
        if spec.get("no_auth"):
            up = local_router_up(spec)
            print(f"  {'reachable' if up else 'NOT reachable'} at {spec.get('base_url')}")
            if up:
                ready.append(name)
            else:
                print("  -> start it, or ignore this backend")
        else:
            src = token_source(cfg, name)
            env = spec.get("token_env")
            if src:
                ready.append(name)
                print(f"  {env} found in {src}")
            else:
                print(f"  no {env}")
                print(f"  -> {spec.get('credential_hint', 'set ' + str(env))}")
        ids = list_remote_models(name, spec, cfg)
        if isinstance(ids, list) and ids:
            configured = [e.get("model") for cap in cfg.get("capabilities", {}).values()
                          if isinstance(cap, list) for e in cap
                          if isinstance(e, dict) and e.get("backend") == name]
            missing = [m for m in configured if m not in ids]
            print(f"  {len(ids)} model(s) visible; sample: {', '.join(ids[:3])}")
            if missing:
                print(f"  WARNING: configured but not offered by this key: {', '.join(missing)}")
                print("  -> edit capabilities in models.json to an id from the list above")
        print()

    print("Credentials are read from the environment, then:")
    for p in harness_secrets.WORKSPACE_FILES:
        print(f"  {p}{'  (exists)' if os.path.exists(p) else ''}")
    print(f"  {os.path.join(HERE, 'token.env')}  (this skill only)\n")

    if ready:
        print(f"Ready: {', '.join(sorted(set(ready)))}. Confirm routing with --doctor.\n")
    else:
        print("Nothing connected yet -- Claude-only mode, which works fine.")
        print("Quickest upgrade is a free Gemini key, the same one meeting transcription uses:")
        print("  1. https://aistudio.google.com/apikey")
        print(f"  2. echo 'GEMINI_API_KEY=<key>' >> {harness_secrets.WORKSPACE_FILES[0]}\n")
    return 0

# ---------- doctor ----------

def doctor(cfg):
    hour, ts = wib_now()
    mode = cfg.get("time_routing", "off")
    print(f"WIB now: {ts} (hour {hour})   time_routing: {mode}")
    if no_backends_configured(cfg):
        print("\n=== Claude-only mode ===")
        print("No non-Claude backend is configured. This is FULLY SUPPORTED: the bridge is an")
        print("OPTIONAL cost saver, not a dependency. Every caller automatically falls back to")
        print("Claude tiers (haiku/sonnet per capability) via the claude_fallback sentinel.")
        print("To enable a backend later (saves cost, changes nothing about correctness):")
        print("  - agy CLI (Gemini / GPT-OSS via Antigravity): install `agy`, run it once")
        print("    interactively to authenticate.")
        print("  - z.ai GLM Coding Plan: subscribe at https://z.ai/subscribe, set ZAI_API_TOKEN")
        print("    (env var or token.env).")
        print("  - Kimi Code: get a token at kimi.com/code/console, set KIMI_CODE_TOKEN")
        print("    (env var or token.env).")
        print()
    has = shutil.which("agy") or (os.path.exists(AGY_BIN) and AGY_BIN)
    print(f"agy resolved: {AGY_BIN if has else 'NO (not found)'}")
    if has:
        authed = False
        for _ in range(2):
            try:
                p = subprocess.run([AGY_BIN, "-p", "Reply with exactly: PONG", "--print-timeout", "12s"],
                                   capture_output=True, text=True, timeout=25)
                blob = (p.stdout + p.stderr).lower()
                authed = ("pong" in blob) and not any(m in blob for m in ("authentication", "sign in", "oauth", "log in"))
            except subprocess.TimeoutExpired:
                authed = False
            if authed:
                break
        print(f"agy authenticated: {'yes' if authed else 'NO -> run `agy` once interactively'}")
    print(f"zai token: {'present' if load_token(cfg, 'zai') else 'MISSING -> z.ai/subscribe, set ZAI_API_TOKEN or token.env'}")
    print(f"kimi token: {'present' if load_token(cfg, 'kimi') else 'MISSING -> Kimi Code Console (kimi.com/code/console), set KIMI_CODE_TOKEN or token.env'}")
    # Two different questions, and asking the wrong one is misleading. A local
    # credential-free router either runs or does not, so probe it. A keyed cloud
    # endpoint is always "up": probing it unauthenticated returns 403/404 and
    # would print "start it", which is nonsense advice for api.groq.com.
    for name, spec in (cfg.get("backends") or {}).items():
        if spec.get("type") != "openai-compatible" or spec.get("retired"):
            continue
        base = (spec.get("base_url") or "").rstrip("/")
        if spec.get("no_auth"):
            if local_router_up(spec):
                ids = list_remote_models(name, spec, cfg)
                n = len(ids) if isinstance(ids, list) else "?"
                print(f"{name}: reachable at {base} ({n} models)")
            else:
                print(f"{name}: NOT RUNNING at {base} -> start it, or its chain entries will fail")
        else:
            src = token_source(cfg, name)
            env = spec.get("token_env")
            print(f"{name} token: {'present (' + src + ')' if src else 'MISSING -> ' + spec.get('credential_hint', 'set ' + str(env))}")
    known = cfg.get("known_agy_models", [])
    print(f"\nper-backend peak status @WIB {hour}h: ", end="")
    print(", ".join(f"{b}={'PEAK' if in_peak(b, hour, cfg) else 'off'}" for b in cfg.get("peak_wib", {}) if not b.startswith("_")))
    print(f"zai/GLM quota multiplier now: {zai_quota_mult('zai', hour, ts, cfg)}x (3x peak 13-17 WIB, 1x off-peak through Sep)")
    print("\neffective chains (capability -> reordered):")
    for task, spec in cfg.get("tasks", {}).items():
        base = chain_for_task(spec, cfg)
        run_chain, note = apply_time_routing(base, cfg, mode if mode != "off" else "off", hour)
        labels = []
        for entry in run_chain:
            b, m = normalize_entry(entry)
            flag = " [!unknown]" if b == "agy" and known and m not in known else ""
            labels.append(f"{m}[{b}]{flag}")
        print(f"  {task:9s} ({spec.get('capability','-')})  {' -> '.join(labels)}  || claude:{spec['claude_fallback']}")
        if note:
            print(f"             time-routing: {note}")
    log_rows = len(read_log())
    print(f"\ntelemetry log: {LOG_PATH}  ({log_rows} rows)")

# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="agy-bridge: GLM/Gemini/GPT-OSS co-processor + cost telemetry")
    ap.add_argument("--task", choices=["harvest", "critic", "research", "draft"])
    ap.add_argument("--label", help="free-form bucket label for telemetry (e.g. inbox-digest); "
                                     "token_efficiency buckets by this when present, else by --task")
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--model", help="force a single model id")
    # Choices come from models.json, not a literal: a backend added to config was
    # otherwise rejected by argparse before any of it could run.
    ap.add_argument("--backend", choices=sorted(load_config().get("backends", {}) or ["agy"]),
                    help="backend for --model (default agy)")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--list", action="store_true", help="print resolved chain, run nothing")
    ap.add_argument("--no-time-routing", action="store_true", help="ignore time_routing for this run")
    ap.add_argument("--report", action="store_true", help="print cost/savings summary")
    ap.add_argument("--analyze", action="store_true", help="print latency/error per backend x WIB hour")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--setup", action="store_true",
                    help="what is connected, and the one command that fixes each gap")
    ap.add_argument("--write", action="store_true",
                    help="with --setup: cache this account's agy model ids to models.local.json")
    args = ap.parse_args()

    cfg = load_config()
    if args.setup:
        sys.exit(setup(cfg, write=args.write))
    if args.doctor:
        return doctor(cfg)
    if args.report:
        return cmd_report(cfg)
    if args.analyze:
        return cmd_analyze(cfg)
    if not args.task:
        ap.error("--task is required (unless --doctor/--report/--analyze)")

    spec = resolve_task(cfg, args.task)
    hour, ts = wib_now()
    mode = "off" if args.no_time_routing else cfg.get("time_routing", "off")
    if not args.list:
        ensure_known_models(cfg)   # one-time, only when the list is still empty

    if not args.model and not args.list and no_backends_configured(cfg):
        # Claude-only mode: no agy CLI, no zai token, no kimi token. Skip the (guaranteed
        # to fail) per-backend chain walk and go straight to the same sentinel the normal
        # fallback path emits, so every caller behaves identically either way.
        sys.stderr.write("[agy-bridge] no non-Claude backends configured, running "
                         "Claude-only; see --doctor\n")
        fallback_tier = spec["claude_fallback"]
        print(json.dumps({"status": "fallback_to_claude", "task": args.task,
                          "claude_fallback": fallback_tier, "tried": []}))
        sys.exit(3)

    if args.model:
        chain = [{"backend": args.backend or "agy", "model": args.model}]
        note = None
    else:
        base = chain_for_task(spec, cfg)
        chain, note = apply_time_routing(base, cfg, mode, hour)

    if note:
        sys.stderr.write(f"[agy-bridge] {note}\n")

    if args.list:
        norm = [f"{m}[{b}]" for b, m in (normalize_entry(e) for e in chain)]
        print(f"task={args.task} capability={spec.get('capability','-')} mode={mode} chain={norm} claude_fallback={spec['claude_fallback']}")
        if note:
            print(f"time-routing: {note}")
        return

    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8") as fh:
            prompt = fh.read()
    elif args.prompt:
        prompt = args.prompt
    else:
        ap.error("provide --prompt or --prompt-file")

    known = cfg.get("known_agy_models", [])
    fallback_tier = spec["claude_fallback"]
    tried = []
    answer = None
    for entry in chain:
        backend, model = normalize_entry(entry)
        ok, text, reason, meta = run_entry(backend, model, prompt, args.timeout, cfg, known)
        if not ok and backend == "agy" and reason == "auth":
            sys.stderr.write(f"[agy-bridge] {model}[agy]: auth blip, retrying once\n")
            ok, text, reason, meta = run_entry(backend, model, prompt, args.timeout, cfg, known)
        cost = compute_cost(meta["in_tok"], meta["out_tok"], model, fallback_tier, backend, cfg) if ok else \
            {"actual_usd": 0, "counterfactual_usd": 0, "saving_usd": 0, "price_estimated": False,
             "cost_model": "subscription_flat" if is_flat_rate(backend, cfg) else "metered", "metered_equiv_usd": 0}
        log_call({
            "ts_wib": ts, "wib_hour": hour, "task": args.task,
            **({"label": args.label} if args.label else {}),
            "backend": backend, "model": model,
            "input_tokens": meta["in_tok"] if ok else 0, "output_tokens": meta["out_tok"] if ok else 0,
            "tokens_estimated": meta["tokens_estimated"], "latency_ms": meta["latency_ms"],
            "ok": ok, "reason": reason, "time_routing": mode,
            "quota_mult": zai_quota_mult(backend, hour, ts, cfg),
            **{k: cost[k] for k in ("actual_usd", "counterfactual_usd", "saving_usd", "price_estimated",
                                     "cost_model", "metered_equiv_usd")},
        })
        tried.append({"backend": backend, "model": model, "ok": ok, "note": None if ok else f"{reason}: {text}"})
        if ok:
            sys.stderr.write(f"[agy-bridge] answered by {model}[{backend}] "
                             f"(in/out {meta['in_tok']}/{meta['out_tok']} tok, saved ${cost['saving_usd']})\n")
            answer = text
            break
        sys.stderr.write(f"[agy-bridge] {model}[{backend}] failed ({reason}); trying next\n")

    write_summary(cfg)
    if answer is not None:
        print(answer)
        return
    print(json.dumps({"status": "fallback_to_claude", "task": args.task,
                      "claude_fallback": fallback_tier, "tried": tried}))
    sys.exit(3)

if __name__ == "__main__":
    main()
