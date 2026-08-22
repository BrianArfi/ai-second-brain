# meetbot — Frozen Contract (v1)

Drop-in Rust replacement for the self-hosted **Vexa Lite** API that
`meeting-recorder/vexa_bots.py` talks to. Everything in this document is a hard
contract: builder agents implement their own module only and must match these
signatures byte for byte.

> **Amended v1.2.** This preamble previously read "The Python client is **not**
> being changed." Its *wire* behaviour is not, and every §0 call site below still
> holds byte for byte. But the client also performs vexa-**container**
> maintenance that has no meaning against meetbot and is actively harmful:
> `ensure_env_url()` rewrites `TRANSCRIPTION_SERVICE_URL` in `~/tools/vexa/.env`
> on WSL gateway-IP drift and runs `docker restart vexa-lite`, and
> `ensure_vexa_api()` runs `docker start` when the API is unreachable. meetbot
> resolves the gateway IP itself (`Config::gateway_ip`, §4), so the rewrite is a
> no-op, and the restart perturbs the container that must stay untouched as the
> rollback path. The client therefore gained an additive `meetbot_mode()` gate,
> keyed on an explicit `MEETBOT=1` env var or `VEXA_API_BASE` naming meetbot's
> port, never inferred otherwise. With `MEETBOT` unset and `VEXA_API_BASE` unset
> or on `:8056`, every legacy path runs unchanged. Regression-tested by
> `vexa_bots.py selftest`.

Reference client: `<second-brain>/meeting-recorder/vexa_bots.py`
(575 lines). Every wire behaviour below was extracted from it.

---

## 0. Ground truth: what the Python client actually does

| Client call site | Line | Behaviour meetbot must honor |
| :-- | :-- | :-- |
| `urlopen(API_BASE + "/")` | 103 | Liveness probe. **Any** HTTP response (even 404) means "alive". Only a connection error means down. |
| `POST /bots` | 269, 495 | JSON body, `X-API-Key` header, client timeout 120 s. |
| `DELETE /bots/{platform}/{mid}` | 282 | Stop/leave. Failure is printed, not fatal. |
| `GET /transcripts/{platform}/{mid}` | 325, 526 | Poll + final pull. |
| `req()` error path | 193-194 | ANY non-2xx raises `RuntimeError("… -> HTTP {code}: {body[:300]}")`. The client only ever pattern-matches `"404" in str(e)`. So: **404 is semantically load-bearing; every other error code is opaque**, but the body must be short and human-readable because it lands verbatim in `state["meetings"][kid]["error"]` (truncated to 300 chars) and in a heartbeat. |
| Terminal check | 326, 538 | `data["status"] in ("completed", "failed", "stopped")`. Anything else = still in progress, client polls again next cron tick (every 5 min). |
| Empty-transcript branch | 332-350 | Counts segments with non-blank `text`. If **0 and terminal** → `status = "skipped_not_admitted"`, heartbeat `ok`, no MOM. If 0 and non-terminal → "no segments yet", retry later. |
| Truncation branch | 356 | `status in ("failed","stopped")` **with** segments → transcript is treated as TRUNCATED, MOM gets a ⚠ PARTIAL banner, heartbeat `fail`. |
| Zombie guard | 528-537 | `bot_sent` + HTTP 404 + `sent_at` older than 3 h → local status `failed_not_found`, heartbeat `fail`. So a meeting meetbot has *never heard of* MUST 404, and one it *has* accepted MUST NOT 404 afterwards. |
| Concurrency rejection | 500-509 | `POST /bots` raising (historically **HTTP 403 "maximum concurrent bot limit (3)"**) is recorded as `send_failed` + fail heartbeat. |
| `cmd_setup` | 242-253 | `POST /admin/users` and `POST /admin/users/{uid}/tokens?scopes=bot,tx,browser` with `X-Admin-API-Key`. Reads `id` from the first, `token`\|`api_key`\|`key` from the second. |

### 0.1 THE critical semantic (do not get this wrong)

> A bot that is **never admitted from the waiting room**, or that sits in a
> **completely silent** meeting, must finish with
> `status = "completed"` and `segments = []`.

Not `failed`. Not `stopped`. `completed`.

Rationale: the client classifies `completed + zero segments` as
`skipped_not_admitted` — an *operational skip*, heartbeat **ok**, no alert.
Getting this wrong turns every un-admitted meeting into a false alarm, which is
why the status meetbot writes for a never-admitted bot is load-bearing.

`failed` / `stopped` are reserved for: bot crashed, was kicked mid-call, lost
the browser, or was explicitly stopped via `DELETE /bots/...` **while audio was
already flowing**. In those cases segments captured so far are still returned —
the client wants the partial transcript with a truncation banner.

Decision table meetbot must implement:

| What happened | Final `status` | `segments` |
| :-- | :-- | :-- |
| Joined, audio captured, meeting ended normally | `completed` | all |
| Joined, admitted, nobody ever spoke | `completed` | `[]` |
| Never admitted (waiting-room timeout, `admission_timeout_min`) | `completed` | `[]` |
| Host denied entry | `completed` | `[]` |
| `DELETE /bots/...` while in call with audio | `stopped` | partial |
| `DELETE /bots/...` before admission | `completed` | `[]` |
| Bot removed by host mid-call | `stopped` | partial |
| Browser/CDP crash, join threw, page unusable | `failed` | whatever exists |
| Admitted, audio captured, whisper died mid-call, **zero** segments | `failed` | `[]` |
| Meeting id never POSTed | *(404, no body status)* | — |

> **Corrected during hostile review (v1.2).** The row above is new and it is the
> one exception to "terminal + zero segments means operational skip". Both
> whisper gates are pre-join only (`ensure_whisper` in the client, the 503 in
> `POST /bots`), so an outage that starts *after* admission was invisible: the
> bot sat through a real meeting, `run_transcriber` dropped every utterance by
> design, `classify_exit` saw `MeetingEnded` and wrote `completed`, and the
> client filed a whole meeting as `skipped_not_admitted` with a green heartbeat.
> A meeting disappearing behind an ok heartbeat is strictly worse than a false
> alarm, so `session::drive` now re-probes whisper at finalize when
> **transcription was enabled, audio frames were actually captured, and zero
> segments landed**, and returns `failed` when the drain proves the transcriber
> was broken.
>
> **Corrected again during re-review (v1.2.1).** The first implementation of this
> row used `WhisperClient::health()` as its only discriminator, which is far too
> weak to carry it: per §6.3 `health()` counts **any** HTTP response as up, and
> `run_transcriber` only sets `whisper_down` when its own probe sees a
> *connection* failure. A whisper whose process is alive but whose model fails
> every request — 500 per utterance, or 200 with an empty body — therefore
> probed healthy, reported `whisper_down: false`, and shipped a whole meeting as
> `completed` with `segments: []`: the exact silent loss this row was added to
> stop, reached through a different door. The `loss_ratio` alarm did not fire
> either, because it sat in the `else` arm of the same `if`.
>
> The discriminator is now `session::judge_silence`, a pure function over the
> `TranscriberOutcome` counters, consulted in order of how conclusive each
> signal is:
>
> | Evidence | Verdict |
> | :-- | :-- |
> | drain hit `FINALIZE_BUDGET` | `failed` |
> | worker never reported counters (panic / `Err`) | `failed` |
> | `whisper_down` | `failed` |
> | `lost() > 0` (dropped or abandoned) | `failed` |
> | `seen > 0`, `lost == 0`, `inserted == 0` | **`completed`** — whisper answered every request and every answer was blank or non-speech, so this is a quiet room, proven |
> | `seen == 0` | inconclusive: whisper was never asked anything, so probe `health()` and fail only if it is down |
>
> `lost() > 0` is what closes the hole, and the `seen > 0` row is what keeps the
> probe off the load-bearing path: where whisper has demonstrably answered, a
> transient probe blip must not be able to convert a quiet meeting into a false
> alarm. The `loss_ratio >= 0.25` error log is now an independent `if`, so a
> heavily-lossy session is logged loudly whichever verdict it gets.
>
> The load-bearing path is untouched and deliberately so: **no audio captured**
> (never admitted, denied, stopped in the waiting room) never reaches this
> check, and **audio captured, whisper answering, nobody speaking** still
> finishes `completed` with `[]`. Silence is only reclassified when the
> transcriber is provably broken, never when the room was quiet.

#### 0.1.1 What the client does with a zero-segment terminal row (verified)

The server-side half above only pays off if the client actually distinguishes
the statuses. It did not until v1.3: `cmd_pull` tested the **segment count
before the status**, so *every* zero-segment terminal row — `failed` and
`stopped` included — was laundered into `skipped_not_admitted` with an **ok**
heartbeat. The `failed` verdict that `judge_silence` works so hard to produce
was computed correctly and then discarded one process later, which is precisely
the silent-loss failure this whole section exists to prevent.

`cmd_pull` now checks **status first**. Observed end-to-end against a live
meetbot (`GET /transcripts/...` -> the real `vexa_bots.py cmd_pull`):

| Server payload | `vexa_state.json` status | Heartbeat | Client output |
| :-- | :-- | :-- | :-- |
| `completed`, 0 segments | `skipped_not_admitted` | **ok** | `SKIPPED: ... not admitted / no audio` |
| `failed`, 0 segments | `failed_empty` | **fail** | `FAILED: ... treat as unrecorded, NOT as not-admitted` |
| `stopped`, 0 segments | `failed_empty` | **fail** | `FAILED: ... treat as unrecorded, NOT as not-admitted` |
| `completed`, N>0 segments | `transcribed` / `drafted` | ok | transcript written, MOM drafted |

The first row is the load-bearing one and is unchanged: an un-admitted bot is
still a green operational skip. Rows two and three are the loudness this spec
claims, now actually delivered rather than merely specified.

Note the ordering is the *only* thing that separates these cases — the payloads
differ solely in `status`. Any future edit that reintroduces a segment-count
test ahead of the status test silently re-breaks every row but the first.

---

## 1. HTTP API

Base: `http://127.0.0.1:8060`. All bodies `application/json; charset=utf-8`.

