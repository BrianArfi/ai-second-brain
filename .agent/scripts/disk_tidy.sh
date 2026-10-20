#!/bin/bash
# Weekly disk tidy for the WSL cron host. Safe deletes only: caches and old WhatsApp status media.
# Reports free space on every Windows drive (C: warns under 30 GB, D-H under 50 GB). Never touches repos, git worktrees or data.
set -u
LOG=${DISK_TIDY_LOG:-$HOME/disk_tidy.log}
STATUS_DAYS=${STATUS_DAYS:-7}
{
echo "== disk_tidy $(date -Is)"
before=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)

# 1. Package caches (rebuildable)
command -v npm >/dev/null && npm cache clean --force >/dev/null 2>&1
[ -x "$HOME/.local/bin/uv" ] && "$HOME/.local/bin/uv" cache clean >/dev/null 2>&1
rm -rf "$HOME"/.gradle/caches/transforms-* "$HOME"/.gradle/caches/build-cache-* 2>/dev/null
command -v docker >/dev/null && docker system prune -f >/dev/null 2>&1

# 2. WhatsApp status broadcast media older than N days (bridge store and desktop app store)
for s in "$HOME"/wa-for-pm/whatsapp-bridge/store/status@broadcast /mnt/c/Users/you/AppData/Roaming/com.aisecondbrain.desktop/whatsapp/store/status@broadcast; do
  [ -d "$s" ] && find "$s" -type f -mtime +"$STATUS_DAYS" -delete 2>/dev/null
done

# 3. Report
after=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
cfree=$(df -BG --output=avail /mnt/c 2>/dev/null | tail -1 | tr -dc 0-9)
echo "wsl free GB: before=$before after=$after | windows C free GB: ${cfree:-?}"
# All Windows drives: C: warns under 30 GB, D-H under 50 GB (You/tools/disk_guard.py sends the Telegram alert)
for d in c d e f g h; do
  free=$(df -BG --output=avail "/mnt/$d" 2>/dev/null | tail -1 | tr -dc 0-9)
  [ -z "$free" ] && continue
  lim=50; [ "$d" = c ] && lim=30
  echo "drive ${d^^}: ${free} GB free (warn under ${lim})"
  [ "$free" -lt "$lim" ] && echo "WARNING: ${d^^}: under ${lim} GB free"
done
echo "report-only (not deleted): rust target dirs, old worktrees, video output"
du -sh "$HOME"/repos/*/src-tauri/target 2>/dev/null | sort -rh | head -5
} >> "$LOG" 2>&1
tail -12 "$LOG"
