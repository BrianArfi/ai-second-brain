#!/bin/zsh
# Kills AI Second Brain chat processes that have been idle past a threshold.
#
# The app spawns one `claude -p` per chat and only stops it on an explicit Stop,
# a settings change, or an agent change. A chat you finish reading keeps its
# process, its MCP servers (~3 node procs, ~130 MB) and ~400 MB of footprint
# for as long as the app runs. Sixty of them do not fit in 16 GB.
#
# Killing one is safe: the app resumes the same conversation by id on the next
# message, which is the same path a settings change already uses.
#
# Usage:  reap_stale_sessions.sh [idle_hours]   (default 6, dry run with DRY=1)

set -u
IDLE_H=${1:-6}
NOW=$(date +%s)
TDIRS=(
  "$HOME/.claude/projects/-Users-you-product-second-brain"
  "$HOME/Library/Application Support/com.aisecondbrain.desktop/transcripts"
)
MYCHAIN=""
p=$PPID
while [[ -n "$p" && "$p" != "1" ]]; do MYCHAIN="$MYCHAIN $p"; p=$(ps -o ppid= -p $p 2>/dev/null | tr -d ' '); done

targets=()
for pid in ${(f)"$(ps -Axo pid,command | grep '[c]laude -p --input-format' | awk '{print $1}')"}; do
  [[ " $MYCHAIN " == *" $pid "* ]] && continue
  # BSD sed has no alternation; grep the flag then take the id after it.
  sid=$(ps -o command= -p $pid 2>/dev/null | grep -oE -- '--(resume|session-id) [0-9a-f-]{36}' | awk '{print $2}' | head -1)
  idle=99999
  for d in $TDIRS; do
    f="$d/$sid.jsonl"
    [[ -n "$sid" && -f "$f" ]] && { i=$(( (NOW - $(stat -f %m "$f")) / 3600 )); (( i < idle )) && idle=$i; }
  done
  # No transcript found = the chat never wrote one; fall back to process age.
  if (( idle == 99999 )); then
    st=$(ps -o lstart= -p $pid 2>/dev/null)
    [[ -n "$st" ]] && idle=$(( (NOW - $(date -j -f "%a %b %e %T %Y" "$st" +%s 2>/dev/null || echo $NOW)) / 3600 ))
  fi
  (( idle >= IDLE_H )) && targets+=("$pid:$idle")
done

if (( ${#targets} == 0 )); then echo "nothing idle >= ${IDLE_H}h"; exit 0; fi
echo "stale (idle >= ${IDLE_H}h): ${#targets} session(s)"
for t in $targets; do echo "  pid=${t%%:*} idle=${t##*:}h"; done
[[ "${DRY:-0}" == "1" ]] && { echo "(dry run)"; exit 0; }

for t in $targets; do
  pid=${t%%:*}
  for k in ${(f)"$(pgrep -P $pid 2>/dev/null)"}; do
    for g in ${(f)"$(pgrep -P $k 2>/dev/null)"}; do kill -9 $g 2>/dev/null; done
    kill -9 $k 2>/dev/null
  done
  kill -9 $pid 2>/dev/null
done
sleep 5
orph=$(ps -Axo pid,ppid,command | grep '[s]lack-mcp-server' | awk '$2==1{print $1}')
[[ -n "$orph" ]] && kill -9 ${=orph} 2>/dev/null
echo "sessions left: $(ps -Axo command | grep -c '[c]laude -p --input-format')"
sysctl -n vm.swapusage