Auth: every non-admin route requires header `X-API-Key: <config.api_key>`.
Admin routes require `X-Admin-API-Key: <config.admin_token>`.
Missing/wrong key → **401** with `{"detail": "..."}`.

`{platform}` path segment is `google_meet` | `teams`. Unknown value → 404.

> **Teams is supported (v1.3).** `teams` is a fully joinable `{platform}`:
> `POST /bots` accepts it, `MeetSession::join` drives the Teams state machine
> off `teams::selectors`, and `DELETE` / `GET /transcripts` work as they always
> did. The v1.2 400 rejection is gone. See §1.3.1.

### 1.1 `GET /` — liveness

`200 OK`

```json
{ "service": "meetbot", "version": "0.1.0", "status": "ok" }
```

No auth (the client probes this before it has done anything).

### 1.2 `GET /health` — dependency health (meetbot extension)

`200 OK` — always 200, even when a dependency is down; read the fields.

```json
{
  "status": "degraded",
  "whisper": { "reachable": false, "endpoint": "http://172.25.32.1:8083/v1/audio/transcriptions" },
  "chromium": { "path": "$HOME/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome", "present": true },
  "active_bots": 1,
  "max_concurrent_bots": 4
}
```

`status` ∈ `"ok" | "degraded"`. `degraded` when whisper is unreachable or
chromium is missing.

### 1.3 `POST /bots` — send a bot

Request (exactly the fields the client sends; unknown fields ignored):

```json
{
  "platform": "google_meet",
  "native_meeting_id": "bqy-ybgi-pbb",
  "bot_name": "Notetaker",
  "language": "en",
  "recording_enabled": false,
  "transcribe_enabled": true,
  "passcode": "482913"
}
```

- `platform` — **required**, `"google_meet"` | `"teams"`.
- `native_meeting_id` — **required**. Meet: `xxx-xxxx-xxx`. Teams: 10–20 digits.
- `bot_name` — optional, default `config.bot_name` (`"Notetaker"`).
- `language` — optional. **May be absent entirely** (client `pop`s it when
  `--lang` was not given, line 268). Absent ⇒ let whisper auto-detect.
- `recording_enabled` — optional, default `false`. `true` ⇒ also persist the
  mixed WAV to `data_dir/audio/{meeting_id}.wav`.
- `transcribe_enabled` — optional, default `true`.
- `passcode` — optional; Teams passcode from the calendar description.

`201 Created`

```json
{
  "id": "0f6f0f0a-8c1f-4a2e-9a10-5d7a2b6e4c11",
  "platform": "google_meet",
  "native_meeting_id": "bqy-ybgi-pbb",
  "constructed_meeting_url": "https://meet.google.com/bqy-ybgi-pbb",
  "bot_name": "Notetaker",
  "status": "requested",
  "created_at": "2026-07-19T10:41:43Z"
}
```

The client only prints this (line 276), so extra fields are safe; the shape
above is the contract for meetbot's own tooling.

Errors:

| Code | When | Body |
| :-- | :-- | :-- |
| 400 | bad/missing `platform`, malformed `native_meeting_id` | `{"detail":"invalid native_meeting_id for google_meet: 'foo'"}` |
| 401 | bad `X-API-Key` | `{"detail":"invalid api key"}` |
| **403** | already `max_concurrent_bots` non-terminal sessions | `{"detail":"maximum concurrent bot limit (4)"}` |
| **409** | a non-terminal session already exists for this `platform/native_meeting_id` | `{"detail":"bot already active for google_meet/bqy-ybgi-pbb"}` |
| 503 | `transcribe_enabled` and whisper unreachable | `{"detail":"transcription service unreachable at http://172.25.32.1:8083/..."}` |

403 wording is deliberately kept close to Vexa's (`maximum concurrent bot
limit (N)`) — it is surfaced verbatim in the operator's heartbeat.

### 1.3.1 Teams support contract (v1.3 — Teams is SUPPORTED; replaced the v1.2 rejection)

`POST /bots` with `"platform": "teams"` → **201**, exactly like Google Meet. The
400 that v1.2 returned is gone, along with `meet::ensure_supported_platform` and
`meet::TEAMS_UNSUPPORTED_DETAIL`. `create_bot` no longer has a step 2b; every
other check (409, 403, 503) applies to Teams unchanged.

`DELETE` and `GET /transcripts` keep accepting `teams` as they always did.
`parse_key` is unchanged: a Teams `native_meeting_id` is still 10-20 digits,
which is what `vexa_bots.py` scrapes off the calendar.

**Request fields Teams uses.** `native_meeting_id` (digits) and the optional
`passcode`. The passcode is **not** typed into a field: it is appended to the
initial navigation as `?p=<passcode>` and Microsoft carries it through the whole
redirect chain (verified). See "passcode" below.

#### What the join path does

`MeetSession::join` dispatches on `key.platform`. Teams runs `join_teams`; the
selector table is `teams::selectors`, a second table under the same one-table
rule as `meet::selectors`. `meet::probe_spec` is the **only** dispatch point
between the two — everything after the join click is shared code.

```
1. navigate teams.microsoft.com/meet/<id>[?p=<passcode>]
2. Teams performs FIVE document navigations (~10-15 s), including a silent MSAL
   `prompt=none` probe that always fails for an anonymous guest. That failure is
   EXPECTED and is not an error. It settles on
   /light-meetings/launch?...&coords=<BASE64>&...
3. verify the meeting id survived: teams::url_contains_code
4. fill the display name through the native value setter (controlled React input)
5. click teams::selectors::JOIN_BUTTON (never disabled — do not gate on enabled)
6. shared wait_for_admission / start_capture / watch_call
```

There is **no "Continue on this browser" interstitial**. `launcher.html`
auto-forwards with no interaction; grepping every captured HTML for
`continue on this browser`, `joinOnWeb`, `continueOnBrowser` returns zero hits.
No click-through is implemented. If Microsoft reintroduces one, the symptom is
the flow stalling on `/dl/launcher/launcher.html` instead of reaching
`/light-meetings/launch`.

#### The `coords` parameter — the root cause of the v1.2 rejection

`coords` is URL-encoded base64 of a JSON object:

```json
{ "meetingUrl": "https://teams.microsoft.com/meet/1234567890123?anon=true&...",
  "meetingCode": "1234567890123",
  "passcode": "AbCd1234EfGh" }
```

The meeting id appears in the final URL **only** inside that blob. `meetbot`'s
"am I still on my meeting?" check was a plain substring test, so it read a
perfectly healthy landing as "navigated away", returned `Admission::Denied`, and
finished the session `completed` with zero segments — which §0.1 makes an
operational skip, so `vexa_bots.py` filed `skipped_not_admitted` with a **green**
heartbeat. Every Teams meeting disappeared with no MOM and no signal anywhere.
That is what the v1.2 400 existed to make visible.

The fix is to decode, not to drop the check:

```
url_contains_code(url, code) :=
      url.contains(code)                                        // /meet/<code> stage
   || base64_decode(query_param(url, "coords")).contains(code)  // light-meetings stage
