---
name: mcp-switcher
description: Swaps between mutually exclusive MCP servers to stay under the tool-count limit. Use when a tool you need belongs to a server that is currently disabled.
---

# MCP Switcher Skill

This skill allows Antigravity to dynamically swap between mutually exclusive MCP servers to stay under the 100-tool limit.

## Usage
When you need a tool that belongs to a disabled MCP server (e.g., Supabase vs Atlassian):
1. Use the `mcp_switcher.py` script to swap them.
2. The script will modify `mcp_config.json` by renaming keys (the most reliable way to "disable" them for most MCP clients).

## Implementation
The script is located at `.agent/scripts/mcp_switcher.py`.

### Commands
- `python .agent/scripts/mcp_switcher.py atlassian supabase` -> Enables Atlassian, Disables Supabase.
- `python .agent/scripts/mcp_switcher.py supabase atlassian` -> Enables Supabase, Disables Atlassian.

## Why this is needed
The platform has a limit of 100 tools. Currently:
- Atlassian + Google Sheets + others > 100.
- Supabase + Google Sheets + others > 100.
By swapping Atlassian and Supabase, we stay under the limit.
