---
description: "Daily - Refer another Claude session from this one: load what it asked, wrote, decided and left open"
argument-hint: "<session id | last | keyword> [task]  (empty = list recent; 'here' = this session's reference)"
---

Bring another session's context into this one, so the owner does not re-explain it.

Every session's reference looks like `/session f245e1e3` (the first 8 characters of its id). In the ASB app, right-click a session for **Copy session reference** or **Refer in this chat**.

Arguments: `$ARGUMENTS`

1. **Split the arguments.** Leading words that are ids (8+ hex characters, optional `session:` prefix) or `last` / `last-N` are references. The rest is the task to do with that context. One non-id word alone is a keyword search.
2. **No argument:** run `python3 .agent/scripts/session_ref.py list --days 2`, show the list, ask which one, stop.
3. **`here`:** run `python3 .agent/scripts/session_ref.py here` and hand the owner the `/session <id>` line to paste elsewhere.
4. **References:** run `python3 .agent/scripts/session_ref.py brief <ref> [<ref> ...]`. The current session is skipped automatically.
5. If the brief is truncated or the needed detail is missing, read the transcript path it prints (JSONL), only the part that matters.
6. **Verify before trusting it.** A transcript is what that session *said*, not current state. Before acting on a claim from it (sent, published, closed, blocked), check the primary source: `git log`, the ledger, the Slack permalink, the Drive link.
7. Reply with a two-to-three line "that session" brief: what it was for, what it produced (links), what is still open. Then do the task, if one was given.

**A session that is still running** can be asked directly: `ListAgents`, then `SendMessage` to its name.

Transcripts are per machine and not in git: Windows, WSL and macOS sessions cannot see each other's. `--all-projects` searches other repos' sessions on this machine.
