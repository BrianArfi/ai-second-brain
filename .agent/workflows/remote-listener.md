---
description: Autonomously check, process, and complete remote instructions from the Telegram bridge queue.
---

**Windows-only workflow: every command below proxies into WSL via `wsl.exe`, which does not exist on macOS. Never run this workflow on macOS.**

1. Run the queue helper to check for any pending tasks:
   ```bash
   wsl.exe bash -c "python3 scripts/queue_helper.py get-pending"
   ```

2. Evaluate the output:
   * **If the output is `{"status": "empty"}`:**
     * Output: "No pending tasks. Antigravity remote control is idle."
     * (Do NOT reschedule the next check. The system is event-driven; the background Telegram bridge will automatically trigger the agent using `agentapi` when a new task is received).
   * **If a pending task is found (JSON output with `"command_id"` and `"command"`):**
     * Extract `command_id` and `command`.
     * Mark the task as active by running the start command:
       ```bash
       wsl.exe bash -c "python3 scripts/queue_helper.py start <command_id>"
       ```
     * **Execute the command:**
       * If the command is a shell/terminal command (e.g. starting with `python3`, `bash`, `wsl.exe`, `git`):
         * Proactively execute the command using `run_command`.
         * Capture the terminal logs and output.
       * If the command is a regular PM or text instruction (e.g. "Update todo P0", "Summary of Secondary Tasks"):
         * Perform the action using your standard workspace tools (reading files, updating files, searching).
         * Keep track of what changes you made.
     * **Complete the task:**
       * Gather the final text output/logs.
       * Identify any absolute paths of files that were generated or updated (separated by commas).
       * Write the result and attach files by running the complete command:
         ```bash
         wsl.exe bash -c "python3 scripts/queue_helper.py complete <command_id> --result '<text_result>' --files '<comma_separated_file_paths>'"
         ```
       * Note: Be very careful to escape quotes in your terminal arguments.
     * (Do NOT reschedule. The system is now fully event-driven).
