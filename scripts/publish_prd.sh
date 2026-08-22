#!/usr/bin/env bash
# Publish a Work PRD/BRD to Google Docs, correctly, in one command.
#
# Exists because the manual sequence was run by hand on 16-17 Jul 2026 and steps
# got skipped twice, shipping wall-of-text docs and leaving one publicly shared.
# The order below is not negotiable:
#   gate source -> convert -> embed diagrams -> format pass -> restrict LAST -> verify doc
# "restrict LAST" matters: every convert re-publishes the doc as "anyone with link".
#
# Usage:
#   scripts/publish_prd.sh --file <path.md> --id <DOC_ID> [--account work]
#   scripts/publish_prd.sh --file <path.md> --title "PRD: ..." [--account work]   # first publish
#   scripts/publish_prd.sh --file <path.md> --id <DOC_ID> --share Teammate@examplevendor.com
#
# Options:
#   --no-restrict   skip the domain restriction (only for a doc meant to stay public)
#   --gate-only     lint the source and stop
set -euo pipefail

cd "$(dirname "$0")/.."

FILE=""; ID=""; TITLE=""; ACCOUNT="work"; SHARE=""; RESTRICT=1; GATE_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) FILE="$2"; shift 2;;
    --id) ID="$2"; shift 2;;
    --title) TITLE="$2"; shift 2;;
    --account) ACCOUNT="$2"; shift 2;;
    --share) SHARE="$2"; shift 2;;
    --no-restrict) RESTRICT=0; shift;;
    --gate-only) GATE_ONLY=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

# macOS has neither GNU `timeout` nor `grep -oP`. Both were used unguarded and
# both fail closed on a Mac: the create step exited rc=127 before python ever
# ran (14 Aug 2026). Resolve a timeout command once, tolerate not finding one.
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
fi
run_with_timeout() {   # run_with_timeout <seconds> <cmd...>
  local secs="$1"; shift
  if [[ -n "$TIMEOUT_BIN" ]]; then
    "$TIMEOUT_BIN" "${secs}s" "$@"
  else
    "$@"
  fi
}

[[ -z "$FILE" ]] && { echo "ERROR: --file is required" >&2; exit 2; }
[[ ! -f "$FILE" ]] && { echo "ERROR: no such file: $FILE" >&2; exit 2; }
[[ -z "$ID" && -z "$TITLE" ]] && { echo "ERROR: pass --id to update, or --title to create" >&2; exit 2; }

# Safety net, installed BEFORE the first step that can publish the doc.
#
# Every convert re-publishes the doc as "anyone with link", and the restrict
# step is deliberately LAST. Under `set -e`, any failure in between (20 Jul
# 2026: a kroki render belonging to an unrelated PRD) exited the script with
# the doc still world-readable. This runs on EVERY exit path: normal, `set -e`
# abort, explicit `exit`, and INT/TERM/HUP (whose handlers below exit so this
# EXIT trap still fires). It is a no-op once step 6 has already restricted.
RESTRICTED=0
PUBLISHED=0   # set to 1 the instant a step that can re-publish the doc starts
restrict_on_failure() {
  local rc=$?
  if [[ $rc -ne 0 && $PUBLISHED -eq 1 && $RESTRICTED -eq 0 && $RESTRICT -eq 1 && -n "$ID" ]]; then
    echo "" >&2
    echo "!! publish failed (rc=$rc). Restricting $ID so it is not left public." >&2
    if python3 .agent/scripts/drive_permissions.py restrict "$ID" \
         --domain yourcompany.com --apply 2>&1 | tail -2 >&2; then
      echo "!! restricted $ID to yourcompany.com" >&2
    else
      echo "!! RESTRICT ALSO FAILED. Doc $ID may be PUBLIC. Fix by hand now:" >&2
      echo "!!   python3 .agent/scripts/drive_permissions.py restrict $ID --domain yourcompany.com --apply" >&2
    fi
  fi
}
trap restrict_on_failure EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

echo "==> [1/6] Readability gate on source"
python3 scripts/readability_gate.py --source "$FILE" || {
  echo ""
  echo "BLOCKED. Fix the SOURCE markdown, never the Google Doc by hand." >&2
  exit 1
}
[[ $GATE_ONLY -eq 1 ]] && exit 0

# From here on the doc can become "anyone with link" at any moment, so the EXIT
# trap is armed. Set BEFORE the call, not after: the convert/create can publish
# the doc and then fail.
PUBLISHED=1

if [[ -n "$ID" ]]; then
  echo "==> [2/6] Converting into existing doc $ID"
  python3 .agent/skills/work-drive-connector/gdrive_manager.py update --id "$ID" --file "$FILE" --convert >/dev/null