```

Both `meetingUrl` and `meetingCode` inside the decoded blob carry the code, so a
substring test over the decoded bytes suffices and no JSON parser is involved —
a schema change on Microsoft's side cannot break it. `meet::on_meeting_url`
dispatches to this for Teams and keeps the plain substring test for Meet.

#### Passcode

`?p=<passcode>` on the initial navigation propagates into `coords.passcode` and
prefills the passcode field on the retry screen (both verified). That is the
whole implementation on the happy path; there is no DOM interaction. The value
is percent-encoded, and it is redacted out of every log line.

On the anonymous path there is **no** separate "this meeting requires a
passcode" prompt before the join click. A missing or wrong passcode surfaces
only as the post-join retry screen. `teams::selectors::PREJOIN_PASSCODE_INPUT`
is a best-effort fallback for a real meeting that turns out to show one (recon
gap E, never observed).

#### Rejection is terminal and deliberately ambiguous

After the join click, a bad meeting id, a wrong passcode and (presumably) an
ended meeting all render the **same** screen — `calling-retry-screen`, copy
"We couldn't find a meeting matching this ID and passcode." — which replaces the
entire pre-join tree. Teams does not distinguish them, almost certainly to
prevent meeting-id enumeration. `calling-retry-rejoinbutton` is `disabled` even
when both fields are prefilled, so there is no automated retry.

meetbot therefore **cannot** disambiguate them and per §0.1 does not need to:
all three are a non-admission, so the terminal state is `status = "completed"`
with `segments: []`, which the client files as `skipped_not_admitted`. That is
the correct outcome, and it is the same outcome an admission timeout produces.

The pre-join screen renders for **any** meeting id, including `/meet/99`.
Reaching pre-join proves nothing about the meeting existing; validation happens
only after the join click.

#### Verified vs inferred selectors

`teams::selectors` is split by an explicit divider. Above it (VERIFIED, observed
in live captures on 2026-07-19): `NAME_INPUT`, `JOIN_BUTTON`, `PREJOIN_ROOT`,
`MEETING_TITLE`, `MIC_TOGGLE`, `CAMERA_TOGGLE`, `RETRY_SCREEN`,
`RETRY_SCREEN_TEXT`, `RETRY_CODE_INPUT`, `RETRY_PASSCODE_INPUT`.

Below it (**INFERRED — never observed live**, because no real Teams meeting was
available during recon): `LOBBY_ROOT`, `LOBBY_TEXTS`, `IN_CALL_ROOT`,
`DENIED_TEXTS`, `CALL_ENDED_TEXTS`, `REMOVED_TEXTS`, `LEAVE_BUTTON`,
`PARTICIPANT_COUNT`, `PARTICIPANT_TILES`, `SPEAKING_INDICATORS`, `SPEAKER_TILE`,
`SPEAKER_NAME`, `PREJOIN_PASSCODE_INPUT`.

Two safeguards exist because of that:

* `teams::selectors::NOT_IN_CALL` **vetoes** an in-call claim whenever the
  (verified) pre-join or retry surface is on screen. The specific trap it
  catches: `toggle-mute` carries a byte-identical `data-tid` on both the
  pre-join and in-call toolbars, so a naive `IN_CALL_ROOT` entry would make the
  bot declare itself admitted while standing in the green room and record a call
  it never joined. `toggle-mute` and `toggle-video` are banned from
  `IN_CALL_ROOT` by a unit test.
* When the inferred selectors never match, the bot waits out
  `admission_timeout` and finishes `completed` with zero segments. Wrong
  selectors degrade to "was not admitted", never to a false transcript.

Closing the inferred groups needs one real Teams meeting the operator hosts, with
`spike/teams_flow.mjs` pointed at the live URL; it snapshots every 3 s and
captures the lobby / admitted / ended DOM in a single run. Audio capture from a
real Teams call is likewise untested (recon gap G).

#### Diagnostics

Every DOM read returns a `diag` map of named selector group → the first
candidate that matched, or null. `join_teams` logs it at every stage, and a
failed join returns an error naming:

* the stage (`redirect`, `prejoin`, `code-check`, `join-click`, `admission`),
* the selector groups that did **not** match,
* whether each of those was ever observed live (`[VERIFIED]` vs `[INFERRED]`),
* the last URL and the full diag map.

That distinction is the point: a VERIFIED group that stops matching means
Microsoft changed their markup; an INFERRED group that never matched means the
guess was wrong from the start. Those need opposite fixes, and a Teams meeting
failing at 22:00 must say which one it was.

#### Language pinning

Teams has no `?hl=en` equivalent — its UI language follows `Accept-Language`.
The browser is launched with `--lang=en-US` **and** sent
`Network.setExtraHTTPHeaders{Accept-Language: en-US,en}`. Without both, every
English text anchor silently stops matching on a non-English host and the bot
degrades to an unexplained admission timeout.

#### Things that are NOT required (verified, contradicting earlier assumptions)

* **User-agent spoofing.** A run with Chrome's real `HeadlessChrome/149.0.0.0`
  UA produced an identical redirect chain, final URL and `data-tid` set. The
  desktop UA string meetbot already sends (mandatory for *Meet*, which serves a
  degraded audio-less page to headless) is kept and works equally.
* **iframe traversal.** `iframeSrcs: []` in all five captures; everything is in
  the top-level document.
* **A "Continue on this browser" click-through.** See above.
* **Gating on the join button being enabled.** It is never disabled.

Recon evidence: `spike/teams_recon.md` (9 sections), raw captures in
`spike/teams_recon/`.

### 1.3.2 The 503 is a deliberate deviation from Vexa (v1.1)

Vexa accepted the bot regardless of whisper's health. meetbot's 503 is a
**conscious** divergence, and since v1.1 it is a config flag,
`Config::require_whisper_for_bots` (default `true`).

| | `true` — **default**, current behaviour | `false` — Vexa-compatible |
| :-- | :-- | :-- |
| whisper down at `POST /bots` | **503**, no bot sent | 201, bot joins and records |
| Client effect | `cmd_auto` records `send_failed` + **fail heartbeat** | normal poll cycle |
| Whisper recovers mid-call | irrelevant, nothing joined | the rest of the call is transcribed |
| Whisper never recovers | — | terminal `completed`, `segments: []` (§0.1), filed as `skipped_not_admitted`, heartbeat `ok` |
| Failure mode | **meeting MISSED entirely** | transcript silently empty |

The sharp edge of the default: the Python client runs its own
`ensure_whisper()` probe before it POSTs. If whisper flaps in the window between
that probe and the POST, meetbot 503s and the meeting is never joined — a total
loss, where Vexa would have joined and captured audio.

Default is nevertheless `true`: a `send_failed` + fail heartbeat is a visible
alarm the operator acts on the same day, whereas the `false` path degrades to an empty
transcript that the client classifies as an operational skip with an **ok**
heartbeat, i.e. a silently missing MOM. A loud failure beats a quiet one. Secondary
the flag to `false` on a host where whisper is known to flap and joining
unreliably is better than not joining.

### 1.4 `DELETE /bots/{platform}/{native_meeting_id}` — make the bot leave

`200 OK`

```json
{ "id": "0f6f...c11", "platform": "google_meet", "native_meeting_id": "bqy-ybgi-pbb", "status": "stopping" }
```

Sends `SessionCommand::Stop`. The session then leaves the call, finalizes, and
lands on `stopped` (had audio) or `completed` (never admitted) per §0.1.

`404` when there is no non-terminal session for that key:
`{"detail":"no active bot for google_meet/bqy-ybgi-pbb"}`.

### 1.5 `GET /transcripts/{platform}/{native_meeting_id}` — the money endpoint

Returns the **most recent** meeting row for that key (terminal or not).

`200 OK`

```json
{
  "id": "0f6f0f0a-8c1f-4a2e-9a10-5d7a2b6e4c11",
  "platform": "google_meet",
  "native_meeting_id": "bqy-ybgi-pbb",
  "constructed_meeting_url": "https://meet.google.com/bqy-ybgi-pbb",
  "status": "completed",
  "start_time": "2026-07-19T10:42:07Z",
  "end_time": "2026-07-19T11:31:52Z",
  "segments": [
    { "start_time": 12.48, "end_time": 17.02, "speaker": "YourManager Marchesi", "text": "Let's start with the seller portal.", "language": "en" },
    { "start_time": 17.40, "end_time": 22.95, "speaker": "Notetaker",     "text": "Sure, PIM sync is done.",              "language": "en" }
  ]
}
```

Field-by-field contract (the client reads exactly these):

| Field | Type | Required | Client use |
| :-- | :-- | :-- | :-- |
| `status` | string | yes | terminality check, lines 326/538; printed in transcript header line 313 |
| `start_time` | string \| null | yes (nullable) | line 368: `datetime.fromisoformat(s).replace(tzinfo=utc)`. **Must be a naive-parseable ISO-8601 UTC instant.** Emit `"2026-07-19T10:42:07Z"` — `fromisoformat` on py3.11+ accepts the trailing `Z`. Never emit an offset like `+07:00` (it would be clobbered to UTC and shift the meeting by 7 h). `null` ⇒ client falls back to `now()`. |
| `end_time` | string \| null | yes (nullable) | only rendered into the truncation banner (line 307) |
| `constructed_meeting_url` | string \| null | yes (nullable) | transcript H1 (line 310) + registry key (line 372) |
| `native_meeting_id` | string | yes | H1 fallback |
| `segments` | array | **yes, never null** | line 302; `data.get("segments", [])` tolerates absence but always emit `[]` |
| `segments[].start_time` | **number (float seconds from meeting start)** | yes | `fmt_ts()` does `divmod(int(seconds), 60)` — this is **elapsed seconds, not a timestamp**. Emitting an ISO string here raises `TypeError`. `null` is tolerated (`or 0`) but always emit a number. |
| `segments[].end_time` | number | recommended | unused by the client; kept for MOM tooling |
| `segments[].speaker` | string \| null | yes (nullable) | `or "Unknown"` (line 303). Real display name from the meeting UI when known. |
| `segments[].text` | string | yes | `.strip()`ed; **blank/whitespace-only segments are not counted** (line 332-333). Never emit blank-text segments — they inflate nothing and risk a false "has content". |
| `segments[].language` | string \| null | no | ISO-639-1 only (`"en"`, never `"english"`, see §6.3) |

Ordering: ascending `start_time`.

`404` — the zombie-guard path. Return this **only** when no meeting row exists
for that key at all:

```json
{ "detail": "no meeting google_meet/bqy-ybgi-pbb" }
```

Once `POST /bots` returned 201, this endpoint must **never** 404 for that key
again (that would strand the client's zombie guard after 3 h). Rows are
permanent; there is no retention expiry (the old Vexa 503-on-expiry behaviour
that produced `failed_expired` in `vexa_state.json` is explicitly not
reproduced).

### 1.6 `GET /bots/status` — introspection (meetbot extension)

`200 OK`

```json
{
  "active_bots": 1,
  "max_concurrent_bots": 4,
  "sessions": [
    { "id": "0f6f...c11", "platform": "google_meet", "native_meeting_id": "bqy-ybgi-pbb",
      "title": null, "phase": "in_call", "started_at": "2026-07-19T10:41:43Z", "segment_count": 42 }
  ]
}
```

### 1.7 Admin compat (keeps `vexa_bots.py setup` working)

`POST /admin/users` — header `X-Admin-API-Key`.

```json
// request
{ "email": "teammate@yourcompany.com", "name": "the operator (local)" }
// 200
{ "id": 1, "email": "teammate@yourcompany.com", "name": "the operator (local)" }
```

`POST /admin/users/{uid}/tokens?scopes=bot,tx,browser` — body `{}`.

```json
// 200
{ "token": "<config.api_key>", "user_id": 1, "scopes": ["bot","tx","browser"] }
```

meetbot is single-tenant: it does not mint keys per request, it returns the
configured `api_key` so `setup` writes the right value into `vexa_token.env`.
`scopes` is echoed from the query string, unvalidated.

**First-boot bootstrap (v1.1).** Because this route only echoes the key meetbot
already resolved, and `AppState::new` previously hard-failed when
`api_key_file` was missing, a lost `vexa_token.env` used to be unrecoverable:
meetbot would not boot, and `vexa_bots.py setup` could not regenerate the file
because the server it needs to call was down. `Config::ensure_api_key` closes
the loop — when `api_key_file` is absent (or present with no `VEXA_API_KEY=`
line) meetbot mints a random key, writes it to `api_key_file` with mode `0600`
(preserving any other variables already in the file), and logs it once at
`WARN`. An existing key is read and **never** rewritten, so a live install is
byte-identical after the upgrade. An `api_key_file` that exists but cannot be
read (EACCES, EISDIR) is still a hard failure — that is a misconfiguration, not
a fresh install.

---

## 2. Session state machine

```
                    POST /bots
                        │
                        ▼
                   ┌─────────┐
                   │ Joining │  db: requested → joining
                   └────┬────┘
        launch+navigate │ join UI clicked
             ┌──────────┴───────────┐
             │                      │ browser/CDP error, bad URL
             ▼                      ▼
      ┌─────────────┐          ┌────────┐
      │ WaitingRoom │          │ Failed │  status "failed"
      └──────┬──────┘          └────────┘
             │
   ┌─────────┼──────────────────────────┐
   │admitted │ timeout (admission_       │ host denied
   │         │ timeout_min = 10 min)     │
   ▼         ▼                           ▼
