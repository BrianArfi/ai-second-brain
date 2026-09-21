# Parked variants

Nothing in here is imported by anything. It exists so that work which was real but unclaimed does
not get thrown away by a sync.

## `common_trimmed_member_workspace.py`

Found uncommitted in the Windows checkout on 15 Sep 2026 during a sync audit, as a modification
sitting on top of `meeting-recorder/common.py`. It is not a stale copy. It is a deliberately
trimmed rewrite, and its own header says so: it carries only what `recorder.py` imports
(`detect_platform`, `load_config`, `slugify`, `resolve_ffmpeg`), and the transcription and Gemini
helpers are absent on purpose because they reach into a repo layout that a member's workspace does
not have.

Two intentions therefore sat on one path: the full harness module in `origin/main`, and this
member-facing cut on one laptop only. the owner did not recall asking for it.

**What was done.** `meeting-recorder/common.py` is back to the full version, so the repo is
consistent and the recorder keeps working. The trimmed cut is parked here instead of being
overwritten, so it is now in git history and findable.

**What to do with it.** If a member-facing recorder bundle is ever built, whether that is the ASB
app's `resources/workspace-template` or something else, start from this file rather than trimming
the full module again. If that never happens, delete the folder. Either is fine; silently losing it
was not.
