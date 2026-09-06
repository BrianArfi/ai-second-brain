# Offload mode

> Toggled by asking in words ("offload on", "offload off", "offload status").
> There is no slash command; the flag file below is the whole mechanism.

Toggle **offload mode** on/off. When ON, the Router offloads heavy generation / research /
draft sub-tasks to agy-bridge (local agy CLI, **zero Claude Code quota**); Claude stays the
orchestrator (plans, reviews, applies). When OFF, normal harness routing. Default OFF.

**Backend is Gemini via agy.** z.ai / GLM 5.2 was retired 2026-07-27 when the subscription ended.
The command, the flag file, and the `glm` name are kept as-is so muscle memory and existing cron
entries keep working. Never pass `--backend zai`: it is removed from every chain and live calls
error out.

**States:** `on` · `off` · `status` (read only)

**What to do when invoked:**
1. Resolve the flag file: `.agent/glm_mode.flag`.
2. `on` → write `on`; `off` → write `off`; `status` → read + report.
   ```bash
   echo on  > .agent/glm_mode.flag    # or: echo off
   cat .agent/glm_mode.flag
   ```
3. Confirm the new state to the owner. The SessionStart hook (`glm_mode.py`) surfaces it each session;
   within the current session, honor the new state immediately.

**Behavior when ON:** route bulk reads → `agy-bridge --task harvest`; content/code/copy generation →
`--task draft`; analysis/research → `--task research`; cross-model critique → `--task critic`.
Keep orchestration, final judgment, and client-facing synthesis on Claude (per CLAUDE.md routing).
This is a convenience toggle; the underlying agy-bridge capability routing is unchanged.