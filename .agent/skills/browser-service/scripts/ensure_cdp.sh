#!/bin/bash
# ensure_cdp.sh — Ensures the Chrome CDP service is running on port 9222.
# Exits 0 if CDP is already running or was successfully started.
# Exits 1 if it cannot start.

CDP_PORT=9222
CDP_URL="http://127.0.0.1:${CDP_PORT}/json/version"

# ── 1. Quick check: is CDP already responsive? ──
if curl -s --max-time 2 "$CDP_URL" | grep -q "Browser"; then
    echo "✅ CDP service already running on port $CDP_PORT."
    exit 0
fi

echo "⚡ CDP not responsive. Starting Chromium..."

# ── 2. Find a Chromium binary ──
CHROME_PATH=""

# 2a. ms-playwright cache (Antigravity / VS Code standard)
candidates=($(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | sort -V))
if [ ${#candidates[@]} -gt 0 ]; then
    CHROME_PATH="${candidates[-1]}"
fi

# 2b. System-installed fallback
if [ -z "$CHROME_PATH" ]; then
    CHROME_PATH=$(which chromium-browser 2>/dev/null \
               || which chromium 2>/dev/null \
               || which google-chrome 2>/dev/null \
               || which google-chrome-stable 2>/dev/null)
fi

# 2c. macOS app bundles. None of the paths above match a Mac, so a machine with
# Chrome installed still reported "No Chromium/Chrome binary found" until this
# was added on 24 Aug 2026.
if [ -z "$CHROME_PATH" ]; then
    for candidate in \
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
        "/Applications/Chromium.app/Contents/MacOS/Chromium" \
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"; do
        if [ -x "$candidate" ]; then CHROME_PATH="$candidate"; break; fi
    done
fi

if [ -z "$CHROME_PATH" ]; then
    echo "❌ No Chromium/Chrome binary found."
    exit 1
fi

echo "   Using: $CHROME_PATH"

# ── 3. Launch headless Chromium with CDP ──
nohup "$CHROME_PATH" \
    --headless \
    --remote-debugging-port=$CDP_PORT \
    --remote-debugging-address=127.0.0.1 \
    --no-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --user-agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
    --user-data-dir="$HOME/.config/antigravity-chrome-data" \
    > /tmp/chrome_stdout.log 2>/tmp/chrome_stderr.log &

# ── 4. Wait up to 5 seconds for CDP to become responsive ──
for i in $(seq 1 10); do
    sleep 0.5
    if curl -s --max-time 1 "$CDP_URL" | grep -q "Browser"; then
        echo "✅ CDP service started successfully on port $CDP_PORT."
        exit 0
    fi
done

echo "❌ CDP service failed to start within 5 seconds."
echo "   Check /tmp/chrome_stderr.log for details."
exit 1
