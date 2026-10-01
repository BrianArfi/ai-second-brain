#!/usr/bin/env bash
# AI Second Brain: one-command bootstrap.
# Level 0 gets you a working conversational brain in ~15 minutes.
# Connectors (Google, Slack, ...) come later via docs/SETUP.md.
#
# Usage:  bash install.sh
# Safe to re-run: it never overwrites a file you already customized.

set -u

ok()   { printf '  \033[32m✔\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m•\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✘\033[0m %s\n' "$1"; }

echo ""
echo "AI Second Brain: bootstrap"
echo "==========================="

# 1. Prerequisites -------------------------------------------------------------
echo ""
echo "[1/5] Checking prerequisites"

MISSING=0
if command -v git >/dev/null 2>&1; then
    ok "git $(git --version | awk '{print $3}')"
else
    fail "git not found: install it first (macOS: xcode-select --install, Ubuntu/WSL: sudo apt install git)"
    MISSING=1
fi

if command -v python3 >/dev/null 2>&1; then
    ok "python3 $(python3 -V 2>&1 | awk '{print $2}')"
else
    warn "python3 not found: only needed for connectors (Level 1+). Level 0 works without it."
fi

if command -v claude >/dev/null 2>&1; then
    ok "Claude Code $(claude --version 2>/dev/null | head -1)"
else
    warn "Claude Code CLI not found."
    warn "Install: npm install -g @anthropic-ai/claude-code   (or see https://claude.com/claude-code)"
    warn "You can finish this script now and install Claude Code after."
fi

if [ "$MISSING" -eq 1 ]; then
    echo ""
    fail "Fix the items above, then re-run: bash install.sh"
    exit 1
fi

# 2. Runtime detection ----------------------------------------------------------
echo ""
echo "[2/5] Detecting your runtime"

if [ -f .agent/scripts/detect_runtime.sh ]; then
    RUNTIME_OUT="$(bash .agent/scripts/detect_runtime.sh 2>/dev/null || true)"
    RT_RUNTIME="$(printf '%s\n' "$RUNTIME_OUT" | sed -n 's/^RUNTIME=//p')"
    RT_MODEL="$(printf '%s\n' "$RUNTIME_OUT" | sed -n 's/^MODEL=//p')"
    RT_TIER="$(printf '%s\n' "$RUNTIME_OUT" | sed -n 's/^TIER=//p')"
    RT_SUBAGENTS="$(printf '%s\n' "$RUNTIME_OUT" | sed -n 's/^SUBAGENTS=//p')"
    if [ -n "$RT_RUNTIME" ] && [ "$RT_RUNTIME" != "unknown" ]; then
        ok "Runtime: $RT_RUNTIME (model: ${RT_MODEL:-unknown}, tier: ${RT_TIER:-unknown})"
        if [ "$RT_SUBAGENTS" = "yes" ]; then
            ok "Subagent delegation available"
        else
            warn "No typed subagent support detected, so delegation will run through agy-bridge or inline"
        fi
    else
        warn "Could not identify a specific runtime yet, which is fine, the harness degrades safely."
        warn "It will detect again once you're inside a session (claude, cursor, antigravity, ...)."
    fi
else
    warn "detect_runtime.sh not found, skipping runtime detection"
fi

# 3. Your brain file -----------------------------------------------------------
echo ""
echo "[3/5] Creating your brain file"

if [ -f CLAUDE.md ]; then
    ok "CLAUDE.md already exists: keeping yours"
elif [ -f CLAUDE.md.template ]; then
    cp CLAUDE.md.template CLAUDE.md
    ok "CLAUDE.md created from template: /setup fills it in with you"
else
    fail "CLAUDE.md.template missing: are you running this from the repo root?"
    exit 1
fi

# 4. Environment file ----------------------------------------------------------
echo ""
echo "[4/5] Creating your .env"

if [ -f .env ]; then
    ok ".env already exists: keeping yours"
elif [ -f .env.example ]; then
    cp .env.example .env
    ok ".env created: fill in API keys later, only for the connectors you use"
else
    warn ".env.example missing: skipped"
fi

# 5. Python dependencies (optional, for connectors) ----------------------------
echo ""
echo "[5/5] Python dependencies (optional, connectors only)"

if command -v python3 >/dev/null 2>&1 && [ -f requirements.txt ]; then
    if python3 -m pip install -r requirements.txt --quiet 2>/dev/null; then
        ok "Python dependencies installed"
    else
        warn "pip install failed or was skipped: fine for Level 0."
        warn "Retry later with: python3 -m pip install -r requirements.txt"
    fi
else
    warn "Skipped (no python3 or no requirements.txt): fine for Level 0."
fi

# Done -------------------------------------------------------------------------
echo ""
echo "Done. Next steps:"
echo "  1. Run:   claude"
echo "  2. Type:  /setup"
echo "     It interviews you about your work and writes your CLAUDE.md."
echo "     (docs/CUSTOMIZING.md explains every section, if you edit it by hand)"
echo ""
echo "When you want it connected to Google Docs, Slack, calendars, and meetings:"
echo "  docs/SETUP.md: the full guide, connector by connector."
echo ""
