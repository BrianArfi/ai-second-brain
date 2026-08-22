---
name: meetbot
description: The Rust meeting-recorder that replaced the Vexa Docker stack - how to run it, send bots, check health, roll back, and the landmines that cost hours to find. Use whenever a meeting was not recorded, the bot misbehaved in a call, or the recorder needs restarting/rolling back.
---

## Where the code lives

Source is tracked in the repo at `meetbot/`. The live deployment at `~/tools/meetbot`
**symlinks** `src/`, `assets/`, `spike/` and `Cargo.*` back to it, so there is one source
of truth and no drift. Only the runtime lives in the deployment dir and stays out of git:
`bin/` (binaries), `data/` (SQLite + the signed-in Chrome profile), `config.toml` (holds
the admin token), `target/`. `meetbot/config.example.toml` is the scrubbed template.

Note: these paths are WSL-host-specific. `~/tools/meetbot` and
`.` do not exist on macOS; meetbot
runs on the WSL automation host only.

## What this is

`meetbot` is a single Rust binary at `~/tools/meetbot` that joins the owner's
Google Meet / Teams meetings as a bot, captures call audio, sends it to the external
whisper.cpp server on the Windows host, and serves transcripts over an HTTP API that is
a **drop-in replacement for vexa-lite**.

It replaced a 1.25 GiB always-on Docker stack (vexa-lite + postgres + minio) with a
~3 MB idle process. The Python client `meeting-recorder/vexa_bots.py` drives both, and
switches between them on one env var, so rollback is one crontab edit.

| | vexa (rollback) | meetbot (live) |
|---|---|---|
| Runs as | 3 Docker containers | `systemd --user` unit `meetbot.service` |
| API | `:8056` | `:8060` |
| Store | postgres + minio | SQLite `data/meetbot.db` |
| Idle RAM | ~1.25 GiB | ~3 MB |

## Running it

```bash
systemctl --user status meetbot.service          # is it up
systemctl --user restart meetbot.service         # the standard remedy
journalctl --user -u meetbot.service -f          # watch a live join
curl -s http://localhost:8060/                   # liveness (200 = alive)
curl -s http://localhost:3737/api/vexa-health | python3 -m json.tool   # full health
```

Deploying a rebuild — the service holds the binary open, so `cp` fails with
`Text file busy`. Move it aside first:

```bash
export PATH="$HOME/.cargo/bin:$PATH"          # cargo is NOT on the default PATH
cd ~/tools/meetbot
cargo build --release && cargo test && cargo clippy --all-targets
cd bin && mv meetbot-live meetbot-live.bak-$(date +%s) && cp ../target/release/meetbot meetbot-live
systemctl --user restart meetbot.service
```

The service deliberately runs `bin/meetbot-live`, a **snapshot copy**, not
`target/release/meetbot` — so an in-progress rebuild can never be picked up by a restart
mid-meeting.

## Sending a bot by hand

```bash
cd .
MEETBOT=1 VEXA_API_BASE=http://localhost:8060 python3 meeting-recorder/vexa_bots.py \
  send --meet "https://meet.google.com/xxx-yyyy-zzz" --title "Meeting name"

# what would the cron cycle do right now, without doing it:
MEETBOT=1 VEXA_API_BASE=http://localhost:8060 python3 meeting-recorder/vexa_bots.py auto --dry-run

# diagnose a join failure stage-by-stage against a real meeting:
cd ~/tools/meetbot && ./bin/meetbot-live doctor <meet-code>
```

`doctor` is the maintenance tool: it dry-runs the whole join dance and names the exact
selector that failed. Reach for it first when Google changes the Meet UI.

## Cutover and rollback

Cutover is the `MEETBOT=1 VEXA_API_BASE=...` prefix on crontab line 29. Rollback is
removing it. **Back up the crontab before editing it** — see
[[feedback_crontab_edit_safety]].

```bash
crontab -l > /tmp/crontab.backup-$(date +%F-%H%M)     # always
crontab -l | grep "vexa_bots.py auto"                  # verify after
```

Vexa's containers are kept **warm, not deleted**, because a cold rollback is not a
rollback. If they are `Exited`, `docker start vexa-postgres vexa-lite` restores it.

## Landmines (each of these cost real debugging time)

**Google refuses automated browsers before the green room.** Two signals are
independently fatal: `navigator.webdriver === true` (chromiumoxide appends
`--enable-automation` from its own DEFAULT_ARGS, so `--disable-blink-features=AutomationControlled`
is mandatory) and a `HeadlessChrome` UA token. Symptom is identical for both:
`You can't join this video call`, refused in ~5 s, no name field and no join button at
all. A merely *stale* UA version is survivable — do not chase that.

