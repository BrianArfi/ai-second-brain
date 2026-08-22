# Meeting Recorder (local note-taker)

Record meetings on your own machine, transcribe them locally on your GPU (or via the
Gemini API), and get a meeting-minutes (MOM) draft automatically.

**This is the default way the harness gets meeting notes.** It needs no account, no API
key, and no per-seat subscription, and the audio never leaves your machine. A cloud
recorder like Fathom is an optional addition rather than a prerequisite. If you do use
one, run both: the local recorder writes into the same registry Fathom does, so one
meeting still produces one MOM.

It lives in `meeting-recorder/`. Everything is plain Python (stdlib) plus `ffmpeg`;
the local-GPU transcription step is optional.

- [How it works](#how-it-works)
- [Platform support](#platform-support)
- [Quick start](#quick-start)
- [Transcription engines](#transcription-engines)
- [Daily use](#daily-use)
- [Advanced: auto-join bot (meetbot)](#advanced-auto-join-bot-meetbot)
- [Advanced: Vexa auto-join bot](#advanced-vexa-auto-join-bot)
- [Troubleshooting](#troubleshooting)

---

## How it works

```
recorder.py  →  .wav in your recordings folder  →  watcher.py
                                                     ├─ transcribe.py  (whisper.cpp GPU, or Gemini)
                                                     ├─ journal/fathom_registry.json   (dedupe key)
                                                     └─ MOM draft in your Clients/<context>/meetings folder
```

1. **Capture**: `recorder.py` records system audio + mic to a `.wav` (optionally a screen-record `.mp4` too).
2. **Watch**: `watcher.py` notices new audio files and runs the pipeline.
3. **Transcribe**: `transcribe.py` turns audio into a timestamped transcript.
4. **Draft**: the transcript is turned into a MOM draft you review (never sent anywhere).

Steps 2 to 4 also run standalone, so you can drop in an `.m4a` from any source and get the same result.

---

## Platform support

The recorder runs on macOS, Windows, and Linux/WSL. What differs is how each OS captures audio:

| Platform | Audio capture | Local GPU transcription | Notes |
|---|---|---|---|
| **macOS** (Apple Silicon) | `ffmpeg` + avfoundation; use a loopback device (e.g. BlackHole) for system audio | whisper.cpp with Metal (`brew install whisper-cpp`) | Fully native. |
| **Windows** | `pyaudiowpatch` (WASAPI loopback, captures system audio cleanly) | whisper.cpp (Vulkan/CUDA) via `whisper-server.exe` | A small GUI (`gui_win.pyw`) is included. |
| **Linux / WSL** | `ffmpeg` + PulseAudio (`<sink>.monitor`) | whisper.cpp (CUDA/Vulkan) if you have a GPU | In WSL, capture often runs on the Windows side and the pipeline runs in WSL. |

If you have no GPU, skip local transcription and use the Gemini engine (see [engines](#transcription-engines)); everything else works the same.

---

## Quick start

### 1. Install prerequisites

`ffmpeg` is the only hard requirement.

```bash
# macOS
brew install ffmpeg
brew install whisper-cpp          # optional: local GPU transcription

# Linux / WSL (Debian/Ubuntu)
sudo apt install -y ffmpeg pulseaudio-utils

# Windows (in your Windows Python, for capture)
pip install PyAudioWPatch
```

For local GPU transcription you need a whisper.cpp build and a model file
(`ggml-large-v3-turbo.bin` is a good default). Building whisper.cpp is per-platform;
see the whisper.cpp project. This step is optional.

### 2. Configure

```bash
cp meeting-recorder/config.example.json meeting-recorder/config.json
```

Open `config.json` and, under the section for your platform (`macos` / `windows` / `wsl`), set:

- `recordings_dir`: where `.wav` files are written.
- `whispercpp_bin` and `whispercpp_model`: paths to your whisper.cpp binary + model. Leave empty to skip local GPU and use Gemini.
- macOS only: `avfoundation_audio_device`: the loopback device index from `--list-devices`.

### 3. Record and process

```bash
# List audio devices (find your loopback/monitor device)
python3 meeting-recorder/recorder.py --list-devices

# Record (Ctrl-C to stop)
python3 meeting-recorder/recorder.py "Sprint Planning"

# Process every new recording once (transcribe + MOM draft), then exit
python3 meeting-recorder/watcher.py --once
```

On Windows you can instead double-click the `gui_win.pyw` GUI: type a meeting name,
click Start / Stop, and optionally tick "Auto-process after stop."

---

## Transcription providers

**Start here:**

```bash
python3 meeting-recorder/transcribe.py --doctor
```

It prints every provider in the order it will be tried, whether this machine can
actually use it, and the exact step to fix the ones it cannot. Nothing else in this
section is worth reading until that command tells you something is wrong.

Providers live in `config.json` under `transcription.providers` and are tried in
order until one succeeds. Adding a provider is a config entry, not a code change.

| Provider | Cost | Speaker labels | Needs |
| :--- | :--- | :--- | :--- |
| `gemini` | free tier | **yes** | key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `groq` | free tier | no | key from [console.groq.com/keys](https://console.groq.com/keys) |
| `openai` | paid | no | key from [platform.openai.com](https://platform.openai.com/api-keys) |
| `whispercpp` | free | no | a GPU, the binary, and a model file |
| `cpu` | free | no | `pip install faster-whisper`, and patience |

Gemini leads the default chain because it is the only one that returns speaker
labels. Everything else produces one unattributed stream, which a MOM can still be
drafted from, but attribution has to come from you.

### Where keys go

Read from the environment first, then from the first of these that has the line:

```
meeting-recorder/.env
.env               (workspace root)
secrets.env        (workspace root)
```

One `KEY=value` per line. All three are gitignored.

```bash
echo 'GEMINI_API_KEY=your-key-here' >> meeting-recorder/.env
```

### The CPU fallback

`transcription.cpu_fallback` decides what happens when nothing else is available.
When `true` (the default for a new install), transcription runs locally on the CPU
so the pipeline works with no account and no GPU. It warns first, because it takes
roughly as long as the meeting itself. When `false`, the run fails and names every
provider it tried instead.

CPU is always appended **last** and never sits mid-chain. A fallback that always
"works" would otherwise hide a misconfigured fast provider behind an hour of
grinding laptop.

### If your Gemini access comes from a local router

Some setups reach Gemini through a local LLM router rather than an API key. Set
`transcription.autodetect_local: true` and the recorder probes `127.0.0.1` for one,
adds whatever it finds to the chain, and picks a model the router claims can take
audio.

That claim is **not trusted**, and this is the important part.

### Provider verification, and why it exists

On 21 Aug 2026 a local router advertised `audioInput: true` on nine Gemini models.
Every one of them accepted the audio and threw it away. One model was honest
("it appears that you forgot to attach the audio file"). The other six returned
fluent, speaker-labelled, entirely invented meetings:

> **[00:00]** Speaker 1: Good morning, everyone. Thanks for joining today's strategy sync.

The clip actually said "The violet kangaroo audits seventeen bridges in Lisbon."
Nothing errored. Left alone, that fiction becomes a MOM.

So any provider reached over a **non-default endpoint** must first transcribe a
known 3-second clip (`fixtures/probe.ogg`) and match its ground truth
(`fixtures/probe.json`) before it may touch a real meeting. The sentence is
deliberately absurd, so a model that never heard the audio cannot guess it.

```bash
python3 meeting-recorder/transcribe.py --verify-providers
```

Results cache in `meeting-recorder/provider_probe.json`. A provider that fails is
disabled and the reason is kept; `--doctor` shows it. Delete that file to re-test.
Stock cloud endpoints are not probed, because the risk comes from a proxy in the
middle rather than from the vendor. Force a probe on any provider with
`"verify": true`.

### Any OpenAI-compatible endpoint

Set `base_url` on an `openai` provider to point anywhere that speaks
`/audio/transcriptions` -- a self-hosted whisper server, another vendor, a router:

```json
{"kind": "openai", "base_url": "http://127.0.0.1:8080/v1", "model": "whisper-1", "api_key_env": "MY_KEY"}
```

Files over the 25 MB API cap are compressed to 16 kHz mono Opus and, if still too
large, split on time with each part's timestamps shifted, rather than the first
25 MB being transcribed and the rest silently dropped.

### Running one file

```bash
python3 meeting-recorder/transcribe.py --in recording.m4a --out transcript.md
python3 meeting-recorder/transcribe.py --in recording.m4a --out transcript.md --engine groq
```

`--engine` pins a single provider. `cli` is still accepted as an alias for `gemini`.

---

## Daily use

Leave the watcher running so recordings are processed as they land:

```bash
python3 meeting-recorder/watcher.py          # poll loop (default every 30s)
```

Each processed meeting writes:

- a timestamped transcript into your meetings/transcripts folder,
- a MOM draft into your meetings folder (review before sharing),
- an entry in `journal/fathom_registry.json` so the same meeting is not double-drafted if you also use Fathom.

To wire it to your calendar and MOM template, see the paths in `meeting-recorder/watcher.py`
(it invokes your calendar connector to match a recording to a calendar event).

---

## Recording from the phone

For a meeting in a room, away from the laptop. The phone runs a small web app that
records and streams the audio to whichever machine answers; from there the pipeline
above is unchanged, because the phone lands exactly the two files `recorder.py`
would have written.

```
phone (browser)  --15s chunks-->  ingest_server.py  -->  <recordings_dir>/x.m4a + x.json
                                                            |
                                                            +--> watcher.py --file  (fired directly)
```

### Why it is a web app and why Tailscale is required

A browser refuses the microphone to any page that is not a **secure context**, so
`http://192.168.1.x:8787` cannot record and a self-signed certificate does not help.
Tailscale issues a real Let's Encrypt certificate for `<machine>.<tailnet>.ts.net`,
which makes one hostname solve three problems at once: microphone permission,
transport on the LAN (Tailscale goes peer-to-peer, so no internet round-trip), and
transport from cellular.

### One-time setup

1. Install Tailscale on the phone and on every machine that should receive recordings,
   all signed into the same tailnet. Enable HTTPS in the admin console.
2. On each machine:
   ```bash
   tailscale cert <machine>.<tailnet>.ts.net
   tailscale serve --bg --https=443 http://127.0.0.1:8787
   ```
3. List every machine's public address in `meeting-recorder/config.json` under
   `ingest.hosts`. The phone reads this from `/health` and learns the other machines
   by itself, so the address is typed once, not on every device.
4. Print the pairing token and keep it for step 5:
   ```bash
   python3 meeting-recorder/ingest_server.py --print-token
   ```
5. On the phone, open `https://<machine>.<tailnet>.ts.net`, expand **Settings**, paste
   the token, press Save. Then Chrome menu → **Add to Home Screen**.

The server itself starts automatically at the beginning of every Claude Code session
(`.agent/scripts/ensure_ingest.sh`, the same health-check-and-replace pattern as the
dashboard). To run it by hand:

```bash
python3 meeting-recorder/ingest_server.py            # port + bind come from config.json
python3 meeting-recorder/ingest_server.py --bind 0.0.0.0   # plain-LAN testing only
```

### How a phone recording behaves

- **Chunked while recording.** A piece goes out every 15 seconds. Android eventually
  kills backgrounded tabs, so a tab that dies at minute 47 of a 60-minute meeting has
  already delivered 47 minutes; the server's stale sweep (`ingest.stale_minutes`,
  default 30) then finishes that session into a real transcript instead of losing it.
- **Works with no network.** Chunks that cannot be sent are held in IndexedDB on the
  phone, and the session is opened late with its original start time attached, so the
  file stamp still describes the meeting rather than the upload.
- **Ad-hoc by default.** A phone recording is usually a room conversation, so calendar
  matching is skipped and the typed title wins. Turn the toggle off only when the
  recording really matches a calendar event.
- **Fires the watcher directly** rather than waiting for cron, because macOS has no
  crontab: a recording landing on the Mac would otherwise never be processed.

### Limits worth knowing

- Android does not let any app, web or native, capture another app's audio. A phone
  recording is **the room through the microphone**. For a Google Meet call the laptop
  path is still better.
- A browser tab is less durable than a native foreground service. Chunking bounds the
  loss to the last 15 seconds; it does not remove it.
- The pairing token lives in `meeting-recorder/ingest_token.env` (gitignored, `0600`).
  Reachability is already limited to your own tailnet; the token is the second layer.

---

## Advanced: auto-join bot (meetbot)

`meetbot/` is a single Rust service that joins your Google Meet (and Teams) calls on its
own, captures the call audio in the browser, and pushes it through the same transcription
and minutes pipeline as the local recorder. It exposes the same HTTP API as the Vexa stack
below, so `vexa_bots.py` drives either one and you switch with a single environment
variable.

It replaced a 1.25 GB always-on Docker stack (app + Postgres + object storage) with a
process that idles at a few MB and only spends memory while a bot is actually in a call.

```
calendar → vexa_bots.py auto (cron */5) → POST /bots → meetbot
                                                        ├─ headless Chromium joins the call
                                                        ├─ in-page WebAudio tap → PCM
                                                        ├─ chunks → your whisper server
                                                        └─ segments → SQLite → GET /transcripts
```

### Setup

Requires Rust and a Chromium build (Playwright's works). See `docs/SETUP.md` section 10.2
for the commands. Two things decide whether it works at all:

**The bot needs a real Google identity.** Meet runs a bot check on the knock itself and
auto-declines anonymous guests roughly 1.5 seconds later. Seed a Chrome profile by signing
in once with a visible browser, then point `profile_template` at it. Each session gets a
*copy*, because Chrome locks a profile directory and a session must never be able to
invalidate the stored login.

**Automation has to be invisible to Google.** Two signals are independently fatal: a
`HeadlessChrome` User-Agent token, and `navigator.webdriver` being true (CDP libraries
often add `--enable-automation` themselves, so the disabling flag is mandatory). Either one
produces the same symptom — refused in about five seconds, with no name field and no join
button rendered at all.

### Running it

```bash
systemctl --user status meetbot.service
journalctl --user -u meetbot.service -f      # watch a live join
./target/release/meetbot doctor <meet-code>  # dry-run the join, name the broken step
```

`doctor` is the tool to reach for first when a join breaks: it walks the whole dance and
tells you which selector stopped matching. Google changes the Meet UI a few times a year,
and every selector lives in one table at the top of `src/meet.rs` for exactly that reason.

### Known sharp edges

- **If you sign the bot in as yourself and you are already in the call**, Meet offers both
  `Join here too` and `Switch here`. The second one *moves* your session to the bot and
  drops you. Selector priority is what keeps the bot on the safe one.
- **`Join here too` is hidden** inside a collapsed `Other ways to join` disclosure, and
  collapsed elements are invisible to a size-based matcher. It has to be expanded first.
- **One error string means two different failures.** `You can't join this video call`
  covers both "refused before the green room" and "your knock was declined". The only way
  to tell them apart is whether a join click was logged first.

A fuller list, including the failure modes that cost the most time to diagnose, is in
`.agent/skills/meetbot/SKILL.md`.

---

## Advanced: Vexa auto-join bot

`vexa_bots.py` sends a bot to auto-join and transcribe your Google Meet / Teams calls,
so you do not have to record manually. **This is an advanced, optional path.** It requires
a self-hosted [Vexa](https://github.com/Vexa-ai/vexa) stack (Docker: the Vexa container,
Postgres, object storage, and a whisper transcription service). It is heavier to run than
the local recorder and is best on a Linux/WSL host with Docker.

```bash
cp meeting-recorder/vexa_token.env.example meeting-recorder/vexa_token.env
# fill in VEXA_API_KEY / VEXA_USER_ID from your Vexa instance

python3 meeting-recorder/vexa_bots.py status          # check the stack
python3 meeting-recorder/vexa_bots.py auto --dry-run  # preview which calls it would join
```

Put `vexa_bots.py auto` on a `*/5 * * * *` cron to auto-join every meeting with a link.
If you do not run a Vexa server, ignore this section entirely; the local recorder does not need it.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No audio / silent recording | Run `--list-devices` and set the right loopback/monitor device. On macOS install BlackHole; on Linux check `pactl list sources`. |
| Local transcription skipped | The GPU probe failed. Check `whispercpp_bin`/`whispercpp_model` paths, or set `engine: "cli"` to use Gemini. |
| "no module named pyaudiowpatch" (Windows) | `pip install PyAudioWPatch` in the Windows Python that runs `recorder.py`. |
| MOM draft step fails | The transcript still lands. The draft step needs your draft backend (e.g. agy-bridge) configured; check `draft_backend` in `config.json`. |
| Gemini engine errors | Confirm your Google AI key and that `gemini_model` in `config.json` is a model you have access to. |
