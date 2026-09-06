#!/usr/bin/env python3
"""
Unified Daily Update Runner (v3 - Morning/Evening Modes)
Runs daily update steps with mode-aware behavior.

Modes:
  --mode morning  : Fast sweep (~60s) for priority-setting.
                    Runs: Calendar, Jira, Slack (all channels, 5 msg/ch), todo.md scan.
                    Writes plan file to _temp/daily_plan_[date].md.
  --mode evening  : Full harvest (~120s) for day-closing recap.
                    Runs: Everything + plan-vs-outcome comparison + LinkedIn prompt.

Outputs (both written every run):
  daily_update_[mode].md          -- human-readable markdown (kept for backward compat
                                     and as user-facing deliverable)
  _temp/harvest_[mode]_[date].json -- structured JSON sidecar; the main-loop synthesis
                                     agent reads THIS instead of the markdown to avoid
                                     re-reading 100-180 KB of raw Slack dumps. Schema:
                                     { mode, date, sections: { slack, jira, calendar,
                                       todo, files, fathom, ... }, meta }

Key reliability features:
- Force UTF-8 stdout on Windows (no emoji encoding crashes)
- Each subprocess has a hard timeout with process kill
- Incremental output: writes to file after EACH step (not all-or-nothing)
- Graceful degradation: if any step fails, others still run
- Progress printed to console so the user sees it's not stuck
- Overall script timeout via signal (safety net)
"""
import subprocess
import sys
import os
import time
import signal
import argparse
import json
from datetime import datetime

# ── Paths ────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))

# ── Import Execution Guard Skill ─────────────────────────────────────
# This skill prevents the script from hanging perpetually.
SKILL_GUARD_PATH = os.path.join(BASE_DIR, '.agent', 'skills', 'execution-guard', 'scripts')
if SKILL_GUARD_PATH not in sys.path:
    sys.path.append(SKILL_GUARD_PATH)
try:
    from execution_guard import ScriptGuardian, safe_run_subprocess, TimeoutException
except ImportError:
    # Fallback if skill is missing (should not happen in normal flow)
    print("[WARN] Execution Guard skill not found. Running without safety net.")
    class ScriptGuardian:
        def __init__(self, s): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    def safe_run_subprocess(cmd, timeout=30, label="", **kw):
        import subprocess
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
            return {"success": p.returncode == 0, "stdout": p.stdout, "stderr": p.stderr}
        except Exception as e:
            return {"success": False, "error": str(e)}
    TimeoutException = Exception

# ── Force UTF-8 on Windows ──────────────────────────────────────────
try:
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass  # If reconfigure fails, continue anyway

# ── Constants ────────────────────────────────────────────────────────
OUTPUT_FILE_MORNING = os.path.join(BASE_DIR, 'daily_update_morning.md')
OUTPUT_FILE_EVENING = os.path.join(BASE_DIR, 'daily_update_evening.md')
OUTPUT_FILE_LEGACY = os.path.join(BASE_DIR, 'daily_update_output.md')
TEMP_DIR = os.path.join(BASE_DIR, '_temp')