**Meet reuses the same copy for two different failures.** `You can't join this video
call` means BOTH "refused before the green room" and "your knock was declined". They
have completely different causes. Check whether the log shows a `clicked join` line
before the denial — that is the only thing distinguishing them.

**The bot joins as the owner, so it can eject the owner.** When the same identity is already in
the call, Meet offers `Join here too` AND `Switch here`. `Switch here` MOVES the session
to the bot and drops the owner's — it did exactly that during a live Marketplace scrum on
20 Jul. `JOIN_BUTTON_TEXTS` order is load-bearing: `Join here too` must sit above the
bare `Join` prefix, and `Switch here` must never be in that list.

**`Join here too` is hidden behind a collapsed disclosure.** It lives inside
`Other ways to join`, whose collapsed children have a zero-size bounding box — and the
matcher skips zero-size elements. So the ONLY visible control is the session-stealing
one. Expand the disclosure before searching for a join control. This is why the logs
honestly read "the only control offered was Switch here" while the owner could plainly see
`Join here too` on his screen: both were true, from different vantage points.

**Material icon ligatures are text inside the button.** A control's `textContent` comes
back as `add_to_queueJoin here too`, so prefix matching misses it entirely. Both matchers
strip a leading lowercase/underscore run that butts against a capital (`__delig`). Any
new selector anchored on visible text inherits this hazard.

**A revoked Google session is worse than no session.** When Google invalidates the
stored login server-side, the cookies remain on disk (expiry years out) but Chrome still
sends them, Google rejects them and bounces to `/landing?pli=1` — which kills the guest
path too, so BOTH join routes die at once. Symptom: `clicked join` followed by a
navigation to `landing?...pli=1`. Remedy is re-seeding the profile, not clearing it.
Watch whether a re-seeded session survives more than a day: if it keeps getting revoked,
the copy-profile-per-session pattern is what trips Google's device heuristics, and the
architecture (not the login) is what needs changing.

**The fake camera is visible to everyone.** Chrome runs with
`--use-fake-device-for-media-stream`, whose "camera" is a green test pattern. The bot
turns camera + mic off in the green room before entering; if that ever regresses, the
whole room sees it.

**Recurring meetings share one Meet code.** `vexa_state.json` is keyed by
`platform/code`, so before 20 Jul the first occurrence permanently suppressed every
later one — four of that day's meetings were blocked by records from 8-17 Jul, silently.
Only an occurrence already handled **today** may suppress a join.

**Zero-segment terminal rows are not all the same.** `completed` + 0 segments is a
benign skip (never admitted / nobody spoke). `failed`/`stopped` + 0 segments is a
recorder failure and raises a **fail** heartbeat. The client checks status BEFORE
segment count; reversing that order laundered real losses into green heartbeats.

**cmd_auto reads the Work calendar only** (`gcal --profile work`). Meetings on the owner's
personal calendar are invisible to it — that is not a meetbot bug and vexa behaves the
same.

## Signed-in profile

The bot joins as an invited participant using a persistent Chrome profile at
`data/bot-profile` (config key `profile_template`). Anonymous guests lose: Meet runs a
bot check on the knock itself and auto-declines in ~1.5 s.

Each session gets a **copy** of that profile — Chrome locks a `user-data-dir`, so
concurrent bots sharing one would fight over it, and a session must never be able to
invalidate the stored login. Caches are skipped so the copy stays cheap.

Re-seeding it (when the Google session finally expires) needs a headed browser. WSLg is
available, so this opens a real window on the Windows desktop:

```bash
~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome \
  --user-data-dir=~/tools/meetbot/data/bot-profile \
  --no-first-run --no-default-browser-check --no-sandbox https://accounts.google.com/
```

Sign in with the **Work** account, then close the window. Use the same Chromium binary
meetbot uses — a session seeded in a different browser build gets re-verified by Google.

## Observability

The dashboard probe reads the **crontab** to decide which recorder is in charge, because
both ports answer while vexa is warm and liveness cannot identify the owner. The payload
carries an explicit `backend` field; an undeterminable backend reports `unknown`, never
green. Verify the monitor can go RED (`systemctl --user stop meetbot.service`, re-probe)
after touching it — a monitor that cannot go red is decoration.

`/tmp/vexa_auto.log` gets one line per cron cycle even when nothing happens, so log
staleness is a real signal rather than being indistinguishable from an idle day.