┌────────┐  ┌────────────────────────────────┐
│ InCall │  │  Finalizing (no audio path)    │
└───┬────┘  └───────────────┬────────────────┘
    │                       ▼
    │                  ┌───────────┐
    │                  │ Completed │  status "completed", segments []
    │                  └───────────┘
    │
    ├── meeting ended / last participant left ──► Finalizing ──► Completed
    ├── DELETE /bots (audio already captured)  ──► Finalizing ──► Stopped
    ├── removed by host / "You've been removed"──► Finalizing ──► Stopped
    └── browser died, page detached            ──► Finalizing ──► Failed
```

### 2.1 Transition triggers (exact)

| From | Trigger | To | DB side effect |
| :-- | :-- | :-- | :-- |
| — | `POST /bots` accepted | `Joining` | insert row, `status=requested` then `joining` |
| `Joining` | page loaded, name filled, "Ask to join" clicked | `WaitingRoom` | `status=awaiting_admission` |
| `Joining` | launch/navigate/selector error | `Failed` | `status=failed`, `error=<msg>` |
| `WaitingRoom` | in-call DOM detected (leave button / participant tray) | `InCall` | `status=active`, `start_time=now()` |
| `WaitingRoom` | `admission_timeout_min` elapsed | `Finalizing` → `Completed` | `status=completed`, `end_time=now()`, `start_time` stays `null` |
| `WaitingRoom` | "You can't join this call" / denied | `Finalizing` → `Completed` | same as timeout |
| `WaitingRoom` | `SessionCommand::Stop` | `Finalizing` → `Completed` | same as timeout |
| `InCall` | participant count drops to 1 (bot alone) for `lonely_grace_sec` (60 s) | `Finalizing` → `Completed` | `status=completed`, `end_time=now()` |
| `InCall` | call-ended DOM ("You left the meeting" / "Return to home screen") | `Finalizing` → `Completed` | idem |
| `InCall` | `SessionCommand::Stop` | `Finalizing` → `Stopped` | `status=stopped`, `end_time=now()` |
| `InCall` | removed by host | `Finalizing` → `Stopped` | idem |
| `InCall` | CDP/browser error, page detached | `Finalizing` → `Failed` | `status=failed`, `error=<msg>` |
| `Finalizing` | audio pipeline flushed, last utterance transcribed, WAV closed | terminal | segments committed **before** the terminal status write |

**Ordering rule (non-negotiable):** in `Finalizing`, drain the audio channel,
flush the segmenter, await every in-flight whisper call, insert all segments,
**then** write the terminal status. The client polls every 5 min and pulls the
instant it sees a terminal status — a status written before the segments have
landed produces a permanently truncated transcript.

**Crash recovery:** on startup `db.sweep_stale()` marks **every** non-terminal
row as `failed` with `error = "server restarted while session was live"`. Never
leave a row non-terminal across a restart — the client would poll it forever.

> **Corrected during integration (v1.1).** This paragraph previously read "every
> non-terminal row *older than `stale_after`*", which contradicted its own next
> sentence and failed acceptance test 9: a session killed ten minutes in is not
> six hours old, so an age-gated sweep would leave it non-terminal forever. The
> sweep is now unconditional, which is sound because sessions exist only in the
> server's memory — at startup no non-terminal row can still have an owner.
> `Config::stale_after_hours` (§4) is consequently vestigial: it is kept for
> config compatibility but gates nothing. It is deliberately *not* repurposed as
> a periodic sweep, since an age-gated sweep on a running server would fail a
> legitimately long call mid-recording.

---

## 3. Module map and ownership

| File | Owner agent | Depends on |
| :-- | :-- | :-- |
| `src/state.rs` | state | db |
| `src/db.rs` | db | — |
| `src/audio.rs` | audio | — |
| `src/whisper.rs` | whisper | audio, db |
| `src/meet.rs` | meet | audio, state, teams |
| `src/teams.rs` | meet | — |
| `src/session.rs` | session | all of the above |
| `src/api.rs` | api | state, db, session |
| `src/lib.rs`, `src/main.rs`, `config.toml` | architect / integrator | — |

Shared vocabulary types (`Platform`, `MeetingKey`, `Config`, `AppState`) live in
`state.rs`. Persistence types (`MeetingStatus`, `MeetingRecord`, `Segment`,
`NewSegment`) live in `db.rs`. Audio types (`AudioFrame`, `Utterance`) live in
`audio.rs`. No other module may redefine them.

Global conventions:
- Fallible functions return `anyhow::Result<T>` unless a typed error is
  specified (`whisper::WhisperError`, `api::ApiError`).
- `Db` is `Clone + Send + Sync` (it wraps `Arc<Mutex<Connection>>`) and its
  methods are **synchronous** — rusqlite is blocking, the DB is local, and every
  call is sub-millisecond. Call them directly from async code.
- Everything else that crosses a task boundary is `Send + 'static`.
- Timestamps are `chrono::DateTime<chrono::Utc>`; serialize with
  `chrono::serde::ts_rfc3339` style (`"2026-07-19T10:42:07Z"`), i.e. `.to_rfc3339_opts(SecondsFormat::Secs, true)`.

---

## 4. `src/state.rs`

```rust
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use tokio::sync::{mpsc, RwLock, Semaphore};
use uuid::Uuid;

use crate::db::Db;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Platform {
    GoogleMeet, // wire: "google_meet"
    Teams,      // wire: "teams"
}

impl Platform {
    pub fn as_str(&self) -> &'static str;
    pub fn parse(s: &str) -> Option<Platform>;
    /// Validates a native id for this platform.
    /// GoogleMeet: `^[a-z]{3}-[a-z]{4}-[a-z]{3}$`. Teams: `^\d{10,20}$`.
    pub fn validate_native_id(&self, id: &str) -> bool;
    /// GoogleMeet -> "https://meet.google.com/{id}"
    /// Teams      -> "https://teams.microsoft.com/meet/{id}"
    pub fn meeting_url(&self, native_id: &str) -> String;
}

impl std::fmt::Display for Platform { /* as_str */ }

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct MeetingKey {
    pub platform: Platform,
    pub native_id: String,
}

impl MeetingKey {
    pub fn new(platform: Platform, native_id: impl Into<String>) -> Self;
    /// "google_meet/bqy-ybgi-pbb"
    pub fn as_path(&self) -> String;
    pub fn url(&self) -> String;
}

impl std::fmt::Display for MeetingKey { /* as_path */ }

/// Every `Option<T>` field carries `#[serde(default)]` (serde does NOT make
/// Option fields optional on its own), so omitting a key in config.toml
/// yields `None`. Non-Option fields are required.
#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub http_port: u16,             // 8060
    pub http_bind: String,          // "127.0.0.1"
    /// Path to vexa_token.env; `VEXA_API_KEY=` line is read out of it.
    pub api_key_file: PathBuf,
    /// Overrides api_key_file when set (tests).
    pub api_key: Option<String>,
    pub admin_token: String,
    pub whisper_port: u16,          // 8083
    /// None => resolve the WSL gateway IP at runtime.
    pub whisper_host: Option<String>,
    pub whisper_path: String,       // "/v1/audio/transcriptions"
    pub max_concurrent_bots: usize, // 4
    pub admission_timeout_min: u64, // 10
    pub lonely_grace_sec: u64,      // 60
    pub headless: bool,             // true
    pub chromium_path: PathBuf,
    pub cdp_port: Option<u16>,      // attach instead of launch when set
    pub bot_name: String,           // "Notetaker"
    pub data_dir: PathBuf,          // holds audio/ and meetbot.db
    pub db_path: PathBuf,
    pub stale_after_hours: i64,     // 6
    /// 503 on POST /bots when transcribe_enabled and whisper is down. See
    /// §1.3.2 for the tradeoff. Defaults to `true` when absent from config.toml.
    #[serde(default = "default_require_whisper")]
    pub require_whisper_for_bots: bool, // true
}

impl Config {
    pub fn load(path: &Path) -> anyhow::Result<Config>;
    /// Resolved key: `api_key` if set, else VEXA_API_KEY from `api_key_file`.
    /// Errors when neither exists; does not touch the filesystem to fix that.
    pub fn resolved_api_key(&self) -> anyhow::Result<String>;
    /// Boot-time resolution: `resolved_api_key`, but mints and persists a fresh
    /// key (0600) when `api_key_file` is absent or carries no key. See §1.7.
    /// This is what `AppState::new` calls.
    pub fn ensure_api_key(&self) -> anyhow::Result<String>;
    /// `http://{host}:{whisper_port}{whisper_path}`, host resolved live.
    pub fn whisper_endpoint(&self) -> String;
    /// Default route gateway from `ip route show default`; "172.25.32.1" on failure.
    pub fn gateway_ip() -> String;
    pub fn admission_timeout(&self) -> std::time::Duration;
    pub fn audio_dir(&self) -> PathBuf; // data_dir/audio
}

/// Coarse lifecycle of a live session; mirrors §2. Wire form is snake_case.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionPhase {
    Joining,
    WaitingRoom,
    InCall,
    Finalizing,
    Completed,
    Failed,
    Stopped,
}

impl SessionPhase {
    pub fn is_terminal(&self) -> bool;
    /// Completed/Failed/Stopped -> the matching MeetingStatus; others -> None.
    pub fn terminal_status(&self) -> Option<crate::db::MeetingStatus>;
}

#[derive(Debug)]
pub enum SessionCommand {
    /// Leave the call and finalize.
    Stop,
    /// Ask the running session for its current phase.
    Query(tokio::sync::oneshot::Sender<SessionPhase>),
}

#[derive(Debug, Clone)]
pub struct SessionHandle {
    pub meeting_id: Uuid,
    pub key: MeetingKey,
    pub title: Option<String>,
    pub started_at: DateTime<Utc>,
    pub phase: Arc<RwLock<SessionPhase>>,
    pub cmd_tx: mpsc::Sender<SessionCommand>,
}

impl SessionHandle {
    pub async fn phase(&self) -> SessionPhase;
    /// Best-effort; Err only if the session task is already gone.
    pub async fn stop(&self) -> anyhow::Result<()>;
}

