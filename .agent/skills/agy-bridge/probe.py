#!/usr/bin/env python3
"""agy-bridge probe (optional): send a tiny prompt to each backend and log latency/throttle
to the SAME telemetry log run.py uses, so `run.py --analyze` has data even for hours the owner
doesn't organically use the bridge. Run it hourly via /loop or the `schedule` skill, e.g.:

    /loop 1h python3 .agent/skills/agy-bridge/probe.py

Rows are logged with task="probe" so they show up in --analyze (latency/error per hour) but
do NOT pollute the cost/savings report, which only counts the real task rows.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run  # noqa: E402  (sibling module)

PROBE_PROMPT = "Reply with exactly one word: PONG"
# One cheap, representative model per backend.
PROBE_TARGETS = [
    # 3.5 Flash is gone from agy (2026-09-16 refresh); 3.8 is the current cheap tier.
    ("agy", "Gemini 3.8 Flash (Low)"),
    ("zai", "glm-5.3-flash"),  # resubscribed + probed OK 2026-09-21
]

def main():
    cfg = run.load_config()
    known = cfg.get("known_agy_models", [])
    hour, ts = run.wib_now()
    for backend, model in PROBE_TARGETS:
        ok, _text, reason, meta = run.run_entry(backend, model, PROBE_PROMPT, 60, cfg, known)
        run.log_call({
            "ts_wib": ts, "wib_hour": hour, "task": "probe", "backend": backend, "model": model,
            "input_tokens": 0, "output_tokens": 0, "tokens_estimated": True,
            "latency_ms": meta["latency_ms"], "ok": ok, "reason": reason, "time_routing": "probe",
            "actual_usd": 0, "counterfactual_usd": 0, "saving_usd": 0, "price_estimated": False,
        })
        sys.stderr.write(f"[probe] {model}[{backend}] @WIB {hour}h: {'ok' if ok else reason} {meta['latency_ms']}ms\n")
    run.write_summary(cfg)

if __name__ == "__main__":
    main()
