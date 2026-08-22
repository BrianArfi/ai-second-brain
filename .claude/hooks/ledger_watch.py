#!/usr/bin/env python3
"""PostToolUse hook: notice when something tracked was acted on, and remember it.

Two jobs.

1. Catch hand-edits. `journal/state/*.json` is written by the ledger CLIs, which
   take the ledger lock and propagate the change. Opening one in an editor does
   neither, so the edit races the next cron sweep and reaches no other session.
   `journal/master_followup_tracker.md` is a generated view; typing into it is
   overwritten on the next render. Both get a warning here.

2. Record discharge actions. Sending the Slack message, publishing the doc,
   transitioning the ticket - each of those is usually the moment a commitment,
   a waiting-on, or a decision stops being open. The record is what the next
   session reads, so an action without a matching record update is invisible
   work: the ledger keeps reporting it as outstanding and someone chases it
   again. This hook writes the action to a per-session file; `ledger_guard.py`
   checks at the end of the turn whether a record followed.

Contract: always exit 0. Never blocks a tool call.
"""
import json
import os
import pathlib
import re
import shlex
import sys
import time

SESSION_DIR = os.path.join(".claude", ".ledger_session")

# ── mentioning an action vs performing one ──────────────────────────────────
# Until 12 Aug 2026 a discharge was recorded whenever the command STRING matched,
# so writing about a send counted as sending. Three ways that fired on nothing:
# a probe piping the command as JSON into a guard, `: slack_client.py --approved`
# (the shell no-op), and a git commit whose own message quoted the command. Each
# left a phantom "Slack message sent" in the session file for ledger_guard to
# block on. Two checks now stand between a match and a record: the script has to
# occupy a command position, and the call has to have actually succeeded.

# Tokens after which a new command begins.
CMD_SEPS = {";", "&&", "||", "|", "|&", "&", "(", ")", "{", "}",
            "then", "do", "else", "elif", "fi", "!"}
# Wrappers that precede the real command without changing what it is.
RUNNERS = {"python3", "python", "python2", "sudo", "nohup", "time", "env",
           "flock", "bash", "sh", "zsh", "command", "exec", "poetry", "uv"}
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# A ledger record actually changed.
LEDGER_MUTATIONS = re.compile(
    r"(commitment_ledger|waiting_watchdog|decision_log|chase_queue)\.py\s+"
    r"(add|close|drop|reopen|decide|supersede|update|link|unlink|touch|sweep|"
    r"extract|dedupe|mark-false-positive|build)"
)

# Actions that normally discharge a tracked item:
#   label, command pattern, the script that must actually run,
#   and the success marker its output prints (None = no reliable marker).
DISCHARGES = [
    ("Slack message sent", re.compile(r"slack_client\.py.*--approved"),
     "slack_client.py", re.compile(r"slack\.com/archives/")),
    ("Google Doc created", re.compile(r"gdocs_create\.py"),
     "gdocs_create.py", re.compile(r"docs\.google\.com|drive\.google\.com")),
    ("Google Doc updated", re.compile(r"gdocs_writer\.py|gdoc_surgical\.py"),
     None, re.compile(r"docs\.google\.com|drive\.google\.com")),
    # Both of these have a dry/no-op mode, so the command pattern has to require
    # the flag that actually writes. Without it, `--help`, a dry run, or a grep
    # that merely names the script filed a phantom discharge and blocked the
    # turn (three times on 14 Aug 2026 while drafting the Project Wolf reply).
    ("Google Doc comment posted",
     re.compile(r"gdoc_comment\.py\s+comment\b|gdoc_reply_comments\.py.*--apply\b"),
     ("gdoc_comment.py", "gdoc_reply_comments.py"), None),
    ("Drive file written", re.compile(r"gdrive_manager\.py\s+(upload|create|update)"),
     "gdrive_manager.py", re.compile(r"docs\.google\.com|drive\.google\.com")),
    ("Artifact published", re.compile(r"publish_cf\.py\s+deploy"),
     "publish_cf.py", re.compile(r"pages\.dev")),
    ("Chase queue sent", re.compile(r"chase_queue\.py\s+send.*--approved"),
     "chase_queue.py", re.compile(r"slack\.com/archives/")),
    ("Email sent", re.compile(r"gmail_connector.*\bsend\b|gmail\.py\s+send"),
     None, None),
]