# ── Harvest Accumulator ───────────────────────────────────────────────
class HarvestAccumulator:
    """Collects structured harvest data in parallel with the markdown sections list.

    At the end of the run, call .write_json(mode) to emit a compact JSON sidecar
    at _temp/harvest_[mode]_[date].json.  The synthesis agent reads this file
    instead of the full markdown to avoid re-reading 100-180 KB of raw dumps.

    Schema overview:
      {
        "mode": "morning" | "evening",
        "date": "YYYY-MM-DD",
        "generated_at": "HH:MM",
        "markdown_path": "<abs path to .md file>",
        "sections": {
          "jira": [...],        // list of board dicts
          "calendar": [...],    // list of event-block strings per profile
          "slack": {...},       // {channel_name: [message strings]}
          "todo_p0": [...],     // open P0 lines from todo.md
          "files_modified": [...],
          "files_created": [...],
          "backlogs": [...],
          "fathom": "",         // raw fathom sync output (compact)
          "morning_plan": "",   // for evening cross-check
          "portfolio": "",
          "git_sync": ""
        },
        "meta": {
          "step_errors": [],    // any steps that errored/timed out
          "duration_s": 0
        }
      }
    """

    def __init__(self):
        self.mode = None
        self.date_str = datetime.now().strftime('%Y-%m-%d')
        self.generated_at = datetime.now().strftime('%H:%M')
        self.markdown_path = None
        self.sections = {
            "jira": [],
            "calendar": [],
            "slack": {},
            "todo_p0": [],
            "files_modified": [],
            "files_created": [],
            "backlogs": [],
            "fathom": "",
            "fathom_registry": "",
            "figma_index": "",
            "morning_plan": "",
            "portfolio": "",
            "git_sync": "",
        }
        self.meta = {
            "step_errors": [],
            "duration_s": 0,
        }

    # ── section setters ──────────────────────────────────────────────

    def set_jira(self, raw_text):
        """Store Jira output as a compact list of lines (strip blank lines)."""
        if raw_text:
            self.sections["jira"] = [l for l in raw_text.splitlines() if l.strip()]

    def add_calendar(self, profile, raw_text):
        """Append a calendar block per profile."""
        if raw_text:
            self.sections["calendar"].append({
                "profile": profile,
                "lines": [l for l in raw_text.splitlines() if l.strip()],
            })

    def add_slack_channel(self, channel_name, raw_text):
        """Store up to 200 lines per channel (trim noise from long dumps)."""
        if raw_text:
            lines = [l for l in raw_text.splitlines() if l.strip()]
            self.sections["slack"][channel_name] = lines[:200]

    def set_todo_p0(self, lines):
        self.sections["todo_p0"] = lines

    def set_files_modified(self, raw_text):
        if raw_text:
            self.sections["files_modified"] = [l for l in raw_text.splitlines() if l.strip()][:60]

    def set_files_created(self, raw_text):
        if raw_text:
            self.sections["files_created"] = [l for l in raw_text.splitlines() if l.strip()][:40]

    def set_backlogs(self, raw_text):
        if raw_text:
            self.sections["backlogs"] = [l for l in raw_text.splitlines() if l.strip()]

    def set_fathom(self, raw_text):
        if raw_text:
            self.sections["fathom"] = raw_text[:3000]  # cap at 3k chars

    def set_fathom_registry(self, raw_text):
        if raw_text:
            self.sections["fathom_registry"] = raw_text[:2000]

    def set_figma_index(self, raw_text):
        if raw_text:
            self.sections["figma_index"] = raw_text[:2000]

    def set_morning_plan(self, raw_text):
        if raw_text:
            self.sections["morning_plan"] = raw_text[:2000]

    def set_portfolio(self, raw_text):
        if raw_text:
            self.sections["portfolio"] = raw_text[:2000]

    def set_git_sync(self, status_text):
        self.sections["git_sync"] = status_text

    def add_error(self, step_label):
        self.meta["step_errors"].append(step_label)

    def set_duration(self, seconds):
        self.meta["duration_s"] = round(seconds, 1)

    # ── output ───────────────────────────────────────────────────────

    def get_json_path(self):
        os.makedirs(TEMP_DIR, exist_ok=True)
        return os.path.join(TEMP_DIR, f'harvest_{self.mode}_{self.date_str}.json')

    def write_json(self):
        """Write the structured sidecar JSON. Returns the path written."""
        payload = {
            "mode": self.mode,
            "date": self.date_str,
            "generated_at": self.generated_at,
            "markdown_path": self.markdown_path,
            "sections": self.sections,
            "meta": self.meta,
        }
        path = self.get_json_path()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"  [JSON] Harvest sidecar written: {path}", flush=True)
            return path
        except Exception as e:
            print(f"  [ERROR] Could not write harvest JSON: {e}", flush=True)
            return None

# Timeouts (seconds)
FILE_SCAN_TIMEOUT = 15
CALENDAR_TIMEOUT = 20
SLACK_LIST_TIMEOUT = 120  # raised from 15: listing now paginates im/mpim and sweeps users.list for DM names
SLACK_HISTORY_TIMEOUT = 20   # per channel; threads can be slow
SLACK_HISTORY_TIMEOUT_MORNING = 12  # faster for morning mode
BACKLOG_TIMEOUT = 10

# Overall safety net: kill entire script after this many seconds
MAX_TOTAL_RUNTIME_MORNING = 180  # 3 minutes for morning
MAX_TOTAL_RUNTIME_EVENING = 600  # 10 minutes for evening

def get_skill_token(skill_name):
    """Load SLACK_USER_TOKEN (personal user POV) or fall back to SLACK_BOT_TOKEN from a skill directory's token.env file."""
    skill_path = os.path.join(BASE_DIR, '.agent', 'skills', skill_name, 'token.env')
    tokens = {}
    if os.path.exists(skill_path):
        with open(skill_path, 'r') as f:
            for line in f:
                if '=' in line:
                    key, val = line.split('=', 1)
                    tokens[key.strip()] = val.strip()
    
    # Prioritize SLACK_USER_TOKEN for personal POV as explicitly requested by user
    user_token = tokens.get('SLACK_USER_TOKEN') or os.environ.get('SLACK_USER_TOKEN')
    if user_token:
        print(f"      [INFO] Found SLACK_USER_TOKEN for '{skill_name}', fetching data from the owner's POV", flush=True)
        return user_token
        
    bot_token = tokens.get('SLACK_BOT_TOKEN') or os.environ.get('SLACK_BOT_TOKEN')
    if bot_token:
        print(f"      [INFO] Falling back to SLACK_BOT_TOKEN for '{skill_name}'", flush=True)
        return bot_token
    return None

