# ASB app: path and link resolution

**Status:** proposal, 14 September 2026. Written after three link failures in one session.
**Split:** three changes belong to the app, one belongs to the workspace template. The test is the same one used in [`asb_app_skill_review.md`](asb_app_skill_review.md): if it needs repo state or one named client, it is workspace; otherwise it is app.

---

## 1. Why this exists

A single session on 14 September produced a file the user could not open, twice in a row, for two different reasons. The user's words: "kok gini lagi sih? kejadian terus ini gak bisa buka gini."

### Failure A: file written to a different checkout of the same repo

The session root was `C:/Users/you/.gemini/antigravity/scratch/product-second-brain`. The assistant wrote to `//wsl.localhost/Ubuntu./journal/...`, because the workspace's own `CLAUDE.md` names WSL the automation host and calls the Windows checkout retired.

The app reported:

> `journal/interview_prep_2026-09-14_zuhair_salkhadi.md` couldn't be opened. The app looked in `C:\Users\the owner\.gemini\antigravity\scratch\product-second-brain\journal\interview_prep_2026-09-14_zuhair_salkhadi.md`, which doesn't exist. The link looks like it lost its leading path prefix.

Two things are wrong here, and only the second is the app's.

The file genuinely was not in that checkout, so the app was right to fail. **But the stated cause was invented.** The link had not lost a prefix. The app guessed at a reason, and the guess sent everyone looking in the wrong place.

### Failure B: a Windows absolute path mangled into a UNC path

After copying the file into the session's own checkout, the assistant wrote the path in bold markdown with a backslash that needed escaping. What reached the renderer was `C:\Users\the owner\\.gemini\antigravity\...`. The app then reported:

> `\\.gemini\antigravity\scratch\product-second-brain\journal\interview_prep_2026-09-14_zuhair_salkhadi.md` isn't there any more, or was never written to that path.

The renderer consumed `C:\Users\the owner\` and treated the remaining `\\.gemini\...` as a UNC root. The file existed and was correct. **Only the rendering failed.**

### Failure C, historical: relative link resolved against the workspace root

Already recorded in `CLAUDE.md`, 22 August 2026. An index file in a sibling repo wrote `../drafts/x.png`. The app resolved it from the **session workspace root** rather than from the folder the file sits in, looked for `product-second-brain/../drafts/x.png`, and failed.

### Why the existing guard did not catch any of it

`.claude/hooks/link_guard.py` has known all three checkout roots since 11 September and does flag a link written for another machine. It passed all three times, because **it runs on whichever host invoked it**. Invoked through WSL, it checked the WSL filesystem, found the file, and reported "all links resolve." A guard that validates against a filesystem the reader cannot see is not a guard.

---

## 2. Classification

| # | Change | Owner | Severity |
| :--- | :--- | :--- | :--- |
| A1 | Resolve relative links against more than the workspace root | App | High, silent failure |
| A2 | Normalise Windows paths before markdown render | App | High, breaks correct paths |
| A3 | Replace the guessed not-found cause with stated facts | App | Medium, misleads debugging |
| W1 | An `ASB:MANAGED` paths block in the workspace `CLAUDE.md` | Workspace template | High, prevents failure A |

---

## 2.5 What the code already does, read 14 Sep 2026

Checked against `src-tauri/src/viewer.rs` in `repos/ai-second-brain-app` (0.13.11, branch `fix/viewer-windows-drive-letter-root`) and `repos/asb-main` (0.14.0, which already carries the merge).

| Behaviour | State |
| :--- | :--- |
| `file:///C:/...` losing its scheme and doubling the drive letter | **Already fixed**, commit `40f501d`, `strip_drive_letter_root`. Shipped in 0.14.0. |
| Linux path opened on Windows, translated through `\\wsl.localhost\<distro>` | Already there, `resolve_readable_path` |
| WSL UNC path opened on Linux, distro segment dropped | Already there |
| Stale distro name retried against distros that exist | Already there |
| **Relative path that misses under the workspace root** | **No fallback at all.** This is failure A. |
| The "looked in / lost its leading path prefix" message | **Not in either repo.** See the open question below. |

So A2 as originally written is largely done for the forward-slash `file:///` spelling. What remains is the **backslash** spelling, which is a renderer problem rather than a resolver one.

---

## 3. App change A1: the relative-path gap, and why the obvious fix is wrong

**The original proposal in this document was to retry a relative path against every known root. Do not do that.** The codebase already considered it and refused it, for a good reason. From `resolve_openable_path`:

> `home` is the workspace the request was made in, and it is the only base a RELATIVE path is ever joined to. Retrying a relative path against every root in turn is exactly the wrong-file-under-the-right-name failure the `workspace_id` parameter exists to close, so it is not done.

There are two checkouts of this repository on this machine and one was 44 commits behind the other on the day of the failure. A blind retry would have silently served the stale copy of a file, which is worse than a dead link because nothing tells the reader.

**But the intent is already on the record**, in `resolve_readable_path`:

> this harness spans several checkouts, the model names files in all of them, and every one of those references came back as a dead link

The gap is that every existing fallback keys off a path that *looks* absolute. A plain `journal/foo.md` reaches none of them.

**Narrowed proposal, for reading only, never for opening:**

1. Retry the relative path under other **registered** roots only, never arbitrary ones.
2. Return it with provenance, and have the frontend say in one line which checkout it came from.
3. When the same relative path resolves in more than one root, **do not pick**. Report both and let the reader choose. That is the wrong-file-under-the-right-name case, and the only safe answer is to surface it.
4. Leave `resolve_openable_path` exactly as it is. Handing a path to the OS keeps the strict rule.