def invoked_scripts(command):
    """The .py scripts this command RUNS, as opposed to the ones it merely names.

    A script counts as run when it sits at a command position: first token, or
    preceded only by wrappers (`python3`, `nohup`, `env FOO=bar`, a short flag)
    back to the start or to a separator. A quoted argument survives shlex as ONE
    token with spaces in it, which is why `echo "python3 x/slack_client.py ..."`
    and a heredoc body do not qualify."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()

    found = set()
    for i, tok in enumerate(tokens):
        if not tok.endswith(".py") or any(c.isspace() for c in tok):
            continue
        j = i - 1
        while j >= 0:
            prev = tokens[j]
            if prev in RUNNERS or ENV_ASSIGN.match(prev) or \
                    (prev.startswith("-") and len(prev) <= 3):
                j -= 1
                continue
            break
        if j < 0 or tokens[j] in CMD_SEPS:
            found.add(os.path.basename(tok))
    return found

def response_text(d):
    """The tool's output as searchable text. '' when the harness sent none."""
    r = d.get("tool_response")
    if r is None:
        return ""
    if isinstance(r, str):
        return r
    try:
        return json.dumps(r, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(r)

def call_failed(d):
    """True when the tool call reported an error. A send that errored discharged
    nothing, so it must not mark the record as handled."""
    r = d.get("tool_response")
    if not isinstance(r, dict):
        return False
    if r.get("is_error") or r.get("isError"):
        return True
    for key in ("exit_code", "exitCode", "returncode", "status", "code"):
        v = r.get(key)
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and v != 0:
            return True
    return False

# MCP tools that discharge an item. Matched on tool_name.
MCP_DISCHARGES = {
    "transitionJiraIssue": "Jira ticket transitioned",
    "editJiraIssue": "Jira ticket edited",
    "addCommentToJiraIssue": "Jira comment posted",
    "createJiraIssue": "Jira ticket created",
    "updateConfluencePage": "Confluence page updated",
}

def project_dir():
    """CLAUDE_PROJECT_DIR when it is set and real, otherwise derived from this
    file's own location (two levels up from .claude/hooks/). The hardcoded WSL
    default other hooks use silently disables them on the macOS checkout, which
    is exactly where a stale ledger does the most damage."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(pathlib.Path(__file__).resolve().parent.parent.parent)

def session_path(project, session_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:80]
    d = os.path.join(project, SESSION_DIR)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{safe}.json")

def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    data.setdefault("discharges", [])
    data.setdefault("ledger_touches", [])
    data.setdefault("hand_edits", [])
    return data

def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

def emit(context):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        }
    }, ensure_ascii=False))
    sys.exit(0)

def main():
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        sys.exit(0)
    if not raw:
        sys.exit(0)
    try:
        d = json.loads(raw)
    except Exception:
        sys.exit(0)

    tool = str(d.get("tool_name") or "")
    ti = d.get("tool_input") or {}
    project = project_dir()
    path = session_path(project, str(d.get("session_id") or ""))
    state = load(path)
    now = time.time()
    note = None

    if tool in ("Write", "Edit", "NotebookEdit"):
        fp = os.path.abspath(str(ti.get("file_path") or ""))
        rel = os.path.relpath(fp, project) if fp.startswith(project) else fp
        if rel.startswith("journal/state/") and rel.endswith(".json"):
            state["hand_edits"].append({"at": now, "path": rel})
            note = (
                f"LEDGER HAND-EDIT: {rel} was written directly. That skips the "
                "ledger lock (so it can be lost to an overlapping cron sweep) and "
                "skips propagation (so no other session sees it).\n"
                "  Use the CLI instead - commitment_ledger.py / waiting_watchdog.py / "
                "decision_log.py / chase_queue.py.\n"
                "  If the edit has to stand, propagate it now:\n"
                "    python3 .agent/scripts/ledger_sync.py sync --reason \"<what changed>\""
            )
        elif rel == "journal/master_followup_tracker.md":
            note = (
                "GENERATED FILE: journal/master_followup_tracker.md is rendered from "
                "the three ledgers and this edit is overwritten on the next render. "
                "Change the ledger instead, then re-render."
            )

    elif tool == "Bash":
        cmd = str(ti.get("command") or "")
        if LEDGER_MUTATIONS.search(cmd):
            state["ledger_touches"].append({"at": now, "cmd": cmd[:200]})
        else:
            scripts = None
            out = None
            for label, pat, script, evidence in DISCHARGES:
                if not pat.search(cmd):
                    continue
                if scripts is None:
                    scripts = invoked_scripts(cmd)
                    out = response_text(d)
                # named but never run (a probe, an echo, a heredoc body, a
                # commit message). `script` may be a tuple when one label can be
                # discharged by more than one script; any of them counts.
                if script:
                    wanted = script if isinstance(script, tuple) else (script,)
                    if not any(s in scripts for s in wanted):
                        continue
                # ran and errored: nothing was discharged
                if call_failed(d):
                    continue
                # ran, but the success marker the script prints is absent. An
                # empty response means the harness sent no output to judge, so
                # fall back to the invocation check rather than going silent.
                if evidence and out and not evidence.search(out):
                    continue
                state["discharges"].append(
                    {"at": now, "what": label, "cmd": cmd[:200]})
                note = (
                    f"TRACKED ACTION: {label}. If this closes or advances a "
                    "commitment, a waiting-on, a decision, or a todo.md item, "
                    "update that record NOW, in this turn - "
                    "`commitment_ledger.py close COM-xxxx --note \"...\"`, "
                    "`waiting_watchdog.py close WAIT-xxxx`, "
                    "`decision_log.py decide DEC-xxxx --decision \"...\"`, "
                    "and tick the todo.md line. The CLI propagates it to every "
                    "reader automatically."
                )
                break

    else:
        short = tool.rsplit("__", 1)[-1]
        if short in MCP_DISCHARGES:
            label = MCP_DISCHARGES[short]
            state["discharges"].append({"at": now, "what": label, "cmd": short})
            note = (
                f"TRACKED ACTION: {label}. Update the matching ledger record and "
                "todo.md line in this turn so other sessions do not keep reading it "
                "as open."
            )

    try:
        save(path, state)
    except Exception:
        pass

    if note:
        emit(note)
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