else
  echo "==> [2/6] Creating new doc: $TITLE"
  # Capture the rc rather than letting `set -e` abort: gdocs_create can create
  # the doc (public by default) and THEN fail or hit the timeout. Extract the
  # ID from whatever it printed so the EXIT trap can still restrict it.
  CREATE_RC=0
  OUT=$(run_with_timeout 180 python3 .agent/skills/gdocs-create/gdocs_create.py create-doc \
        --title "$TITLE" --file "$FILE" --account "$ACCOUNT" 2>&1) || CREATE_RC=$?
  echo "$OUT"
  ID=$(echo "$OUT" | grep -oE 'ID:[[:space:]]*[A-Za-z0-9_-]+' | head -1 \
       | sed -E 's/^ID:[[:space:]]*//' || true)
  if [[ $CREATE_RC -ne 0 ]]; then
    echo "ERROR: doc creation failed (rc=$CREATE_RC), treat as FAILURE" >&2
    exit 1   # EXIT trap restricts $ID if one was created before the failure
  fi
  [[ -z "$ID" ]] && { echo "ERROR: no file ID returned, treat as FAILURE" >&2; exit 1; }
fi

echo "==> [3/6] Embedding mermaid diagrams"
# NON-FATAL BY DESIGN. A dead diagram renderer must never stop the pipeline
# reaching the restrict step. Record the outcome, report it loudly at the end.
# The || guard is required: under set -e a failing command substitution would
# kill the script before any of this ever prints.
EMBED_RC=0
EMBED_OUT=$(python3 scripts/embed_mermaid_in_gdoc.py --id "$ID" --account "$ACCOUNT" 2>&1) || EMBED_RC=$?
echo "$EMBED_OUT"
EMBED_PROBLEM=""
if [[ $EMBED_RC -eq 3 ]]; then
  EMBED_PROBLEM="one or more diagrams failed to render; literal [[PLACEHOLDER]] text is still in the doc"
  echo "WARNING: $EMBED_PROBLEM" >&2
  echo "WARNING: continuing to the format pass and the restrict step." >&2
elif [[ $EMBED_RC -ne 0 ]]; then
  EMBED_PROBLEM="the embed step failed hard (rc=$EMBED_RC); diagrams may be missing"
  echo "WARNING: $EMBED_PROBLEM" >&2
  echo "WARNING: continuing to the format pass and the restrict step." >&2
fi

echo "==> [4/6] Formatting pass"
python3 .agent/skills/gdocs-create/format_pass.py "$ID" --account "$ACCOUNT"

if [[ -n "$SHARE" ]]; then
  echo "==> [5/6] Sharing commenter access to $SHARE"
  python3 - "$ID" "$ACCOUNT" "$SHARE" <<'PY'
import sys
sys.path.insert(0, '.agent/skills/gdocs-create')
from gdocs_create import authenticate
from googleapiclient.discovery import build
doc_id, account, emails = sys.argv[1], sys.argv[2], sys.argv[3]
drive = build('drive', 'v3', credentials=authenticate(account))
for email in [e.strip() for e in emails.split(',') if e.strip()]:
    drive.permissions().create(
        fileId=doc_id,
        body={'type': 'user', 'role': 'commenter', 'emailAddress': email},
        sendNotificationEmail=False,
    ).execute()
    print(f"  granted commenter: {email}")
PY
else
  echo "==> [5/6] No --share requested, skipping"
fi

if [[ $RESTRICT -eq 1 ]]; then
  echo "==> [6/6] Restricting to yourcompany.com (LAST, converts re-publish public)"
  python3 .agent/scripts/drive_permissions.py restrict "$ID" --domain yourcompany.com --apply | tail -2
  RESTRICTED=1
else
  echo "==> [6/6] --no-restrict passed, doc left as-is"
fi

echo ""
echo "==> Verifying published doc"
GATE_ARGS=(--doc "$ID" --account "$ACCOUNT")
[[ $RESTRICT -eq 0 ]] && GATE_ARGS+=(--allow-public)
python3 scripts/readability_gate.py "${GATE_ARGS[@]}" || {
  echo "Published doc failed verification. Investigate before sharing the link." >&2
  exit 1
}

# The doc is now permissioned correctly, so it is safe to fail loudly. Surface
# any diagram that never rendered: the doc is publishable but visibly broken,
# and nobody should share it without knowing that.
if [[ -n "$EMBED_PROBLEM" ]]; then
  echo ""
  echo "########################################################################" >&2
  echo "## DOC IS NOT READY TO SHARE: $EMBED_PROBLEM" >&2
  grep -E 'RENDER FAIL|UNRENDERED|^  - \[\[' <<<"$EMBED_OUT" >&2 || true
  echo "##" >&2
  echo "## Permissions ARE correct (restrict step ran). Re-run the embed once" >&2
  echo "## the renderer recovers, then re-check the doc:" >&2
  echo "##   python3 scripts/embed_mermaid_in_gdoc.py --id $ID --account $ACCOUNT" >&2
  echo "########################################################################" >&2
  echo ""
  echo "Doc: https://docs.google.com/document/d/$ID/edit"
  exit 1
fi

echo ""
echo "File published: $FILE"
echo "  ID:  $ID"
echo "  URL: https://docs.google.com/document/d/$ID/edit"
