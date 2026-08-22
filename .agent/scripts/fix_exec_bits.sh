#!/usr/bin/env bash
# Restore executable bits that git expects but the filesystem has lost.
#
# Why: the desktop app now opens the repo over \\wsl.localhost (9P). Editing a
# shell script through that path can drop its +x bit, and the symptom is a
# confusing "permission denied" from cron rather than anything obviously
# path-related. Commit b32a77f was exactly this, for dashboard_keepalive.sh.
#
#   bash .agent/scripts/fix_exec_bits.sh          # report only
#   bash .agent/scripts/fix_exec_bits.sh --apply  # repair
set -uo pipefail
cd "$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")" || exit 1

apply=0
[ "${1:-}" = "--apply" ] && apply=1

fixed=0
found=0
while read -r mode _hash _stage path; do
  [ "$mode" = "100755" ] || continue
  [ -f "$path" ] || continue
  if [ ! -x "$path" ]; then
    found=$((found+1))
    if [ "$apply" = 1 ]; then
      chmod +x "$path" && { echo "  fixed  $path"; fixed=$((fixed+1)); }
    else
      echo "  needs +x  $path"
    fi
  fi
done < <(git ls-files -s)

if [ "$found" -eq 0 ]; then
  echo "all tracked executables have their +x bit. Nothing to do."
elif [ "$apply" = 1 ]; then
  echo "repaired $fixed file(s)."
else
  echo "$found file(s) need repair. Re-run with --apply."
fi