pub struct AppState {
    pub cfg: Config,
    pub api_key: String,
    pub db: Db,
    pub http: reqwest::Client,
    pub whisper: Arc<crate::whisper::WhisperClient>,
    /// Live sessions only; entries are removed when the session task ends.
    pub sessions: RwLock<HashMap<MeetingKey, SessionHandle>>,
    /// Permits == cfg.max_concurrent_bots.
    pub slots: Arc<Semaphore>,
    pub started_at: DateTime<Utc>,
}

pub type SharedState = Arc<AppState>;

impl AppState {
    /// Opens the DB, runs migrations, sweeps stale rows, builds the HTTP client.
    pub fn new(cfg: Config) -> anyhow::Result<SharedState>;
    pub async fn register(&self, handle: SessionHandle);
    pub async fn unregister(&self, key: &MeetingKey);
    pub async fn get_session(&self, key: &MeetingKey) -> Option<SessionHandle>;
    pub async fn active_count(&self) -> usize;
    pub async fn list_sessions(&self) -> Vec<SessionHandle>;
}
```

---

## 5. `src/db.rs`

Schema (created by `migrate()`, `PRAGMA journal_mode=WAL`):

```sql
CREATE TABLE IF NOT EXISTS meetings (
    id                      TEXT PRIMARY KEY,     -- uuid v4
    platform                TEXT NOT NULL,
    native_meeting_id       TEXT NOT NULL,
    constructed_meeting_url TEXT,
    title                   TEXT,
    bot_name                TEXT NOT NULL,
    language                TEXT,
    passcode                TEXT,
    recording_enabled       INTEGER NOT NULL DEFAULT 0,
    transcribe_enabled      INTEGER NOT NULL DEFAULT 1,
    status                  TEXT NOT NULL,
    error                   TEXT,
    audio_path              TEXT,
    start_time              TEXT,                 -- RFC3339 UTC
    end_time                TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meetings_key
    ON meetings(platform, native_meeting_id, created_at DESC);

CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start_time REAL NOT NULL,   -- seconds elapsed from meeting start
    end_time   REAL NOT NULL,
    speaker    TEXT,
    text       TEXT NOT NULL,
    language   TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_meeting
    ON segments(meeting_id, start_time);
```

```rust
use std::path::Path;
use std::sync::{Arc, Mutex};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::state::{MeetingKey, Platform};

/// Wire values are exactly these snake_case strings.
/// TERMINAL: Completed | Failed | Stopped  (see §0.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MeetingStatus {
    Requested,          // row created, task not started
    Joining,            // browser launching / navigating
    AwaitingAdmission,  // in the waiting room
    Active,             // in call, capturing
    Finalizing,         // draining audio + whisper
    Completed,          // TERMINAL
    Failed,             // TERMINAL
    Stopped,            // TERMINAL
}