**Honest assessment:** this is worth doing, but it would not have saved 14 September. The file was in no registered workspace at all, only in a WSL checkout the app had never been pointed at. W1 is the change that actually prevents that failure. A1 reduces a class of dead links; it does not close this one.

---

## 4. App change A2: normalise Windows paths before markdown render

**Today.** A Windows absolute path that reaches the markdown renderer can be mangled. A run of two backslashes is read as a UNC root, and any preceding drive-letter segment is discarded.

**Proposed.** Before rendering, detect a path-shaped token and normalise it rather than trusting the author's escaping:

- A token matching `^[A-Za-z]:[\\/]` is a Windows absolute path. Convert backslashes to forward slashes and treat the whole token as one path. Never reinterpret an interior `\\` as a UNC root once a drive letter has been seen.
- A token starting `\\\\` or `//` with no drive letter is a genuine UNC path. Leave it.
- Apply this inside bold, italic and inline code as well as in plain text, because a path in bold is where this broke.

**Why the app and not the author.** Requiring the assistant, or the user, to hand-escape backslashes correctly every time is a rule that will be broken again. The app has the path and can see its shape.

---

## 5. App change A3: state facts in the not-found message

**Today.** The message asserts a cause: "the link looks like it lost its leading path prefix." That was false, and it cost time.

**Proposed.** State only what was checked:

> `journal/interview_prep_2026-09-14_zuhair_salkhadi.md` was not found.
> Looked in:
> - `C:/Users/you/.gemini/antigravity/scratch/product-second-brain/` (workspace root)
> - `C:/Users/you/.gemini/antigravity/scratch/product-second-brain/journal/` (link's own folder)
> Other known checkouts of this repository were not searched: `//wsl.localhost/Ubuntu./`. Search there?

The offer at the end is what turns a dead end into a fix, and it is only possible once A1 knows the other roots.

---

## 6. Workspace change W1: an `ASB:MANAGED` paths block

No app version fixes failure A, because the app was correct to look where the session runs. The defect is that the workspace told the assistant to write somewhere else. `CLAUDE.md` says `origin/main` is the source of truth and the Windows checkout is retired, and says nothing about where a file the user will open should land.

The app already injects and maintains three managed blocks in `CLAUDE.md`: `branching`, `worktree`, `connections`. Adding a fourth is the mechanism that guarantees every workspace on the latest version carries the rule with nobody editing anything by hand.

Proposed content:

```markdown
<!-- ASB:MANAGED paths START -->
## Files The User Will Open

A file the user is meant to open is written to the checkout **this session is running in**,
not to whichever checkout the workspace calls canonical. The app opens files relative to the
session's own root. A file written to a second checkout of the same repository does not exist
as far as the app is concerned, however correct the path looks.

- **Write where the session runs.** Other checkouts are for scripts, cron and automation.
  If a file must exist in both, copy it. Do not choose one.
- **Hand over a path with forward slashes.** `C:/Users/.../file.md`, never `C:\Users\...`.
  A backslash path can be mangled into a UNC path by the renderer, and the file then
  appears to be missing when it is not.
- **Validate on the machine that will do the opening.** A link checker run through a proxy
  shell validates against a filesystem the reader cannot see, and passes a link that
  is broken for the user.
<!-- ASB:MANAGED paths END -->
```

---

## 7. Acceptance tests

| # | Setup | Expected |
| :--- | :--- | :--- |
| 1 | Relative link to a file beside the document, document not at workspace root | Opens |
| 2 | Relative link, file only in another known checkout of the same repo | Opens, with a one-line note naming the checkout it came from |
| 3 | `C:\Users\x\.foo\bar.md` inside `**bold**` | Opens, no UNC reinterpretation |
| 4 | `//wsl.localhost/Ubuntu/home/x/bar.md` on Windows | Opens, still treated as UNC |
| 5 | File genuinely absent everywhere | Message lists every root searched, names unsearched known checkouts, asserts no cause |
| 6 | Fresh workspace created on the latest app version | `CLAUDE.md` contains the `ASB:MANAGED paths` block |

---

## 7.5 Open question, blocking the app-side work

**Which checkout is the release source?** There are five on this machine and they disagree:

| Repo | Version | Head |
| :--- | :--- | :--- |
| `repos/asb-main` | 0.14.0 | `6c7ae3e chore(release): bump to 0.14.0` |
| `repos/ai-second-brain-app` | 0.13.11 | `40f501d` on `fix/viewer-windows-drive-letter-root` |
| `repos/ai-second-brain` | n/a | `080368d chore: sync from private repo 2026-09-11` |
| `repos/asb-fixes` | n/a | `2afc71e` |
| `repos/asb-082` | n/a | not inspected |

Neither error message the user actually saw on 14 September exists in any of them:

- "couldn't be opened. The app looked in ... the link looks like it lost its leading path prefix"
- "isn't there any more, or was never written to that path"

Either the running build is newer than every local checkout, or those banners are composed outside the app repo. **A3 cannot be implemented until that source is located**, and A2's backslash case needs the same answer, because the renderer that mangles the path lives wherever those strings do.

---

## 8. Out of scope

- Reconciling "`origin/main` is the source of truth" with a session running against a stale checkout. That is a workspace decision, and in this repo the Windows checkout was 44 commits behind when these failures happened. W1 makes the symptom stop; it does not make the checkouts agree.
- Fetching `http(s)` links. `link_guard.py` deliberately does not, and nothing here changes that.
