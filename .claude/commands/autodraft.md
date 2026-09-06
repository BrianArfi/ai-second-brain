---
description: Comms - Toggle or inspect auto reply drafts (reply-router) - on/off, per source, status
argument-hint: "on | off | status | on --source gmail"
---

Auto reply drafts (reply-router). Full SOP: `.agent/skills/reply-router/SKILL.md`.

Run the matching CLI and report its output in one line:

```bash
python3 .agent/skills/reply-router/scripts/reply_router.py $ARGUMENTS
```

- No argument or `status` -> `status`.
- `on` / `off` toggles the master switch; add `--source slack|gmail` for one source.
- The change takes effect on the next cron tick (within 5 minutes, WSL host).
- This toggles DRAFTING only. Sending stays approval-gated regardless.