impl MeetingStatus {
    pub fn as_str(&self) -> &'static str;
    pub fn parse(s: &str) -> Option<MeetingStatus>;
    /// Completed | Failed | Stopped — exactly the client's tuple.
    pub fn is_terminal(&self) -> bool;
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MeetingRecord {
    pub id: Uuid,
    pub platform: Platform,
    pub native_meeting_id: String,
    pub constructed_meeting_url: Option<String>,
    pub title: Option<String>,
    pub bot_name: String,
    pub language: Option<String>,
    pub passcode: Option<String>,
    pub recording_enabled: bool,
    pub transcribe_enabled: bool,
    pub status: MeetingStatus,
    pub error: Option<String>,
    pub audio_path: Option<String>,
    pub start_time: Option<DateTime<Utc>>,
    pub end_time: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl MeetingRecord {
    pub fn key(&self) -> MeetingKey;
}

#[derive(Debug, Clone)]
pub struct NewMeeting {
    pub key: MeetingKey,
    pub title: Option<String>,
    pub bot_name: String,
    pub language: Option<String>,
    pub passcode: Option<String>,
    pub recording_enabled: bool,
    pub transcribe_enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Segment {
    pub id: i64,
    pub meeting_id: Uuid,
    pub start_time: f64,
    pub end_time: f64,
    pub speaker: Option<String>,
    pub text: String,
    pub language: Option<String>,
    pub created_at: DateTime<Utc>,
}

/// What whisper.rs produces and db.rs persists.
#[derive(Debug, Clone, PartialEq)]
pub struct NewSegment {
    pub start_time: f64,
    pub end_time: f64,
    pub speaker: Option<String>,
    pub text: String,
    pub language: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Db {
    conn: Arc<Mutex<rusqlite::Connection>>,
}

impl Db {
    /// Opens (creating parents), sets WAL + busy_timeout, runs `migrate`.
    pub fn open(path: &Path) -> anyhow::Result<Db>;
    pub fn open_in_memory() -> anyhow::Result<Db>;
    pub fn migrate(&self) -> anyhow::Result<()>;

    pub fn create_meeting(&self, req: &NewMeeting) -> anyhow::Result<MeetingRecord>;
    /// Most recent row for the key, terminal or not. `None` => the API 404s.
    pub fn latest_meeting(&self, key: &MeetingKey) -> anyhow::Result<Option<MeetingRecord>>;
    pub fn get_meeting(&self, id: Uuid) -> anyhow::Result<Option<MeetingRecord>>;
    /// Most recent NON-terminal row for the key (drives the 409 on POST /bots).
    pub fn active_meeting(&self, key: &MeetingKey) -> anyhow::Result<Option<MeetingRecord>>;
    /// Rows in a non-terminal status (drives the 403 concurrency check).
    pub fn count_active(&self) -> anyhow::Result<usize>;
    pub fn list_meetings(&self, limit: usize) -> anyhow::Result<Vec<MeetingRecord>>;

    /// Also bumps updated_at. `error` is only written when `Some`.
    pub fn set_status(&self, id: Uuid, status: MeetingStatus, error: Option<&str>) -> anyhow::Result<()>;
    pub fn set_start_time(&self, id: Uuid, ts: DateTime<Utc>) -> anyhow::Result<()>;
    pub fn set_end_time(&self, id: Uuid, ts: DateTime<Utc>) -> anyhow::Result<()>;
    pub fn set_title(&self, id: Uuid, title: &str) -> anyhow::Result<()>;
    pub fn set_audio_path(&self, id: Uuid, path: &str) -> anyhow::Result<()>;

    /// Blank/whitespace-only `text` is dropped here, not at the API layer.
    /// Returns the number actually inserted.
    pub fn insert_segments(&self, meeting_id: Uuid, segs: &[NewSegment]) -> anyhow::Result<usize>;
    pub fn insert_segment(&self, meeting_id: Uuid, seg: &NewSegment) -> anyhow::Result<i64>;
    /// Ordered by start_time ASC. Never returns blank-text rows.
    pub fn get_segments(&self, meeting_id: Uuid) -> anyhow::Result<Vec<Segment>>;
    pub fn count_segments(&self, meeting_id: Uuid) -> anyhow::Result<usize>;

    /// Startup crash recovery: every non-terminal row older than `older_than`
    /// becomes Failed with the given error. Returns rows touched.
    pub fn sweep_stale(&self, older_than: chrono::Duration, error: &str) -> anyhow::Result<usize>;
}
```

---

## 6. Audio and transcription

### 6.1 `src/audio.rs`

Canonical format everywhere: **16 kHz, mono, signed 16-bit little-endian PCM**.

```rust
use std::path::{Path, PathBuf};
use tokio::sync::mpsc;

pub const SAMPLE_RATE: u32 = 16_000;
pub const CHANNELS: u16 = 1;
pub const BITS_PER_SAMPLE: u16 = 16;

/// One capture buffer as produced by meet.rs.
#[derive(Debug, Clone)]
pub struct AudioFrame {
    pub pcm: Vec<i16>,
    /// Seconds elapsed since capture start (== meeting start_time origin).
    pub offset_sec: f64,
    /// Active speaker display name at capture time, if the page exposed one.
    pub speaker: Option<String>,
}

impl AudioFrame {
    pub fn duration_sec(&self) -> f64;
    pub fn is_silent(&self, threshold_rms: f32) -> bool;
}

/// A speech run cut out by the segmenter; the unit whisper transcribes.
#[derive(Debug, Clone)]
pub struct Utterance {
    pub pcm: Vec<i16>,
    pub start_time: f64,
    pub end_time: f64,
    pub speaker: Option<String>,
}

impl Utterance {
    pub fn duration_sec(&self) -> f64;
    /// In-memory RIFF/WAVE bytes for the multipart upload.
    pub fn to_wav_bytes(&self) -> anyhow::Result<Vec<u8>>;
}

#[derive(Debug, Clone)]
pub struct VadConfig {
    pub silence_rms: f32,          // 0.006 of full scale
    pub min_utterance_ms: u64,     // 400   — shorter runs are discarded
    pub max_utterance_ms: u64,     // 25_000 — hard cut, whisper chunk ceiling
    pub trailing_silence_ms: u64,  // 700   — closes an utterance
    /// A speaker change also closes the current utterance.
    pub split_on_speaker_change: bool, // true
}

impl Default for VadConfig { /* the values above */ }

/// Energy-gated utterance cutter. Not Sync; owned by one task.
pub struct Segmenter { /* private */ }

impl Segmenter {
    pub fn new(cfg: VadConfig) -> Segmenter;
    /// Feeds one frame; returns any utterances completed by it (usually 0 or 1).
    pub fn push(&mut self, frame: AudioFrame) -> Vec<Utterance>;
    /// Emits whatever is buffered (call during Finalizing).
    pub fn flush(&mut self) -> Option<Utterance>;
}

/// Streaming WAV sink for `recording_enabled`.
pub struct WavSink { /* private */ }

impl WavSink {
    pub fn create(path: &Path) -> anyhow::Result<WavSink>;
    pub fn write(&mut self, pcm: &[i16]) -> anyhow::Result<()>;
    /// Finalizes the RIFF header; returns total samples written.
    pub fn finalize(self) -> anyhow::Result<u64>;
}

pub fn rms(pcm: &[i16]) -> f32;
/// Little-endian i16 bytes (CDP payload) -> samples.
pub fn pcm_from_le_bytes(bytes: &[u8]) -> Vec<i16>;
/// f32 [-1,1] samples (WebAudio) -> i16, clamped.
pub fn pcm_from_f32(samples: &[f32]) -> Vec<i16>;
/// Linear resample to SAMPLE_RATE. No-op when `from == SAMPLE_RATE`.
pub fn resample_to_16k(pcm: &[i16], from: u32) -> Vec<i16>;

/// Long-running task: frames in, utterances out, optional WAV on the side.
/// Returns when `frames` closes, after flushing the segmenter.
pub async fn run_segmenter(
    frames: mpsc::Receiver<AudioFrame>,
    cfg: VadConfig,
    utterances: mpsc::Sender<Utterance>,
    wav_path: Option<PathBuf>,
) -> anyhow::Result<Option<PathBuf>>;
```

### 6.2 Channel topology (fixed)

```
meet.rs  --mpsc::Sender<AudioFrame>(cap 256)-->  audio::run_segmenter
audio    --mpsc::Sender<Utterance>(cap 64)--->  whisper::run_transcriber  --> Db::insert_segment
api.rs   --mpsc::Sender<SessionCommand>(cap 8)-> session::run
```

Every channel is `tokio::sync::mpsc`. Dropping the sender is the shutdown
signal; each stage exits only after draining its receiver.

### 6.3 `src/whisper.rs`

External OpenAI-compatible endpoint on the Windows host:
`POST http://{gateway}:8083/v1/audio/transcriptions`, multipart with
`file` (`audio.wav`, `audio/wav`), `model` (`whisper-1`),
`response_format` (`json`), and `language` when known.

```rust
use std::sync::Arc;
use std::time::Duration;

use serde::Deserialize;
use tokio::sync::mpsc;
use uuid::Uuid;

use crate::audio::Utterance;
use crate::db::{Db, NewSegment};

#[derive(Debug, thiserror::Error)]
pub enum WhisperError {
    #[error("whisper unreachable at {endpoint}: {source}")]
    Unreachable { endpoint: String, source: reqwest::Error },
    #[error("whisper HTTP {status}: {body}")]
    Http { status: u16, body: String },
    #[error("whisper response decode failed: {0}")]
    Decode(String),
    #[error("wav encode failed: {0}")]
    Encode(String),
}

#[derive(Debug, Clone, Deserialize)]
pub struct Transcription {
    pub text: String,
    /// Raw server value, may be a full name like "english" — normalize before use.
    pub language: Option<String>,
}

#[derive(Debug, Clone)]
pub struct WhisperClient {
    http: reqwest::Client,
    endpoint: String,
    timeout: Duration,
}

impl WhisperClient {
    pub fn new(http: reqwest::Client, endpoint: impl Into<String>) -> WhisperClient;
    pub fn endpoint(&self) -> &str;
    /// GET the endpoint root; any HTTP response counts as up, only a
    /// connection error counts as down. Never panics, never blocks > 5 s.
    pub async fn health(&self) -> bool;
    pub async fn transcribe(&self, wav: Vec<u8>, language: Option<&str>)
        -> Result<Transcription, WhisperError>;
    /// `Ok(None)` when the transcript is blank or a known non-speech artifact.
    pub async fn transcribe_utterance(&self, u: &Utterance, language: Option<&str>)
        -> Result<Option<NewSegment>, WhisperError>;
}

/// LANDMINE (vexa_bots.py:484-488): whisper-server returns the language as a
/// full English name ("english"). Vexa's validator rejected that and silently
/// saved zero segments. Always map to ISO-639-1 before persisting.
/// "english" -> "en", "indonesian" -> "id", "arabic" -> "ar", ...;
/// already-ISO input passes through; unknown input returns None.
pub fn normalize_language(raw: &str) -> Option<String>;

/// Blank text, "", "(silence)", "[BLANK_AUDIO]", "you", "Thank you." on a
/// sub-second clip, and similar whisper hallucinations on silence.
pub fn is_noise_transcript(text: &str, duration_sec: f64) -> bool;

/// Default gateway from `ip route show default`; falls back to "172.25.32.1".
pub fn gateway_ip() -> String;

/// What `run_transcriber` reports. "0 segments" is ambiguous on its own — a
/// silent room and a total ASR outage look identical — and these counts are
/// what let `session::drive` tell them apart (§0.1, v1.2 row).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct TranscriberOutcome {
    pub inserted: usize,   // segments committed
    pub seen: usize,       // utterances pulled off the channel
    pub dropped: usize,    // lost to exhausted retries or a failed DB write
    pub abandoned: usize,  // backlog discarded after whisper was confirmed down
    pub whisper_down: bool,
}

impl TranscriberOutcome {
    pub fn lost(&self) -> usize;      // dropped + abandoned
    pub fn loss_ratio(&self) -> f64;  // lost / seen; 0.0 when seen == 0
}

/// Consumes utterances, transcribes, inserts segments. Retries each utterance
/// up to `max_retries` (3) with exponential backoff on `Unreachable`/5xx;
/// on final failure it logs and DROPS that utterance — a dead whisper must
/// never abort the session (§0.1).
///
/// **Backlog short-circuit (v1.2).** Dropping utterances one at a time is only
/// cheap while failures are isolated. Against a whisper that is simply gone
/// each utterance still costs `max_retries` attempts plus backoff, drained
/// strictly serially, with a 64-deep channel that is typically full at the end
/// of a call. That is minutes per utterance and hours per meeting, spent with
/// the row pinned in `finalizing` and the concurrency permit held — far past
/// `SHUTDOWN_GRACE` (90 s) and the unit's `TimeoutStopSec` (120 s), so systemd
/// SIGKILLs the process and the terminal write never happens. After two
/// consecutive failures the worker therefore runs exactly one `health()` probe;
/// if whisper is unreachable the remaining backlog is drained untranscribed
/// into `abandoned` and the worker returns. Segments are committed per
/// utterance, so giving up never rolls back transcribed work.
pub async fn run_transcriber(
    client: Arc<WhisperClient>,
    utterances: mpsc::Receiver<Utterance>,
    db: Db,
    meeting_id: Uuid,
    language: Option<String>,
    max_retries: u32,
) -> anyhow::Result<TranscriberOutcome>;
```

---

## 7. `src/meet.rs` (+ `src/teams.rs`)

Drives Chromium over CDP with `chromiumoxide`. One state machine, two selector
tables: `meet::selectors` (Google Meet) and `teams::selectors` (Microsoft Teams,
§1.3.1). `meet::probe_spec(platform)` is the ONLY place the two are dispatched
between — admission, capture, the wall-clock cap, end-detection and teardown are
shared code for both platforms, not parallel implementations.

`src/teams.rs` holds no browser code at all: the selector table, the
`coords`/base64 URL decoding, the passcode routing and the diagnostic vocabulary
(`JoinStage`, `Verified`, `DIAG_GROUPS`) are pure functions, so all of it is
unit-testable without a Chrome. Two modes: launch a fresh
browser at `cfg.chromium_path`, or attach to an already-running CDP endpoint
(`cfg.cdp_port`, e.g. the systemd `tln-browser.service` on 127.0.0.1:9222 —
**attach only, never kill or restart it**).

Launch flags (mandatory): `--headless=new` when `headless`,
`--use-fake-ui-for-media-stream`, `--use-fake-device-for-media-stream`,
`--autoplay-policy=no-user-gesture-required`, `--disable-dev-shm-usage`,
`--no-sandbox`, `--mute-audio=false`.

Audio path: an injected script taps the remote `MediaStream` through WebAudio,
downsamples to 16 kHz mono f32, and ships buffers to Rust via a CDP binding
(`Runtime.addBinding("meetbotAudio")`) alongside the current active-speaker
name. `meet.rs` converts each payload to an `AudioFrame` and sends it on the
frames channel.

```rust
use std::path::PathBuf;
use std::time::Duration;

use tokio::sync::mpsc;

use crate::audio::AudioFrame;
use crate::state::{MeetingKey, Platform};

#[derive(Debug, Clone)]
pub struct BrowserOptions {
    pub chromium_path: PathBuf,
    pub headless: bool,
    /// Attach to ws://127.0.0.1:{port} instead of launching.
    pub attach_cdp_port: Option<u16>,
    pub user_data_dir: Option<PathBuf>,
    pub window_size: (u32, u32), // (1280, 720)
}

/// Result of waiting at the waiting-room gate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Admission {
    /// In-call DOM detected.
    Admitted,
    /// `admission_timeout` elapsed — session must finish `completed`, 0 segments.
    TimedOut,
    /// Host denied / "You can't join this call" — also finishes `completed`.
    Denied,
}

/// Why the in-call watch loop returned.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CallExit {
    /// Meeting ended or the bot was left alone past the grace period.
    MeetingEnded,
    /// Host removed the bot mid-call.
    RemovedByHost,
    /// `Stop` command observed via the cancel token.
    Stopped,
    /// Page/CDP broke.
    BrowserError(String),
}

/// Owns the browser, the page, and the chromiumoxide handler task.
pub struct MeetSession { /* private */ }

impl MeetSession {
    pub async fn launch(opts: &BrowserOptions) -> anyhow::Result<MeetSession>;

    /// Navigates to `key.url()`, dismisses mic/cam prompts, types `bot_name`,
    /// enters `passcode` when the platform asks, clicks "Ask to join"/"Join now".
    /// Errors here drive Joining -> Failed.
    pub async fn join(
        &self,
        key: &MeetingKey,
        bot_name: &str,
        passcode: Option<&str>,
    ) -> anyhow::Result<()>;

    /// Polls the DOM every 2 s until admitted, denied, or timed out.
    pub async fn wait_for_admission(&self, timeout: Duration) -> anyhow::Result<Admission>;

    /// Injects the tap and starts pushing frames. Must be called only after
    /// `Admission::Admitted`.
    pub async fn start_capture(&self, frames: mpsc::Sender<AudioFrame>) -> anyhow::Result<()>;

    /// Watches for meeting end / removal / stop. `stop` resolving means the
    /// session got `SessionCommand::Stop`. Returns when the call is over.
    pub async fn watch_call(
        &self,
        lonely_grace: Duration,
        stop: tokio::sync::oneshot::Receiver<()>,
    ) -> CallExit;

    /// Participants currently visible in the tray, bot included.
    pub async fn participant_count(&self) -> anyhow::Result<usize>;
    /// Display name of whoever the UI marks as speaking right now.
    pub async fn active_speaker(&self) -> anyhow::Result<Option<String>>;

    /// Clicks leave. Best effort; never errors the session.
    pub async fn leave(&self);
    /// Drops the frames sender, closes the page, and kills the browser when it
    /// was launched (never when attached). Idempotent.
    pub async fn close(self);
}

/// Same rules as `Platform::meeting_url`, re-exported for callers that only
/// have loose parts.
pub fn meeting_url(platform: Platform, native_id: &str) -> String;
```

---

### 7.1 Capture transports (added during integration, v1.1)

Two independent taps were built against this spec. Rather than delete one, both
ship, selected by `Config::capture_transport` (`state::CaptureTransport`):

| | `cdp` (**default**) | `websocket` |
| :-- | :-- | :-- |
| Asset | `assets/capture.js` | `assets/capture_ws.js` |
| Wire | base64 i16 over `Runtime.addBinding("meetbotAudio")` | `[f64 offset][f32 samples]` over a loopback WebSocket |
| Frames reach Rust via | `meet.rs` pump task -> frames channel | `audio::start_ingest_server` -> frames channel |
| Entry point | `MeetSession::start_capture(frames)` | `MeetSession::start_capture_ws(url)` |
| Status | the §7 contract; what the acceptance tests exercise | fallback, unproven on a live call |

`cdp` is the specified path and the default. `websocket` exists because
`Runtime.bindingCalled` carries every audio frame through the same CDP session
as the DOM polling, so a slow consumer can back audio up behind other traffic;
the WebSocket path gives audio its own socket. Switch only if the CDP tap
misbehaves on a real call.

**Both taps are templates**, not standalone JS: `meet.rs::fill_selectors()`
substitutes the speaker-attribution selectors from the canonical table for the
platform being driven (`meet::selectors` or `teams::selectors`) into each. Neither asset carries markup knowledge of its own,
so a DOM fix is applied once. Tests assert no token survives substitution and
that both taps carry the same selectors. The WS server binds `127.0.0.1:0` and
must never be reachable off-box; the `IngestServer` must outlive the call, since
dropping it closes the frames channel and is the end-of-capture signal.

**Audio-clock gate (mandatory).** Both paths call
`MeetSession::await_audio_clock()` after injection, which returns only once the
page's `AudioContext.currentTime` has been observed to *advance* across two
probes. Per the headless-capture spike this must not be replaced by a fixed
sleep: under `--headless=new` a context can report `state === "running"` while
the render graph is never pumped, and `currentTime` is the only witness that
separates a live graph from a stalled one. It advances whenever the graph runs,
so silence in the room does not trip it — the gate tests the tap, never speech.
Failure names `--autoplay-policy=no-user-gesture-required` as the first suspect.

---

### 7.2 Resource bounds (added v1.2, from the adversarial review)

Three unbounded resources, all of which failed silently.

**Wall-clock cap on the in-call phase.** `admission_timeout_min` bounds the
waiting room; nothing bounded the call. Every exit from `watch_call` other than
this one depends on the DOM still reading the way `selectors` expects — and the
lonely-bot exit is not a backstop, because `PARTICIPANT_COUNT` and
`PARTICIPANT_TILES` are both keyed off `data-participant-id` and drift together.
When they do while `IN_CALL_MARKERS` still matches, `probe.participants` is
`None`, `alone_since` resets on every poll, the lonely exit disables itself, and
one Google UI release strands every bot in an endless call holding a live Chrome,
a concurrency permit and a non-terminal DB row.

`meet::DEFAULT_MAX_CALL_DURATION` is **4 hours** and returns
`CallExit::MeetingEnded`, i.e. a normal `completed` with whatever audio was
captured — reaching the cap is not a failure. Overridable per-process with
`MEETBOT_MAX_CALL_MIN` (minutes) or per-session with
`MeetSession::set_max_call_duration`; zero is clamped back to the default, never
read as "uncapped".

The `config.toml` key `max_call_duration_min` **is plumbed** (v1.3):
`Config::max_call_duration_min: Option<u64>` (serde default `None`) with the
accessor `Config::max_call_duration() -> Option<Duration>`, applied by
`session.rs` immediately after `MeetSession::launch`:

```rust
if let Some(d) = cfg.max_call_duration() {
    meet.set_max_call_duration(d);
}
```

Precedence, highest first: the config key, then `MEETBOT_MAX_CALL_MIN`, then
`DEFAULT_MAX_CALL_DURATION`. The `Option` is what makes that ordering work —
defaulting the field to `Some(4h)` would have silently shadowed the env
override on every host that never set the key. The cap is active on every
session either way; the key only overrides which value it holds.

**`Browser::connect` timeout (attach mode).** Launch mode has
`launch_timeout(45s)`; `Browser::connect` builds its own reqwest client with no
timeout, so a CDP port that accepts TCP and never completes the handshake pends
forever and pins the session in `joining`. Bounded by
`meet::ATTACH_CONNECT_TIMEOUT` (20 s), which fails the session normally.

**`Drop` for `MeetSession` + a startup profile sweep.** All cleanup used to live
in the consuming `close(self)`, so a panic, a task abort or a hard exit leaked:
(a) the temp profile `/tmp/meetbot-chrome-<uuid>/`, tens of MB, with nothing on
the box sweeping them; (b) in **attach** mode the Meet tab, because `page.close()`
only ran inside `close()` — one orphan renderer on the shared
`tln-browser.service` per session, each roughly the 500 MB this project exists to
eliminate. `MeetSession` now has a `Drop` that aborts both tasks, closes the
target when attached, and removes the profile; `close()` sets a `closed` flag so
the destructor is a no-op on the normal path. `meet::sweep_stale_profiles()` runs
once per process from the first `MeetSession::launch` (and is `pub` for `main.rs`
to call at boot beside `db.sweep_stale()`); it only removes `meetbot-chrome-*`
directories older than 6 h, so a live session's profile is never pulled out from
under it. `remove_profile_dir`'s final give-up now logs at **warn**, not debug.

---

## 8. `src/session.rs`

The orchestrator. Owns the §2 state machine and is the **only** module allowed
to write terminal statuses.

```rust
use std::sync::Arc;

use tokio::sync::mpsc;
use uuid::Uuid;

use crate::state::{MeetingKey, SessionCommand, SessionHandle, SessionPhase, SharedState};

#[derive(Debug, Clone)]
pub struct SessionSpec {
    pub meeting_id: Uuid,
    pub key: MeetingKey,
    pub title: Option<String>,
    pub bot_name: String,
    /// ISO-639-1 or None (auto-detect).
    pub language: Option<String>,
    pub passcode: Option<String>,
    pub recording_enabled: bool,
    pub transcribe_enabled: bool,
}

/// Creates the command channel + phase cell, spawns `run` on the tokio runtime,
/// registers the handle in `state.sessions`, and returns immediately. The
/// concurrency permit is acquired by the CALLER (api.rs) and moved in.
pub fn spawn(
    state: SharedState,
    spec: SessionSpec,
    permit: tokio::sync::OwnedSemaphorePermit,
) -> anyhow::Result<SessionHandle>;

/// The full lifecycle. Never panics; every exit path writes exactly one
/// terminal MeetingStatus and then `state.unregister(&spec.key)`.
///
/// Order in Finalizing (hard requirement):
///   1. leave the call, close the browser, under BROWSER_TEARDOWN_BUDGET (20 s)
///   2a. drop the IngestServer, then fire the `capture_done` signal
///   2b. relay stops on that signal -> drops seg_tx -> run_segmenter flushes ->
///       drops the utterance sender
///   3. await the relay, the segmenter and run_transcriber against one shared
///      FINALIZE_BUDGET (45 s) deadline, then re-probe whisper if the drain
///      produced nothing (§0.1 v1.2 row)
///   4. set_end_time, then set_status(<terminal>)
///
/// **Corrected during the v1.3 re-review.** Steps 1 and 3 above previously read
/// "leave the call, close the browser" (unbounded) and "await run_transcriber
/// under FINALIZE_BUDGET", i.e. the budget covered only the last stage. That
/// bounded nothing, and the wording specified the bug: steps 2b and 3 are one
/// backpressured chain, so a stalled transcriber blocks `run_segmenter` in
/// `utterances.send()` on the full 64-deep channel, which fills `seg_tx` and
/// blocks the relay, and BOTH were awaited unbounded and BEFORE the deadline was
/// armed. Measured against the real `run_relay` + `run_segmenter`, the sequence
/// had not returned after 3 s; the field figure is `HEALTH_PROBE_AFTER_FAILURES`
/// x `max_retries` x `DEFAULT_TIMEOUT` ~= 16 min, far past `TimeoutStopSec`.
/// The budget is therefore a single deadline shared by all three stages, and
/// each stage is ABORTED on expiry — aborting is load-bearing, because each
/// stage owns the sender feeding the next one, so dropping its task is what
/// releases the stage behind it. Browser teardown is bounded separately because
/// `leave()`/`close()` are unbounded CDP round-trips and the exit that most
/// needs them (`CallExit::BrowserError`) is by definition a broken page;
/// `BROWSER_TEARDOWN_BUDGET + FINALIZE_BUDGET` must stay under `SHUTDOWN_GRACE`.
/// `MeetSession::close` accordingly claims its `closed` flag LAST, so a close
/// cancelled by that timeout still falls through to the `Drop` cleanup instead
/// of suppressing it.
///
/// Two bounds added in v1.2, both because the original steps 2 and 3 could
/// block forever:
///
/// * **`capture_done` (step 2a).** Relay termination must not depend on remote
///   behaviour. `IngestServer::drop` aborts only the accept loop; every
///   accepted connection runs in its own task holding a CLONE of the frames
///   sender, so on the `websocket` transport the frames channel stayed open
///   until Chrome tore its sockets down. The relay now stops on an explicit
///   in-process signal, drains whatever is already buffered, and drops
///   `seg_tx`. The `cdp` transport is unaffected (its sender dies with `meet`),
///   and the drain means no buffered frame is lost either way.
/// * **`FINALIZE_BUDGET` (step 3; widened to all of steps 2b-3 in v1.3).** A
///   hard ceiling on the drain,
///   well under `SHUTDOWN_GRACE` (90 s) and `TimeoutStopSec` (120 s). A whisper
///   that accepts TCP and never answers costs `max_retries * timeout` per
///   queued utterance, serially, which overran both and got the process
///   SIGKILLed mid-finalization — losing the entire transcript rather than its
///   tail. On expiry the worker is aborted and the DB's own `count_segments` is
///   used: everything already committed is kept, and the session is then
///   classified per the §0.1 v1.2 row (an expired drain counts as proof the
///   transcriber is broken, so a zero-segment session lands on `failed`).
pub async fn run(
    state: SharedState,
    spec: SessionSpec,
    cmd_rx: mpsc::Receiver<SessionCommand>,
    phase: Arc<tokio::sync::RwLock<SessionPhase>>,
    permit: tokio::sync::OwnedSemaphorePermit,
);

/// Maps a `meet::CallExit` + "did we capture any audio" to the terminal phase,
/// per the §0.1 decision table. Pure; unit-testable.
pub fn classify_exit(exit: &crate::meet::CallExit, captured_audio: bool) -> SessionPhase;

/// Waiting-room outcomes always classify to `SessionPhase::Completed`.
pub fn classify_admission(admission: &crate::meet::Admission) -> SessionPhase;
```

---

## 9. `src/api.rs`

```rust
use axum::Router;
use serde::{Deserialize, Serialize};

use crate::db::Segment;
use crate::state::SharedState;

#[derive(Debug, Deserialize)]
pub struct CreateBotRequest {
    pub platform: String,
    pub native_meeting_id: String,
    #[serde(default)]
    pub bot_name: Option<String>,
    #[serde(default)]
    pub language: Option<String>,
    #[serde(default)]
    pub passcode: Option<String>,
    #[serde(default)]
    pub recording_enabled: Option<bool>,
    #[serde(default)]
    pub transcribe_enabled: Option<bool>,
    /// meetbot extension; the Python client never sends it.
    #[serde(default)]
    pub title: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct CreateBotResponse {
    pub id: String,
    pub platform: String,
    pub native_meeting_id: String,
    pub constructed_meeting_url: String,
    pub bot_name: String,
    pub status: String,
    pub created_at: String,
}

#[derive(Debug, Serialize)]
pub struct StopBotResponse {
    pub id: String,
    pub platform: String,
    pub native_meeting_id: String,
    pub status: String, // "stopping"
}

#[derive(Debug, Serialize)]
pub struct TranscriptSegment {
    pub start_time: f64,
    pub end_time: f64,
    pub speaker: Option<String>,
    pub text: String,
    pub language: Option<String>,
}

impl From<Segment> for TranscriptSegment { /* … */ }

#[derive(Debug, Serialize)]
pub struct TranscriptResponse {
    pub id: String,
    pub platform: String,
    pub native_meeting_id: String,
    pub constructed_meeting_url: Option<String>,
    pub status: String,
    /// RFC3339 UTC with a trailing "Z", or null. NEVER a non-UTC offset.
    pub start_time: Option<String>,
    pub end_time: Option<String>,
    /// Always present, `[]` when empty. Never null.
    pub segments: Vec<TranscriptSegment>,
}

#[derive(Debug, Serialize)]
pub struct ErrorResponse {
    pub detail: String,
}

#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error("invalid api key")]
    Unauthorized,
    #[error("{0}")]
    BadRequest(String),
    #[error("{0}")]
    NotFound(String),
    #[error("{0}")]
    Conflict(String),
    #[error("maximum concurrent bot limit ({0})")]
    TooManyBots(usize),
    #[error("{0}")]
    Unavailable(String),
    #[error("{0}")]
    Internal(String),
}

/// 401 / 400 / 404 / 409 / 403 / 503 / 500 + `ErrorResponse` body.
impl axum::response::IntoResponse for ApiError { /* … */ }

/// GET /, GET /health, GET /bots/status,
/// POST /bots, DELETE /bots/{platform}/{native_id},
/// GET /transcripts/{platform}/{native_id},
/// POST /admin/users, POST /admin/users/{uid}/tokens
pub fn router(state: SharedState) -> Router;

/// Binds `cfg.http_bind:cfg.http_port` and serves until ctrl-c.
pub async fn serve(state: SharedState) -> anyhow::Result<()>;

// Handlers (pub for testing; all take State<SharedState>):
pub async fn root() -> impl axum::response::IntoResponse;
pub async fn health(state: axum::extract::State<SharedState>) -> impl axum::response::IntoResponse;
pub async fn create_bot(
    state: axum::extract::State<SharedState>,
    headers: axum::http::HeaderMap,
    body: axum::Json<CreateBotRequest>,
) -> Result<(axum::http::StatusCode, axum::Json<CreateBotResponse>), ApiError>;
pub async fn stop_bot(
    state: axum::extract::State<SharedState>,
    headers: axum::http::HeaderMap,
    path: axum::extract::Path<(String, String)>,
) -> Result<axum::Json<StopBotResponse>, ApiError>;
pub async fn get_transcript(
    state: axum::extract::State<SharedState>,
    headers: axum::http::HeaderMap,
    path: axum::extract::Path<(String, String)>,
) -> Result<axum::Json<TranscriptResponse>, ApiError>;
pub async fn bots_status(
    state: axum::extract::State<SharedState>,
    headers: axum::http::HeaderMap,
) -> Result<axum::Json<serde_json::Value>, ApiError>;

/// Constant-time compare of the `X-API-Key` header against `state.api_key`.
pub fn check_api_key(state: &SharedState, headers: &axum::http::HeaderMap) -> Result<(), ApiError>;
/// Same for `X-Admin-API-Key` against `state.cfg.admin_token`.
pub fn check_admin_key(state: &SharedState, headers: &axum::http::HeaderMap) -> Result<(), ApiError>;
/// Parses + validates the (platform, native_id) path pair -> MeetingKey.
pub fn parse_key(platform: &str, native_id: &str) -> Result<crate::state::MeetingKey, ApiError>;
/// Step 5 of `create_bot`, pure so the §1.3.2 policy is testable without an
/// AppState. `Err(Unavailable)` only when `require_whisper && !whisper_up`.
pub fn whisper_gate(require_whisper: bool, whisper_up: bool, endpoint: &str) -> Result<(), ApiError>;
```

`create_bot` order of operations (fixed, so the 403/409 semantics are stable):

1. `check_api_key` → 401
2. `parse_key` → 400
   (v1.2's step 2b — a 400 for `"platform": "teams"` — is REMOVED. Teams is a
   supported platform; see §1.3.1.)
3. `db.active_meeting(&key)?.is_some()` → 409
4. `state.slots.clone().try_acquire_owned()` fails → **403 `TooManyBots`**
5. `transcribe_enabled && !whisper.health().await` → `whisper_gate` → 503 when
   `cfg.require_whisper_for_bots` (the default; permit dropped), otherwise a WARN
   and the bot is accepted anyway — see §1.3.2
6. `db.create_meeting(...)` → row in `Requested`
7. `session::spawn(state, spec, permit)` → 201

---

## 10. Acceptance tests (must pass before this replaces vexa-lite)

1. `curl localhost:8060/` → 200 JSON.
2. `POST /bots` with the exact body from §1.3 → 201; a second identical POST → 409; five concurrent distinct meetings → the 5th gets 403 with `maximum concurrent bot limit (4)`.
3. `GET /transcripts/google_meet/never-sent-xyz` → 404 (zombie guard).
4. Any request without `X-API-Key` → 401.
5. Bot sent to a meeting it is never admitted to → after `admission_timeout_min`, `GET /transcripts/...` returns `status="completed"`, `segments=[]`, `start_time=null`. `vexa_bots.py pull --meet <id>` prints `SKIPPED: ... not admitted / no audio` and writes `skipped_not_admitted` into `vexa_state.json`. **No transcript file, no MOM, heartbeat `ok`.**
6. Bot in a real meeting with speech → terminal `completed` with `segments[].start_time` as floats; `vexa_bots.py pull` writes a `.md` + `.txt` and drafts a MOM.
7. `DELETE /bots/...` mid-call → terminal `stopped` with the partial segments; `vexa_bots.py pull` emits the ⚠ PARTIAL banner and a `fail` heartbeat.
8. Whisper outage, split into the two cases the v1.2 §0.1 row separates:
   - **No audio captured** (never admitted, denied, stopped in the waiting room) → terminal `completed`, `segments=[]`, `start_time=null`, heartbeat `ok`. Unchanged, and this is the case the pre-join 503 gate cannot cover.
   - **Admitted, audio captured, whisper down, zero segments** → terminal `failed` with an `error` naming the endpoint. **This inverts the v1.0 wording of this test**, which asserted `completed` and thereby specified the exact silent-data-loss bug the review found: the client would file a real meeting as `skipped_not_admitted` with a green heartbeat and no MOM. Finalization must also stay inside `FINALIZE_BUDGET` here — a bot that heard a 60-minute meeting against a hung whisper finalizes in seconds, not hours. Since v1.3 that budget is one deadline shared by the relay, the segmenter and the transcriber (plus a separate `BROWSER_TEARDOWN_BUDGET` for step 1); verifying only the transcriber await is bounded is not sufficient, because backpressure moves the stall upstream.
   - **Admitted, audio captured, whisper reachable but failing every request** (500s, or 200 with an empty transcript) → terminal `failed`. `health()` returns *up* here, so this case is caught by the counters (`lost() > 0`), never by the probe. Added v1.2.1.
   - **Admitted, audio captured, whisper healthy, nobody spoke** → still terminal `completed`, `segments=[]`. This is the regression that must never break, and it must reach `completed` on the counters alone when `seen > 0`, without a finalize-time probe that could blip.
9. Kill and restart meetbot with a live session → that row is `failed` on next read, not stuck non-terminal.
10. `python3 vexa_bots.py setup` against meetbot writes a working `vexa_token.env`.
