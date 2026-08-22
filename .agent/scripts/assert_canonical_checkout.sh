#!/usr/bin/env bash
# Checkout health, for whichever machine this session is running on.
#
# Background: on 3 Aug 2026 an entire evening's work was written to the Windows
# copy while all 19 cron jobs wrote to the WSL copy. The two drifted in BOTH
# directions, and documents were published to Drive from the stale side before
# anyone noticed. Nothing warned, because nothing was watching.
#
# The rule that came out of it is no longer "one machine is canonical". It is
# origin/main is the source of truth, WSL is the automation host, macOS is a
# first-class interactive checkout. So this script watches every checkout for
# the two ways a machine goes stale: behind origin, or holding work it never
# pushed. The old version only ever checked the WSL path, which is why the
# macOS clone could sit a week behind in silence.
#
# Exit is always 0. This informs, it does not block. It also makes no network
# call: the SessionStart hook budget is ~10s, and session_git_sync.py runs
# right after this and does the real fetch/pull.

WSL_HOST="."
MACOS_CHECKOUT="."
HERE="$(pwd -P 2>/dev/null || pwd)"

case "$(uname -s 2>/dev/null)" in
  Darwin)               PLATFORM="macos"   ;;
  Linux)                PLATFORM="wsl"     ;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
  *)                    PLATFORM="windows" ;;
esac

# First guard: the retired Windows scratch clone.
# Normalise the Windows mount so /mnt/c and C:\ compare the same.
case "$HERE" in
  /mnt/c/Users/the owner/.gemini/antigravity/scratch/product-second-brain*|\
  [Cc]:/Users/the owner/.gemini/antigravity/scratch/product-second-brain*|\
  *"\\Users\\the owner\\.gemini\\antigravity\\scratch\\product-second-brain"*)
    cat <<'EOF'

  ┌────────────────────────────────────────────────────────────────────┐
  │  RETIRED CHECKOUT                                                  │
  │                                                                    │
  │  You are in the Windows copy. All 19 cron jobs write to WSL:       │
  │    .          │
  │                                                                    │
  │  Work done here does NOT reach automation, and ledger writes here  │
  │  will be overwritten by the next sweep. This is what caused the    │
  │  3 Aug drift, where both copies held records the other had lost.   │
  │                                                                    │
  │  Prefer starting the session from the WSL path above.              │
  └────────────────────────────────────────────────────────────────────┘

EOF
    ;;
esac

# Second guard: keep core.fileMode out of the SHARED repo config.
#
# One directory is opened by two gits: WSL (ext4, real exec bits) and the
# desktop app over \\wsl.localhost (9P, which reports none). They share one
# .git/config, so a repo-local core.filemode=true -- which `git clone` on Linux
# writes by default -- makes the Windows side manufacture a 100755 => 100644
# deletion for EVERY tracked executable. On 8 Aug 2026 that was 52 phantom
# entries, indistinguishable from real work in `git status`. Committing them
# would have stripped +x off ~21 cron scripts for real, surfacing later as an
# opaque "permission denied" from cron (precedent: b32a77f).
#
# The setting is a property of the FILESYSTEM, not of the repo, so it belongs
# in each OS's global config. Repo-local always wins, so it has to go.
if git rev-parse --git-dir >/dev/null 2>&1; then
  if [ -n "$(git config --local --get core.fileMode 2>/dev/null)" ]; then
    git config --local --unset-all core.fileMode 2>/dev/null
    case "$PLATFORM" in
      macos|wsl) want=true  ;;   # real exec bits, honor them
      *)         want=false ;;   # MINGW/MSYS over 9P, ignore the noise
    esac
    git config --global core.fileMode "$want" 2>/dev/null
    echo "  note: removed repo-local core.fileMode (shared by WSL + Windows);"
    echo "        pinned it per-machine instead -> global core.fileMode=${want}."
  fi
fi

# Everything below needs a git repo. Bail out quietly if this is not one.
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# Compare the repo ROOT, not the working directory: a session started in
# journal/ or Clients/ is the same checkout and must not be flagged as a
# stray clone.
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || printf %s "$HERE")"

# Third guard: is this a checkout we recognise?
#
# Recognised checkouts stay silent, since naming them on every session start is
# noise. An unrecognised clone of this repo gets one line, because that is the
# case where work is most likely to be stranded somewhere nobody syncs.
origin_url="$(git config --get remote.origin.url 2>/dev/null)"
case "$origin_url" in
  *product-second-brain*)
    if [ "$PLATFORM" = "wsl" ] && [ "$ROOT" != "$WSL_HOST" ]; then
      echo "  note: this is not the WSL automation host (${WSL_HOST})."
      echo "        Cron writes there, so ledger sweeps will not see work left here."
    elif [ "$PLATFORM" = "macos" ] && [ "$ROOT" != "$MACOS_CHECKOUT" ]; then
      echo "  note: unrecognised macOS checkout. The usual one is ${MACOS_CHECKOUT}."
    fi
    ;;
esac

# Fourth guard: behind origin. Runs on EVERY checkout now, not just the WSL one.
#
# The count is measured against the last fetch, so it can lag: session_git_sync.py
# fetches immediately after this hook and will pull when the tree is clean.
behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
if [ "${behind:-0}" -gt 0 ] 2>/dev/null; then
  echo "  note: this checkout is ${behind} commit(s) behind origin/main. Consider: git pull"
fi

# Fifth guard: work committed here but never pushed.
#
# This is the failure the 3 Aug drift actually was. A commit that never reaches
# origin/main is invisible to the other machine and to all 19 cron jobs, and it
# looks perfectly safe locally because `git status` calls the tree clean. Only
# warn once it has been sitting for a while, so an in-progress session that is
# about to push is not nagged.
ahead=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
if [ "${ahead:-0}" -gt 0 ] 2>/dev/null; then
  oldest=$(git log --reverse --format=%ct origin/main..HEAD 2>/dev/null | head -1)
  now=$(date +%s 2>/dev/null)
  if [ -n "$oldest" ] && [ -n "$now" ]; then
    age_h=$(( (now - oldest) / 3600 ))
    if [ "$age_h" -ge 6 ]; then
      echo "  note: ${ahead} unpushed commit(s) here, oldest ${age_h}h old."
      echo "        origin/main is the source of truth: unpushed work is invisible to"
      echo "        the other machine and to cron. Consider: git push"
    fi
  fi
fi

exit 0