def run_step(label, command, timeout=30):
    """
    Run a subprocess using the Execution Guard's safe runner.
    """
    print(f"  ▸ {label}...", end=" ", flush=True)
    res = safe_run_subprocess(command, timeout=timeout, label=label, cwd=BASE_DIR)
    
    if not res.get("success"):
        if res.get("error") == "TIMEOUT":
            print(f"❌ TIMEOUT after {res['duration']:.0f}s", flush=True)
            return f"[ERROR] {label} timed out after {timeout}s"
        else:
            print(f"⚠ FAILED ({res['duration']:.1f}s)", flush=True)
            return f"[WARN] {label} failed: {res.get('message', 'Unknown error')}\n{res.get('stdout', '')}{res.get('stderr', '')}"

    print(f"✓ ({res['duration']:.1f}s)", flush=True)
    return (res.get("stdout") or "").strip()

def git_sync(sections):
    """
    Step 10: Sync local changes to GitHub.
    Uses GIT_EDITOR=true to avoid blocking during rebase.
    """
    print("\n[10/9] Syncing to GitHub...", flush=True)
    try:
        # 1. Add all changes
        subprocess.run(["git", "add", "."], cwd=BASE_DIR, capture_output=True)
        
        # 2. Check for changes to commit
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=BASE_DIR)
        if status.stdout.strip():
            print("      Committing local changes...", end=" ", flush=True)
            msg = f"chore: automated daily sync {datetime.now().strftime('%Y-%m-%d')}"
            subprocess.run(["git", "commit", "-m", msg], cwd=BASE_DIR, capture_output=True)
            print("✓")

        # 3. Pull with rebase to integrate remote changes.
        #    Always operate on the CHECKED-OUT branch. This used to hardcode
        #    'main', which rebased whatever feature branch was checked out onto
        #    origin/main, rewriting every one of its SHAs and leaving the branch
        #    permanently diverged from its own remote. Fixed 29 Jul 2026.
        branch_res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                    cwd=BASE_DIR, capture_output=True, text=True)
        branch = branch_res.stdout.strip() or "main"
        if branch == "HEAD":
            print("      ⚠ detached HEAD, skipping sync")
            sections.append("## GitHub Sync Status\n> [!WARNING]\n> Detached HEAD, sync skipped.\n")
            return
        print(f"      Pulling remote changes ({branch})...", end=" ", flush=True)
        env = os.environ.copy()
        env["GIT_EDITOR"] = "true"
        pull_res = subprocess.run(["git", "pull", "--rebase", "origin", branch],
                                  cwd=BASE_DIR, env=env, capture_output=True, text=True)
        
        if pull_res.returncode != 0:
            print("❌ CONFLICT")
            subprocess.run(["git", "rebase", "--abort"], cwd=BASE_DIR, capture_output=True)
            sections.append("## GitHub Sync Status\n> [!WARNING]\n> GitHub Sync failed due to a merge conflict. Please resolve it manually.\n")
            return
        print("✓")

        # 4. Push
        print(f"      Pushing to origin ({branch})...", end=" ", flush=True)
        push_res = subprocess.run(["git", "push", "origin", branch],
                                  cwd=BASE_DIR, capture_output=True, text=True)
        if push_res.returncode == 0:
            print("✓")
            sections.append(f"## GitHub Sync Status\n✓ Successfully pushed latest local changes to origin/{branch}.\n")
        else:
            print("❌ FAILED")
            sections.append(f"## GitHub Sync Status\n> [!ERROR]\n> Push failed: {push_res.stderr}\n")

    except Exception as e:
        print(f"❌ ERROR: {e}")
        sections.append(f"## GitHub Sync Status\n> [!ERROR]\n> Sync encountered an error: {e}\n")

def write_output(sections, output_file=None):
    """Write all collected sections to the output file incrementally."""
    if output_file is None:
        output_file = OUTPUT_FILE_LEGACY
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(sections))
    except Exception as e:
        print(f"  [ERROR] Could not write output file: {e}", flush=True)

def parse_args():
    parser = argparse.ArgumentParser(description='Daily Update Runner v3 - Morning/Evening Modes')
    parser.add_argument('--mode', choices=['morning', 'evening'], default=None,
                        help='Run mode: morning (fast, priority-setting) or evening (full, day-closing). '
                             'If not specified, auto-detects based on current time (before 14:00 = morning).')
    parser.add_argument('--dry-run', action='store_true',
                        help='Skip all live API/subprocess calls. Exercise the output-writing path '
                             'with stub data to validate the JSON sidecar format without hitting '
                             'Slack, Jira, Calendar, or Fathom.')
    return parser.parse_args()

def get_plan_file_path(date_str=None):
    """Get the path to the daily plan file for cross-referencing morning vs evening."""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')
    os.makedirs(TEMP_DIR, exist_ok=True)
    return os.path.join(TEMP_DIR, f'daily_plan_{date_str}.md')

