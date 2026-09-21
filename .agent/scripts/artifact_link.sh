#!/usr/bin/env bash
# Print a clickable link for a repo artifact, and optionally open it.
#
# Why this exists
# --------------
# On 4 Aug 2026 a project-plan HTML artifact was handed to the owner as a relative
# markdown link with percent-escaped spaces:
#     [OC_Project_Plan.html](Clients/Work/Online%20Catalogue/OC_Project_Plan.html)
# The terminal rendered it as text, not a link, so it could not be opened at all.
# Relative paths and %20 escapes do not resolve in the terminal. A file:// URL does.
#
# Auto-opening on every write is NOT the answer: that hook existed, stole focus,
# and was deliberately disabled on 26 Jul (see open_md_in_vscode.sh). This script
# is explicit, on demand.
#
# Usage
#   bash .agent/scripts/artifact_link.sh "Clients/Work/Example Catalogue/OC_Project_Plan.html"
#   bash .agent/scripts/artifact_link.sh --open "Clients/.../file.html"
#
# Accepts a repo-relative path or an absolute path. Quote paths containing spaces.

set -u

OPEN=0
if [ "${1:-}" = "--open" ]; then OPEN=1; shift; fi

if [ $# -lt 1 ]; then
  echo "usage: artifact_link.sh [--open] <path-to-artifact>" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IN="$1"

# Resolve to an absolute path
case "$IN" in
  /*) ABS="$IN" ;;
  *)  ABS="$REPO_ROOT/$IN" ;;
esac

if [ ! -f "$ABS" ]; then
  echo "not found: $ABS" >&2
  exit 1
fi

PLATFORM="$(bash "$REPO_ROOT/.agent/scripts/detect_platform.sh" 2>/dev/null | awk -F= '/^PLATFORM=/{print $2}')"
if [ -z "${PLATFORM:-}" ]; then
  case "$(uname -s)" in
    Darwin) PLATFORM="macos" ;;
    Linux)  PLATFORM="wsl" ;;
    *)      PLATFORM="windows" ;;
  esac
fi

# Percent-encode only what a file:// URL genuinely needs. Spaces are the common case.
#
# `python3` is not on PATH under Git Bash on Windows: the interpreter is `python`, and the name
# `python3` resolves to Microsoft's App Execution Alias, which prints a Store advertisement to
# stdout and exits 9009. That advertisement went into the URL, so the script printed `file:///`
# and nothing else. Pick whichever interpreter is actually there.
PY_BIN="$(command -v python3 2>/dev/null || true)"
if [ -z "$PY_BIN" ] || ! "$PY_BIN" -c '' >/dev/null 2>&1; then
  PY_BIN="$(command -v python 2>/dev/null || true)"
fi

urlencode() {
  if [ -n "$PY_BIN" ]; then
    "$PY_BIN" - "$1" <<'PY'
import sys, urllib.parse
print(urllib.parse.quote(sys.argv[1], safe="/:"))
PY
  else
    # No interpreter at all. A space is the only character that shows up in practice, and an
    # unencoded one breaks the link, so encode that much rather than printing nothing.
    printf '%s\n' "${1// /%20}"
  fi
}

case "$PLATFORM" in
  wsl)
    # the owner reads these from Windows, so give the UNC path that Windows can open.
    WINPATH="\\\\wsl.localhost\\Ubuntu${ABS//\//\\}"
    URLPATH="$(urlencode "$ABS")"
    echo "$WINPATH"
    echo "file://wsl.localhost/Ubuntu${URLPATH}"
    if [ "$OPEN" = "1" ]; then
      powershell.exe -NoProfile -Command "Start-Process '${WINPATH}'" >/dev/null 2>&1 \
        || explorer.exe "$WINPATH" >/dev/null 2>&1 || true
    fi
    ;;
  macos)
    echo "file://$(urlencode "$ABS")"
    [ "$OPEN" = "1" ] && open "$ABS"
    ;;
  *)
    # Windows native. Two things have to be right or the link opens nothing.
    #
    # 1. Git Bash hands back an MSYS path, `/c/Users/you/x.html`. Windows cannot open that, so
    #    the drive letter goes back where Windows expects it: `C:/Users/you/x.html`.
    # 2. A `file://` URL needs THREE slashes in front of a drive letter. `file://C:/x` names a host
    #    called `C:`; `file:///C:/x` names the path. The two-slash form was what this branch emitted,
    #    and it is why nothing handed over from a Windows session had ever been clickable.
    WINABS="$ABS"
    case "$WINABS" in
      /[A-Za-z]/*) WINABS="$(printf '%s' "${WINABS:1:1}" | tr '[:lower:]' '[:upper:]'):${WINABS:2}" ;;
    esac
    echo "${WINABS//\//\\}"
    echo "file:///$(urlencode "$WINABS")"
    [ "$OPEN" = "1" ] && start "" "$ABS" 2>/dev/null || true
    ;;
esac
