# Setup Guide

This guide walks you through setting up AI Second Brain from scratch.
Estimated time: **2–4 hours**, mostly spent on Google OAuth.

---

## Table of Contents

0. [Quick-Start: Conversational Brain (15 Mins)](#0-quick-start-conversational-brain-15-mins) ← *Fastest path!*
1. [System Requirements](#1-system-requirements)
2. [macOS Setup](#2-macos-setup)
3. [Windows / WSL Setup](#3-windows--wsl-setup)
4. [Install Python Dependencies](#4-install-python-dependencies)
5. [Install Claude Code](#5-install-claude-code)
6. [Configure Environment Variables](#6-configure-environment-variables)
7. [Google OAuth Setup](#7-google-oauth-setup) ← most involved step
8. [Slack Setup](#8-slack-setup)
9. [Other API Keys](#9-other-api-keys)
10. [Which Skills Do You Actually Need?](#10-which-skills-do-you-actually-need)
11. [Browser Service Setup](#11-browser-service-setup)
12. [Verify Everything Works](#12-verify-everything-works)
13. [Troubleshooting](#13-troubleshooting)

---

## 0. Quick-Start: Conversational Brain (15 Mins)

If you only want a smart, local AI companion that knows your context, helps you manage markdown notes, drafts PRDs, and acts as your conversational second brain in your editor, **you do not need any complex programming setup (like Git, Homebrew, Python, Node, or WSL)!**

Follow these 3 simple steps:

### Step 1: Install VS Code
*   Download and install **VS Code** (if not already installed).
    *   [Download VS Code](https://code.visualstudio.com/)

### Step 2: Download the Repository
You need the workspace files to start your Second Brain. Choose one of two easy methods:
*   **Method A: Clone via Git (For Developers)**
    Open your terminal and run:
    ```bash
    git clone https://github.com/BrianArfi/ai-second-brain.git
    ```
*   **Method B: Download as ZIP (No Git required!)**
    *   [Download the ZIP File directly](https://github.com/BrianArfi/ai-second-brain/archive/refs/heads/main.zip)
    *   Extract the downloaded ZIP file to a folder on your computer (e.g., your Desktop or Documents).

### Step 3: Open the Folder in VS Code
*   Open VS Code.
*   Select **File → Open Folder...** from the top menu.
*   Choose the `ai-second-brain` folder you just cloned or extracted.

### Step 4: Configure your CLAUDE.md
*   Rename the file `CLAUDE.md.template` in the root of the folder to `CLAUDE.md`.
*   Open `CLAUDE.md` and customize the **"Who You're Helping"** and **"Work Contexts"** sections with your details.
*   Open the AI Sidebar in VS Code (like Claude Sidebar, GitHub Copilot Chat, or your favorite AI Extension) and start chatting! The AI extension will automatically read your `CLAUDE.md` rules and help you manage your files.

---

*If you want to unlock automated data sync, calendar updates, Slack triggers, and Python scripting integrations, proceed to the sections below for **Level 2: Advanced Setup**.*

---

## 1. System Requirements

| Requirement | Minimum | Notes |
|---|---|---|
| OS | macOS 12+ / Ubuntu 20.04+ / WSL2 | macOS is native (highly recommended!) |
| Python | 3.8+ | `python3 --version` to check |
| Node.js | 18+ | For Claude Code CLI |
| RAM | 4 GB | 8 GB recommended |
| Storage | 2 GB free | For Chrome, dependencies, outputs |
| ffmpeg | any recent | Needed by the local meeting recorder. `brew install ffmpeg` (macOS) or `sudo apt install -y ffmpeg pulseaudio-utils` (Linux/WSL) |

---

## 2. macOS Setup

Most of our team uses MacBooks. The setup here is completely native and does not require virtual machines or WSL.

### Step 1: Install Homebrew (if not already installed)

Open your Terminal and run:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Step 2: Install Python, Node.js, and Git

Run the following command in your terminal:

```bash
brew install git python node
```

### Step 3: Verify Installation

Ensure all versions are correct:

```bash
git --version
python3 --version  # should show 3.8+
node --version     # should show v18+ or v20+
npm --version
```

---

## 3. Windows / WSL Setup

> Skip this section if you're on macOS or already running Ubuntu/Linux.

Most corporate laptops run Windows. This repo runs inside WSL2 (Windows Subsystem for Linux).

### Install WSL2

Open PowerShell as Administrator and run:

```powershell
wsl --install
```

Restart your computer when prompted. This installs Ubuntu by default.

### Open WSL

After restart, search for "Ubuntu" in the Start menu and open it. You'll land in a Linux terminal.

### Install prerequisites inside WSL

```bash
sudo apt update
sudo apt install -y python3 python3-pip git curl build-essential
```

### Install Node.js inside WSL

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

Verify:
```bash
node --version   # should show v20.x.x
npm --version
```

> **All subsequent commands in this guide run inside your native terminal (macOS) or inside WSL (Windows)**, not Windows PowerShell.

---

## 4. Install Python Dependencies

You have two options to install the required Python libraries:

### Option A: Agent-Driven Setup (Recommended — Let the AI do it!) 🤖

If you are using **VS Code** (with an agentic AI extension) or **Claude Code**, the AI companion can run commands and set up the dependencies for you. 

**The Setup Prompt:**
Open your AI sidebar chat or terminal agent and type:
> *"Set up this repository for me. Read requirements.txt and install the Python dependencies in my active terminal environment."*

The AI will automatically run the installation commands and configure everything in your terminal!

### Option B: Manual Setup (Do It Yourself) 🛠️

From inside the repo directory, run:

```bash
pip install -r requirements.txt
playwright install chromium
```

`requirements.txt` contains:

```
google-auth>=2.0.0
google-auth-oauthlib>=1.0.0
google-auth-httplib2>=0.2.0
google-api-python-client>=2.0.0
requests>=2.28.0
openpyxl>=3.9.0
playwright>=1.40.0
```

---

## 5. Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

Verify:

```bash
claude --version
```

You'll need a Claude account. Sign up at [claude.ai](https://claude.ai) if you haven't.

**Cost note**: Claude Code uses the Claude API, billed by token usage. Light usage (a few tasks/day) typically costs $20–50/month. Heavy automation can go higher. Monitor your usage at [console.anthropic.com](https://console.anthropic.com).

---

## 6. Configure Environment Variables

You have two options to set up your environment files and folders:

### Option A: Agent-Driven Setup (Recommended — Let the AI do it!) 🤖

You don't need to manually copy files or navigate folder structures. Simply ask your AI companion in the sidebar:
> *"Create my local environment files for me. Copy .env.example to .env, and create placeholder token.env files in the relevant skill folders like slack-connector and fathom-connector."*

The AI will automatically duplicate the templates, set up the folders, and prep them for your API keys!

### Option B: Manual Setup (Do It Yourself) 🛠️

Run the copy command in your terminal:

```bash
cp .env.example .env
```

Open `.env` and fill in the keys you have. You don't need all of them — only the services you plan to use. See section 10 for which ones are essential.

### Set your identity (required, don't skip this)

The `.env` file starts with an **Identity block**. Several skills use it to answer
"who does this harness work for?". If you leave the defaults, docs get shared to
a placeholder domain and action-item matching looks for someone else's name.

```bash
WORK_DOMAIN=yourcompany.com        # your Google Workspace domain
OWNER_EMAIL=you@example.com        # your personal Google account
OWNER_WORK_EMAIL=you@yourcompany.com
OWNER_NAME_TOKENS=jane doe,jane m doe   # name variants meeting tools use for you
OWNER_SLACK_ID=<SLACK_ID>           # Slack → Profile → ⋮ → Copy member ID
```

What each one drives:

| Variable | Used by | What happens if wrong |
| :--- | :--- | :--- |
| `WORK_DOMAIN` | every Drive and Docs writer (through `apply_visibility` in `.agent/scripts/file_utils.py`), `drive_permissions.py` | New docs get domain-shared to the wrong workspace |
| `OWNER_WORK_EMAIL` | `gmail-connector` auth flow | The auth prompt suggests the wrong account |
| `OWNER_NAME_TOKENS` | `commitment-ledger` | Action items assigned to YOU are never picked up |
| `OWNER_SLACK_ID` | `commitment-ledger`, Slack sweeps | Mentions of you are missed |

**Doc sharing:** every writer shares a new or updated work doc with your `WORK_DOMAIN`
as commenters, and removes any "anyone with the link" access it finds. Pass
`--visibility public` (or `--share`) only when you mean to publish. To audit an older doc:

```bash
python3 .agent/scripts/drive_permissions.py restrict <DOC_ID> --domain $WORK_DOMAIN --apply
```

For each skill that needs its own token, create a `token.env` inside that skill's folder:

```bash
# Example: Slack
echo "SLACK_BOT_TOKEN=<SLACK_TOKEN>" > .agent/skills/slack-connector/token.env

# Example: Fathom (optional cloud recorder; the local recorder needs no key)
echo "FATHOM_API_KEY=your-key-here" > .agent/skills/fathom-connector/token.env
```

---

## 7. Google OAuth Setup

This is the most involved step. Follow carefully — it's a one-time setup.

### What you'll get

One OAuth credential that lets the AI read/write your Google Drive, Calendar, and Gmail on your behalf. You can set this up for a work account, personal account, or both.

### Step 1: Create a Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown at the top → **New Project**
3. Name it `ai-second-brain` (or anything you'll recognize)
4. Click **Create**
5. Make sure your new project is selected in the dropdown

### Step 2: Enable the APIs you need

In the left sidebar, go to **APIs & Services → Library**.

Search for and enable each of these (click the API name → click **Enable**):

- **Google Drive API** — for reading/writing files
- **Google Docs API** — for creating real Google Docs
- **Google Sheets API** — for reading/writing spreadsheets
- **Google Calendar API** — for reading your calendar
- **Gmail API** — only if you want email access

### Step 3: Configure the OAuth consent screen

1. Go to **APIs & Services → OAuth consent screen**
2. User Type: select **External** → click **Create**
3. Fill in:
   - App name: `AI Second Brain`
   - User support email: your email
   - Developer contact: your email
4. Click **Save and Continue**
5. On the **Scopes** screen: click **Save and Continue** (we'll use pre-approved scopes)
6. On the **Test users** screen: click **+ Add Users** → add your own email address → click **Save and Continue**
7. Click **Back to Dashboard**

### Step 4: Create OAuth credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth 2.0 Client IDs**
3. Application type: **Desktop app**
4. Name: `AI Second Brain Desktop`
5. Click **Create**
6. In the popup, click **Download JSON**
7. Rename the downloaded file to `credentials.json`

### Step 5: Place credentials.json

Copy the file into the connector folder for the account you're setting up:

```bash
# For your primary work account
cp ~/Downloads/credentials.json .agent/skills/work-drive-connector/credentials.json

# For your personal Google account (if using both)
cp ~/Downloads/credentials.json .agent/skills/personal-drive-connector/credentials.json
```

### Step 6: Run the first authentication

```bash
python3 .agent/skills/work-drive-connector/gdrive_manager.py search --query "test"
```

You'll see output like:

```
[Work Drive] Authentication Required!

1. Visit this URL in your browser:
   https://accounts.google.com/o/oauth2/v2/auth?...

2. Authorize the application. If you see "Google hasn't verified this app",
   click "Advanced" → "Go to AI Second Brain (unsafe)" — this is expected
   for personal Cloud projects.

3. After authorizing, the browser will redirect to a page that may fail to load.
   That's fine. Copy the full URL from the address bar.
   It will look like: http://localhost:8080/?code=4/0AcXXX...

4. Paste just the code value (after code=) here:
```

Paste the code and press Enter. The token is saved automatically. You won't need to do this again unless the token is explicitly revoked.

### Step 7: Verify it worked

```bash
python3 .agent/skills/work-drive-connector/gdrive_manager.py search --query "test"
```

You should see a list of files from your Drive (or an empty result if "test" matches nothing — that's fine).

---

## 8. Slack Setup

You can set up Slack in one of two ways depending on your needs and workspace permissions:

*   **Option A: User Token (xoxp-) [RECOMMENDED for Personal Productivity]**
    *   **Pros**: The AI acts directly on your behalf. It automatically sees the exact same channels, threads, and DMs that you have access to. **No need to invite a bot (`/invite`) to every channel!**
    *   **Cons**: Permissions are tied entirely to your personal Slack account.
*   **Option B: Bot Token (xoxb-) [Standard App Integration]**
    *   **Pros**: Acts as a separate, distinct bot user. Excellent for shared team integrations.
    *   **Cons**: You must manually invite the bot to every channel it needs to access, and some corporate workspaces restrict bot installations.

---

### Step 1: Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App → From Scratch**
3. App name: `AI Second Brain`
4. Pick your workspace → click **Create App**

### Step 2: Configure Scopes

In the left sidebar, go to **OAuth & Permissions**, scroll down to **Scopes**, and add the scopes based on your chosen option:

*   **For Option A (User Token - xoxp-):** Add these under **User Token Scopes**
*   **For Option B (Bot Token - xoxb-):** Add these under **Bot Token Scopes**

Add these scopes:

```
channels:read
channels:history
chat:write
files:read
groups:read
groups:history
im:read
im:history
mpim:read
mpim:history
users:read
reactions:read
```

*(Note: If using Bot Token Option B, also add `channels:join` and `chat:write.public` so the bot can auto-join channels).*

### Step 3: Install the App & Save Token

1. Scroll to the top of the **OAuth & Permissions** page.
2. Click **Install to Workspace** → **Allow**.
3. Copy your token:
   *   For **Option A (User Token)**: Copy the **User OAuth Token** (starts with `xoxp-`).
   *   For **Option B (Bot Token)**: Copy the **Bot User OAuth Token** (starts with `xoxb-`).
4. Save the token in `.agent/skills/slack-connector/token.env`:

```bash
# For Option A (User Token):
echo "SLACK_USER_TOKEN=<SLACK_TOKEN>" > .agent/skills/slack-connector/token.env

# For Option B (Bot Token):
echo "SLACK_BOT_TOKEN=<SLACK_TOKEN>" > .agent/skills/slack-connector/token.env
```

---

## 9. Other API Keys

| Service | Where to get it | Where to save it |
|---|---|---|
| **Fathom** *(optional)* | [fathom.video](https://fathom.video) → Settings → Integrations → API Token | `.agent/skills/fathom-connector/token.env` as `FATHOM_API_KEY=...`. Only if you use Fathom on top of the local recorder (section 10.1) |
| **Figma** | figma.com → Account Settings → Personal Access Tokens | `.agent/skills/figma-connector/token.env` as `FIGMA_ACCESS_TOKEN=...` |
| **Mixpanel** | mixpanel.com → Settings → Project Settings → Access Keys | `.agent/skills/mixpanel-connector/token.env` (see template inside) |
| **ClickUp** | app.clickup.com → Settings → Apps → API Token | `.agent/skills/clickup-connector/token.env` as `CLICKUP_ACCESS_TOKEN=...` |

---

## 10. Which Skills Do You Actually Need?

You don't need to set up everything. Use this decision tree:

```
Do you use Google Workspace (Drive, Docs, Calendar)?
  YES → Set up Google OAuth (section 7) — this is the foundation
  NO  → Skip to Slack or other services

Do you want your meetings turned into notes automatically?
  YES → Set up the local meeting recorder (the default, see section 10.1)
        It runs on your own machine. No account, no API key, no per-seat cost.
  NO  → Skip (meeting notes will need manual input)

  ...and do you ALSO use a cloud recorder (Fathom)?
    YES → Optionally set up fathom-connector as well; both write to the same
          registry, so one meeting still produces one set of notes
    NO  → Skip it. The local recorder covers this on its own

Do you use Slack at work?
  YES → Set up slack-connector
  NO  → Skip

Do you use Figma for design?
  YES → Set up figma-connector
  NO  → Skip

Do you have product analytics in Mixpanel?
  YES → Set up mixpanel-connector
  NO  → Skip

Do you manage tasks in ClickUp?
  YES → Set up clickup-connector
  NO  → Skip (most PM work is tracked in todo.md directly)
```

**Minimum viable setup** (gets you 80% of the value):
- Google Drive + Docs (work-drive-connector)
- Google Calendar (google-calendar-connector)
- Local meeting recorder (`meeting-recorder/`, nothing to sign up for)
- Slack (if your team uses it)

Fathom and the other cloud connectors are add-ons. Set them up only if you already use those
services.

### 10.1 Meeting recorder (the default path)

Meeting notes are one of the highest-value things the harness does, so the recorder is set up by
default rather than treated as an extra. It records the meeting on your machine, transcribes it
locally, and leaves a minutes draft for you to review. Nothing is uploaded and no third party sees
the audio.

It is plain Python from the standard library plus `ffmpeg`, so there is no `pip install` step. The
whole setup is three commands:

```bash
# 1. Copy the config and fill in your platform's section
cp meeting-recorder/config.example.json meeting-recorder/config.json

# 2. Find your loopback/monitor audio device
python3 meeting-recorder/recorder.py --list-devices

# 3. Record a two-minute test meeting, then process it
python3 meeting-recorder/recorder.py "Test Meeting"     # Ctrl-C to stop
python3 meeting-recorder/watcher.py --once
```

You should end up with a transcript and a `MOM_*.md` draft in your meetings folder. If you do, the
recorder is working.

Two things worth knowing before you start:

- **Audio capture differs per OS.** macOS needs a loopback device such as BlackHole, Windows uses
  WASAPI loopback (`pip install PyAudioWPatch` in your *Windows* Python), Linux and WSL use a
  PulseAudio `.monitor` source.
- **Local GPU transcription is optional.** With a whisper.cpp build the transcription runs on your
  own GPU. Without one, leave `whispercpp_bin` empty and it falls back to the Gemini API.

Full guide including engines, daily use, the auto-join bot, and troubleshooting:
**`docs/MEETING_RECORDER.md`**.

### 10.2 Auto-join bot (optional, for meetings you cannot attend)

The recorder above covers meetings you sit in. `meetbot/` covers the ones you miss: a small
Rust service that reads your calendar, joins the call in a headless browser, captures the
audio, and feeds it to the same transcription + minutes pipeline. It replaced a 1.25 GB
Docker stack and idles at a few MB.

It is optional and strictly more work than the local recorder, so set it up only once that
one is working. Requires Rust and a Chromium build (Playwright's is fine).

```bash
cd meetbot
cp config.example.toml config.toml     # then edit the paths + set admin_token
cargo build --release
./target/release/meetbot doctor <a-meet-code>   # dry-runs the join, names any broken step
```

Then install `meetbot.service` as a systemd user unit and point the recorder's cron line at
it with `MEETBOT=1 VEXA_API_BASE=http://localhost:8060`.

The thing that decides whether this works at all: **the bot must be signed in to a real
Google account.** Anonymous guests are auto-declined by Meet's bot check about 1.5 seconds
after knocking. Seed a Chrome profile once, by hand, with a visible browser:

```bash
<chromium> --user-data-dir=<meetbot>/data/bot-profile https://accounts.google.com/
```

Sign in, close the window, and point `profile_template` at that directory. Each session then
gets a *copy* of it, because Chrome locks a profile directory and a session must never be
able to invalidate your stored login.

Read `.agent/skills/meetbot/SKILL.md` before debugging a join failure. It documents the
landmines that are genuinely hard to rediscover — automation fingerprints Google rejects,
one error string that means two different failures, and a control that ejects *you* from
your own meeting if the selector priority is wrong.

---

## 11. Browser Service Setup

Only needed for WhatsApp automation and SEO audits. Skip if you don't need these.

```bash
bash .agent/skills/browser-service/scripts/ensure_cdp.sh
```

Expected output:
```
✅ CDP service started successfully on port 9222
```

For WhatsApp: on first run, you'll need to scan a QR code manually. The session is saved and persists from then on.

---

## 12. Verify Everything Works

Test each connector you've set up:

```bash
# Google Drive
python3 .agent/skills/work-drive-connector/gdrive_manager.py search --query "test"

# Google Calendar
python3 .agent/skills/google-calendar-connector/gcal_manager.py sweep --profile work --output markdown

# Slack
python3 .agent/skills/slack-connector/scripts/slack_client.py --action list_channels

# Fathom
python3 .agent/skills/fathom-connector/scripts/fathom_client.py --action list

# Daily runner (tests multiple connectors at once — takes 2–3 min)
python3 .agent/scripts/daily_update_runner.py
```

---

## 13. Troubleshooting

### "This app isn't verified" warning from Google

Expected. Click **Advanced** → **Go to [App Name] (unsafe)**. This warning appears for any app on a personal Cloud project that hasn't gone through Google's verification process. Your own app accessing your own data is safe.

### Token expired / authentication error

Re-run the first-auth flow for that connector:
```bash
python3 .agent/skills/work-drive-connector/gdrive_manager.py search --query "test"
```
The script detects the expired token and prompts for re-auth.

### Python: "No module named X"

```bash
pip install -r requirements.txt
```

If using a virtual environment, make sure it's activated:
```bash
source venv/bin/activate
```

### Chrome CDP won't start

```bash
# Check if chromium is installed
which chromium-browser || which google-chrome

# If not found, install via Playwright
playwright install chromium

# Retry
bash .agent/skills/browser-service/scripts/ensure_cdp.sh
```

### WSL: "Connection refused" on localhost

Some WSL2 configurations need explicit port binding. Check `/tmp/chrome_stderr.log` for errors after running ensure_cdp.sh.

### Slack: "not_in_channel" errors

The bot needs to be added to the channel:
1. In Slack, open the channel
2. Type `/invite @YourAppName`
3. Retry the command

### Google OAuth: redirect URL doesn't work

Make sure `http://localhost:8080/` (with trailing slash) is in your OAuth client's Authorized Redirect URIs in Google Cloud Console.

---

## 14. Optional Local Tools

Extra tools ship with the template and are set up separately when you want them:

- **Integration wizard** (`setup/connect.py`): an interactive CLI that walks you through
  connecting MCP servers (Atlassian, and more) into `~/.claude/settings.json` from a catalog
  in `setup/integrations.json`. A complement to the manual steps above; `/setup` uses the same
  ideas conversationally.

- **Meeting recorder** (`meeting-recorder/`): not listed here as optional any more. It is part of
  the default setup, covered in section 10.1 above and in full in **`docs/MEETING_RECORDER.md`**.

- **Visual dashboard** (`dashboard/`): a local web cockpit at `http://localhost:3737` over your notes, calendar, projects, tracker, and system health. Start it with `python3 dashboard/server.py`. See **`docs/DASHBOARD.md`**.