def write_morning_plan(priorities, date_str=None):
    """Write the morning's proposed priorities to a plan file for evening cross-check."""
    plan_path = get_plan_file_path(date_str)
    try:
        with open(plan_path, 'w', encoding='utf-8') as f:
            f.write(f"# Morning Plan - {date_str or datetime.now().strftime('%Y-%m-%d')}\n\n")
            f.write("## Proposed Priorities\n")
            for i, p in enumerate(priorities, 1):
                f.write(f"{i}. {p}\n")
            f.write(f"\n---\n*Generated at {datetime.now().strftime('%H:%M')}*\n")
        print(f"  [INFO] Morning plan written to {plan_path}", flush=True)
    except Exception as e:
        print(f"  [WARN] Could not write morning plan: {e}", flush=True)

def read_morning_plan(date_str=None):
    """Read the morning's plan file for evening comparison."""
    plan_path = get_plan_file_path(date_str)
    if os.path.exists(plan_path):
        try:
            with open(plan_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return None
    return None

def main():
    args = parse_args()
    mode = args.mode
    dry_run = args.dry_run

    # Auto-detect mode if not specified
    if mode is None:
        current_hour = datetime.now().hour
        mode = 'morning' if current_hour < 14 else 'evening'
        print(f"[AUTO] Detected mode: {mode} (current hour: {current_hour})", flush=True)

    if dry_run:
        print(f"[DRY-RUN] No live API calls will be made. Exercising output path with stub data.", flush=True)

    max_runtime = MAX_TOTAL_RUNTIME_MORNING if mode == 'morning' else MAX_TOTAL_RUNTIME_EVENING

    try:
        with ScriptGuardian(max_runtime):
            _main_logic(mode, dry_run=dry_run)
    except TimeoutException:
        print(f"\n{'!'*60}")
        print(f"  CRITICAL: Global timeout of {max_runtime}s reached.")
        print(f"  Ending runner to prevent hanging.")
        print(f"{'!'*60}\n")
        sys.exit(1)

def _main_logic(mode, dry_run=False):
    script_start = time.time()
    now = datetime.now()
    is_morning = (mode == 'morning')
    is_weekend = now.weekday() >= 5  # Saturday=5, Sunday=6
    mode_label = 'Morning Prep' if is_morning else 'Evening Closing'

    # ── Harvest accumulator: collects structured data for JSON sidecar ──
    harvest = HarvestAccumulator()
    harvest.mode = mode
    harvest.date_str = now.strftime('%Y-%m-%d')
    harvest.generated_at = now.strftime('%H:%M')

    # Set output file based on mode
    if is_morning:
        output_file = OUTPUT_FILE_MORNING
    else:
        output_file = OUTPUT_FILE_EVENING
    harvest.markdown_path = output_file

    print(f"{'='*60}", flush=True)
    print(f"  Daily Update Runner v3.0 ({mode_label}) - {now.strftime('%Y-%m-%d %H:%M')}", flush=True)
    if is_weekend:
        print(f"  [WEEKEND MODE] Minimal scan - Slack only", flush=True)
    if dry_run:
        print(f"  [DRY-RUN] Stub data only - no live API calls", flush=True)
    print(f"{'='*60}", flush=True)

    # ── Pre-flight check: GCal credentials ──────────────────────────
    # This prevents hanging mid-scan if browser auth is needed but impossible
    if not dry_run:
        print("\n[0/9] Pre-flight: Checking credentials...", end=" ", flush=True)
        os.environ['AGENT_MODE'] = '1' # Hint to scripts that this is a non-interactive runner

        gcal_script = os.path.join(BASE_DIR, '.agent', 'skills', 'google-calendar-connector', 'gcal_manager.py')
        # Try a quick list with 0 days to check auth with a hard timeout
        try:
            res = subprocess.run([sys.executable, gcal_script, 'list', '--days-back', '0', '--days-forward', '0'],
                                 capture_output=True, text=True, encoding='utf-8', timeout=10)
            if "[ERROR] GOOGLE CALENDAR AUTHENTICATION REQUIRED" in res.stdout or "[ERROR] GOOGLE CALENDAR AUTHENTICATION REQUIRED" in res.stderr:
                print("⚠ FAILED")
                print("  Error: Google Calendar auth required. Run manually to refresh token.")
                # We continue, but this warns the user
            else:
                print("✓")
        except subprocess.TimeoutExpired:
            print("❌ TIMEOUT")
            print("  Error: Google Calendar check timed out. Token may be invalid or expired.")
        except Exception as e:
            print(f"❌ ERROR: {e}")
    else:
        print("\n[0/9] Pre-flight: SKIPPED (dry-run)", flush=True)
        os.environ['AGENT_MODE'] = '1'

    # ── Step 0b: Dashboard Health Check ──────────────────────────────
    import socket
    is_alive = False
    if not dry_run:
        print("[0b/9] Dashboard check: port 3737...", end=" ", flush=True)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(('127.0.0.1', 3737))
            is_alive = True
            s.close()
            print("✓ (Alive)")
        except Exception:
            print("⚠ DOWN. Restarting...", end=" ", flush=True)
            try:
                # Start dashboard server in background
                subprocess.Popen(
                    "nohup python3 dashboard/server.py > dashboard_server.log 2>&1 &",
                    shell=True,
                    cwd=BASE_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setpgrp if os.name != 'nt' else None
                )
                time.sleep(2) # Give it a second to bind
                print("✓ (Restarted)")
            except Exception as e:
                print(f"❌ FAILED to restart: {e}")
    else:
        print("[0b/9] Dashboard check: SKIPPED (dry-run)", flush=True)
        is_alive = True  # assume up in dry-run

    sections = [f"# {mode_label} - {now.strftime('%Y-%m-%d %H:%M')}\n"]
    if not is_alive:
        sections.append("> [!IMPORTANT]\n> Dashboard was found offline and was automatically restarted.\n")

    gcal_script = os.path.join(BASE_DIR, '.agent', 'skills', 'google-calendar-connector', 'gcal_manager.py')
    work_slack_script = os.path.join(BASE_DIR, '.agent', 'skills', 'slack-connector', 'scripts', 'slack_client.py')
    secondary_slack_script = os.path.join(BASE_DIR, '.agent', 'skills', 'secondary-slack-connector', 'scripts', 'slack_client.py')
    file_utils  = os.path.join(SCRIPT_DIR, 'file_utils.py')

    def _step(label, cmd, timeout):
        """Run a step unless in dry-run mode (returns stub then)."""
        if dry_run:
            stub = f"[DRY-RUN stub for: {label}]"
            print(f"  ▸ {label}... [DRY-RUN]", flush=True)
            return stub
        result = run_step(label, cmd, timeout)
        if result.startswith("[ERROR]") or result.startswith("[WARN]"):
            harvest.add_error(label)
        return result

    # ── Step 1: Modified files (Evening only) ────────────────────────
    if not is_morning:
        print("\n[1/9] File scan: modified...", flush=True)
        out = _step("Modified Files", [
            sys.executable, file_utils,
            '--action', 'recent_modified', '--dir', BASE_DIR,
            '--hours', '24', '--limit', '50'
        ], timeout=FILE_SCAN_TIMEOUT)
        harvest.set_files_modified(out)
        sections.append(f"## Modified Files (last 24h)\n```\n{out}\n```\n")
        write_output(sections, output_file)

        # ── Step 2: Created files (Evening only) ─────────────────────
        print("[2/9] File scan: created...", flush=True)
        out = _step("Created Files", [
            sys.executable, file_utils,
            '--action', 'recent_created', '--dir', BASE_DIR,
            '--hours', '24', '--limit', '30'
        ], timeout=FILE_SCAN_TIMEOUT)
        harvest.set_files_created(out)
        sections.append(f"## Created Files (last 24h)\n```\n{out}\n```\n")
        write_output(sections, output_file)
    else:
        print("\n[1-2] File scan: SKIPPED (morning mode)", flush=True)

    # ── Step 2.5: Work Jira Sprints (Skip on weekends) ──────────────
    if not is_weekend:
        print("[2.5] Work Jira Sprints...", flush=True)
        jira_client_script = os.path.join(BASE_DIR, '.agent', 'skills', 'jira-connector', 'scripts', 'jira_client.py')
        out = _step("Work Jira Sprints", [
            sys.executable, jira_client_script, 'daily-digest'
        ], timeout=60)
        harvest.set_jira(out)
        sections.append(f"## Work Jira Sprint Progress\n{out}\n")
        write_output(sections, output_file)
    else:
        print("[2.5] Work Jira Sprints: SKIPPED (weekend)", flush=True)

    # ── Step 3: Calendar (default/Secondary) removed — Secondary archived, token expired ──
    # ── Step 4: Calendar (work) ─────────────────────────────────────
    print("[4] Calendar sweep (work)...", flush=True)
    out = _step("Work Calendar", [
        sys.executable, gcal_script,
        'sweep', '--profile', 'work', '--output', 'markdown'
    ], timeout=CALENDAR_TIMEOUT)
    harvest.add_calendar("work", out)
    sections.append(f"## Calendar: Work\n{out}\n")
    write_output(sections, output_file)

    # ── Step 5: Work Slack (Married) ────────────────────────────────
    work_token = get_skill_token('slack-connector')
    print("[5a] Slack: listing Work channels...", flush=True)
    if dry_run:
        out = "- work-product (ID: DRY001)\n- b2c-superapp (ID: DRY002)\n- marketplace-general (ID: DRY003)"
        print(f"  ▸ Work Slack Channels... [DRY-RUN]", flush=True)
    else:
        out = run_step("Work Slack Channels", [
            sys.executable, work_slack_script,
            '--action', 'list_joined_channels', '--token', work_token or "",
            '--include-dms'
        ], timeout=SLACK_LIST_TIMEOUT)
    sections.append(f"## Slack: Work (Married)\n```\n{out}\n```\n")
    write_output(sections, output_file)

    # Keep the name -> handle index current, so a draft written today can mention a
    # person who joined this week. Weekly, read-only, and never fatal to the sweep.
    if not dry_run:
        run_step("Slack person index", [
            sys.executable, os.path.join(BASE_DIR, '.agent', 'scripts', 'slack_mentions.py'),
            'refresh', '--if-stale', '7'
        ], timeout=120)

    # Fetch Work history
    work_channels = []
    for line in out.split('\n'):
        if '(ID: ' in line:
            try:
                name = line.split('- ')[1].split(' (ID:')[0].strip()
                cid = line.split('(ID: ')[1].split(')')[0].strip()
                is_dm = '[DM' in line
                dm_updated = 0
                if is_dm and 'updated=' in line:
                    dm_updated = int(line.split('updated=')[1].split(']')[0].strip() or 0)
                work_channels.append((name, cid, is_dm, dm_updated))
            except: continue

    if work_channels:
        # Filter for relevant channels. DMs bypass the keyword list entirely:
        # the owner's most actionable traffic arrives there and a keyword list
        # cannot match a person's name. No recency prefilter is possible here,
        # because users.conversations returns `updated` as the conversation's
        # creation time, not its last message.
        # No keyword filter. It used to gate channels on a hardcoded word list,
        # which silently dropped 29 live channels on 29 Jul 2026, among them
        # ksa-2026-ExampleVendor, new-biz-ExampleVendor, exampleco-tech-ops and
        # 2026-offers-redemption-journey-saib, all carrying open the owner items
        # that day. the owner is only in ~92 conversations, so sweeping all of them
        # costs ~45s and removes a whole class of silent blind spot.
        filtered_work = work_channels

        # Morning mode: all channels but fewer messages (5 per channel)
        # Evening mode: filtered channels with more messages (10 per channel)
        msg_limit = '5' if is_morning else '10'
        history_timeout = SLACK_HISTORY_TIMEOUT_MORNING if is_morning else SLACK_HISTORY_TIMEOUT

        print(f"      Fetching history for {len(filtered_work)} channels ({msg_limit} msg/ch, mode={mode})...", flush=True)
        n_dms = sum(1 for c in filtered_work if c[2])
        print(f"      [scope] {len(filtered_work) - n_dms} channels + {n_dms} DMs", flush=True)

        for name, cid, is_dm, _updated in filtered_work:
            # Channels expand thread replies; DMs do not, since DM threads are
            # rare and the mention ledger already tracks them statefully. That
            # keeps the added DM sweep to roughly 1.5s per conversation.
            cmd = [
                sys.executable, work_slack_script,
                '--action', 'history', '--channel', cid,
                '--token', work_token or "", '--limit', msg_limit, '--full'
            ]
            if not is_dm:
                cmd.append('--replies')
            out = _step(f"Work #{name}", cmd, timeout=history_timeout)
            harvest.add_slack_channel(name, out)
            sections.append(f"### Work: #{name}\n```\n{out}\n```\n")
            write_output(sections, output_file)

    # ── Step 5b: Secondary Slack (Disabled temporarily due to network hangs) ──
    # secondary_token = get_skill_token('secondary-slack-connector')
    # print("[5b/7] Slack: listing Secondary channels... SKIPPED", flush=True)
    # out = run_step("Secondary Slack Channels", [
    #     sys.executable, secondary_slack_script,
    #     '--action', 'list_joined_channels', '--token', secondary_token or ""
    # ], timeout=SLACK_LIST_TIMEOUT)
    # sections.append(f"## Slack: Secondary\n```\n{out}\n```\n")
    # write_output(sections)

    # Fetch Secondary history (Disabled)
    """
    secondary_channels = []
    for line in out.split('\n'):
        if '(ID: ' in line:
            try:
                name = line.split('- ')[1].split(' (ID:')[0].strip()
                cid = line.split('(ID: ')[1].split(')')[0].strip()
                secondary_channels.append((name, cid))
            except: continue

    if secondary_channels:
        print(f"      Fetching history for {len(secondary_channels)} Secondary channels...", flush=True)
        for name, cid in secondary_channels:
            out = run_step(f"Secondary #{name}", [
                sys.executable, secondary_slack_script,
                '--action', 'history', '--channel', cid,
                '--token', secondary_token or "", '--limit', '10', '--replies'
            ], timeout=SLACK_HISTORY_TIMEOUT)
            sections.append(f"### Secondary: #{name}\n```\n{out}\n```\n")
            write_output(sections)
    """

    # ── Step 6: Todo.md scan (both modes - load current priorities) ──
    # Runs in evening too. The evening rubric check compares the briefing's
    # stated P0 count against sections.todo_p0, so gating this on morning made
    # verify_briefing_numbers.py report MISMATCH unconditionally every night.
    if True:
        print("[6] Loading current todo.md priorities...", flush=True)
        todo_path = os.path.join(BASE_DIR, 'journal', 'todo.md')
        try:
            if dry_run:
                open_p0 = ["- [ ] P0: Stub todo item A", "- [ ] P0: Stub todo item B"]
                print(f"  ▸ todo.md scan... [DRY-RUN]", flush=True)
            else:
                with open(todo_path, 'r', encoding='utf-8') as f:
                    todo_content = f.read()
                # Match the structured marker, not a bare "P0" substring, so
                # prose that merely mentions P0 cannot inflate the count.
                # No cap: the length of this list IS the number the rubric
                # verifies against, so truncating it makes the check lie.
                open_p0 = []
                for line in todo_content.split('\n'):
                    if line.lstrip().startswith('- [ ]') and '<!-- P0 -->' in line:
                        open_p0.append(line.strip())
            harvest.set_todo_p0(open_p0)
            sections.append(f"## Current Open P0 Items (from todo.md)\n")
            for item in open_p0:
                sections.append(f"{item}\n")
            sections.append("\n")
            write_output(sections, output_file)
        except Exception as e:
            print(f"  [WARN] Could not read todo.md: {e}", flush=True)

    # ── Step 7: Backlogs (Evening only) ──────────────────────────────
    if not is_morning:
        print("[7] Finding backlogs...", flush=True)
        out = _step("Find Backlogs", [
            sys.executable, file_utils,
            '--action', 'find',
            '--dir', os.path.join(BASE_DIR, 'Clients'),
            '--patterns', '*backlog*'
        ], timeout=BACKLOG_TIMEOUT)
        harvest.set_backlogs(out)
        sections.append(f"## Backlogs Found\n```\n{out}\n```\n")
        write_output(sections, output_file)

    # ── Step 8: Fathom Meetings (Evening only, skip weekends) ────────
    if not is_morning and not is_weekend:
        print("[8] Syncing Fathom meetings...", flush=True)
        fathom_script = os.path.join(BASE_DIR, 'scripts', 'fathom_to_notes.py')
        out = _step("Fathom Sync", [sys.executable, fathom_script], timeout=180)
        harvest.set_fathom(out)
        sections.append(f"## Fathom Sync Status\n```\n{out}\n```\n")
        write_output(sections, output_file)

        # Update the cumulative Fathom meeting registry (incremental: stops at known recordings)
        registry_script = os.path.join(BASE_DIR, 'scripts', 'fathom_registry_sync.py')
        out = _step("Fathom Registry", [sys.executable, registry_script], timeout=180)
        harvest.set_fathom_registry(out)
        sections.append(f"## Fathom Registry Update\n```\n{out}\n```\n")
        write_output(sections, output_file)

        # Refresh the Marketplace Figma Design Index mirror (version-gated: only
        # rewrites the local mirror when Teammate bumps the Confluence page; else no-op).
        figma_index_script = os.path.join(BASE_DIR, 'scripts', 'marketplace_figma_index_sync.py')
        out = _step("Marketplace Figma Index", [sys.executable, figma_index_script], timeout=120)
        harvest.set_figma_index(out)
        sections.append(f"## Marketplace Figma Index Sync\n```\n{out}\n```\n")
        write_output(sections, output_file)
    elif is_morning:
        print("[7-8] Backlogs/Fathom: SKIPPED (morning mode)", flush=True)

    # ── Step 9: Work Document Indexing (Evening only) ───────────────
    if not is_morning:
        print("[9] Work Document Indexing (Centralization)... [SKIPPED]", flush=True)
        out = "Skipped (Drive Auth requires manual interaction in WSL)"
        sections.append(f"## Work Document Index Status\n* {out}\n")
        write_output(sections, output_file)
    else:
        print("[9] Doc Indexer: SKIPPED (morning mode)", flush=True)

    # ── Step 10: Plan vs Outcome (Evening only) ──────────────────────
    if not is_morning:
        print("[10] Plan vs Outcome comparison...", flush=True)
        morning_plan = read_morning_plan(now.strftime('%Y-%m-%d'))
        if morning_plan:
            harvest.set_morning_plan(morning_plan)
            sections.append(f"## Morning Plan vs Evening Outcome\n")
            sections.append(f"### Morning Plan (set at 09:30)\n```\n{morning_plan}\n```\n")
            sections.append(f"### Evening Status\n")
            sections.append(f"> [!NOTE]\n> Cross-reference the priorities above against today's Slack signals and Jira updates to determine completion status.\n")
            write_output(sections, output_file)
        else:
            sections.append(f"## Morning Plan vs Evening Outcome\n> [!WARNING]\n> No morning plan found for today. Morning update may not have been run.\n")
            write_output(sections, output_file)

        # LinkedIn Content Prompt
        sections.append(f"## LinkedIn Content Check\n")
        sections.append(f"> **Reminder**: target 1 post per day on LinkedIn.\n")
        sections.append(f"> Posted today? If not, look for an angle in today's activity.\n")
        sections.append(f"> Content Pillars: AI (priority), Career, Startup, Family.\n")
        write_output(sections, output_file)

    # ── Step 10.5: Portfolio Sync (Evening only) ─────────────────────
    # Reconcile portfolio.json against the waiting-on ledger, refresh the `now` lines
    # that have new evidence via GLM, then render journal/portfolio.md. Rendering alone
    # never touched portfolio.json, so the Portfolio card's freshness rotted to "dead"
    # while this step reported success every night.
    if not is_morning:
        print("[10.5] Syncing portfolio...", flush=True)
        portfolio_script = os.path.join(BASE_DIR, '.agent', 'scripts', 'portfolio_sync.py')
        out = _step("Portfolio Sync", [sys.executable, portfolio_script, '--narrative'],
                    timeout=600)
        harvest.set_portfolio(out)
        sections.append(f"## Portfolio Sync\n```\n{out}\n```\n")
        write_output(sections, output_file)

    # ── Step 11: GitHub Sync (Evening only) ──────────────────────────
    if not is_morning:
        if dry_run:
            print("[11] GitHub Sync: SKIPPED (dry-run)", flush=True)
            harvest.set_git_sync("skipped (dry-run)")
        else:
            git_sync(sections)
            harvest.set_git_sync("completed")
        write_output(sections, output_file)
    else:
        print("[10-11] Plan comparison/GitHub: SKIPPED (morning mode)", flush=True)

    # ── Step 10.6: Followup Tracker Render (both modes) ───────────────
    # journal/master_followup_tracker.md is a generated view over the PM
    # ledgers (commitments/waiting_on/decisions), not hand-maintained. This
    # keeps it mechanically fresh even if the Claude-driven SOP step is
    # skipped. Cheap (three `report` subprocess calls), safe to run in both
    # morning and evening mode.
    print("[10.6] Rendering followup tracker...", flush=True)
    tracker_script = os.path.join(BASE_DIR, '.agent', 'skills', 'project-tracking-update',
                                   'scripts', 'render_followup_tracker.py')
    out = _step("Followup Tracker Render", [sys.executable, tracker_script], timeout=60)
    sections.append(f"## Followup Tracker Render\n```\n{out}\n```\n")
    write_output(sections, output_file)

    # ── Ledger self-checks ───────────────────────────────────────────
    # Both of these answer a question the harvest above cannot: is what the
    # ledger says still true. On 22 Aug the briefing led on three decisions that
    # had already been made, because every source it read agreed with the stale
    # record. Deterministic, offline, and both exit 1 when they find something.
    print("[10.7] Checking decision consistency...", flush=True)
    consistency_script = os.path.join(BASE_DIR, '.agent', 'scripts', 'decision_consistency.py')
    out = _step("Decision Consistency", [sys.executable, consistency_script, 'check'],
                timeout=120)
    sections.append(f"## Decision Consistency\n```\n{out}\n```\n")

    print("[10.8] Checking unresolved meeting speakers...", flush=True)
    speaker_script = os.path.join(BASE_DIR, 'meeting-recorder', 'speaker_map.py')
    if os.path.exists(speaker_script):
        out = _step("Unresolved Speakers", [sys.executable, speaker_script, 'pending'],
                    timeout=120)
        sections.append(f"## Unresolved Speakers\n```\n{out}\n```\n")
    write_output(sections, output_file)

    # ── Write morning plan file (Morning only) ───────────────────────
    if is_morning:
        # Extract a summary of priorities from the collected data for plan file
        plan_items = []
        plan_items.append("[Auto-generated from morning sweep data]")
        plan_items.append("Review Calendar, Jira, and Slack signals to finalize top 5 priorities.")
        write_morning_plan(plan_items, now.strftime('%Y-%m-%d'))

    # ── Summary ──────────────────────────────────────────────────────
    total_time = time.time() - script_start
    harvest.set_duration(total_time)
    sections.append(f"\n---\n*Runner ({mode_label}) completed in {total_time:.1f}s*\n")
    write_output(sections, output_file)

    # ── Write JSON sidecar ───────────────────────────────────────────
    json_path = harvest.write_json()

    print(f"\n{'='*60}", flush=True)
    print(f"  Done in {total_time:.1f}s. Mode: {mode_label}.", flush=True)
    print(f"  Markdown : {output_file}", flush=True)
    if json_path:
        print(f"  JSON     : {json_path}", flush=True)
    print(f"{'='*60}", flush=True)

    # Heartbeat so the dashboard "Routines" tab shows this routine's last run.
    if not dry_run:
        try:
            import subprocess as _sp
            _sp.run(['python3', os.path.join(BASE_DIR, '.agent', 'scripts', 'heartbeat.py'),
                     '--job', 'morning-update' if is_morning else 'evening-update',
                     '--status', 'ok', '--summary', f'{mode_label} done in {total_time:.0f}s'],
                    cwd=BASE_DIR, capture_output=True, timeout=10)
        except Exception:
            pass

if __name__ == '__main__':
    main()
