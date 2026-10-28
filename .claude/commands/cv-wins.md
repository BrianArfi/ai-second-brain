---
description: "Career - Track your product wins and add them to your CV"
argument-hint: "[month, for example 2026-09]"
---

Find the real wins in your product numbers and put them into your CV.

A win is a metric whose last closed month is at least 1.5 times its low of the six
months before, and is a new high. The tracker drafts a CV bullet for each win.
A bullet you do not answer within 7 days goes into your CV as drafted. You can reject
it at any time, even after it went in. Silence counts as a yes.

The skill lives in `.claude/skills/achievements-tracker/`. Read its `SKILL.md` first.
Run its script with `"${ASB_PYTHON:-python3}" .claude/skills/achievements-tracker/scripts/achievements.py <command>`.

Arguments given: `$ARGUMENTS`
(A month like `2026-09` means scan that month. Empty means the last closed month.)

Never message anyone else. Never make up a number. Every figure in a bullet must come
from the numbers file or from the user.

## 1. Set up (only if `.claude/skills/achievements-tracker/config.json` is missing)

Ask a few short questions. Use one AskUserQuestion call for the ones with a fixed answer.

1. **Your CV.** Where is it? If it is a markdown file in this workspace, use it.
   If it lives elsewhere (Google Docs, Word, PDF), offer to create `career/cv.md` and
   put the job history there. Do not write anything until they say yes.
2. **The job.** Which line in the CV starts the job these wins belong under
   (for example `**Acme Inc**`)? Bullets go after the last bullet of that section.
3. **The product.** What is it called, and what currency do its sales use?
4. **The numbers that matter.** For example orders, revenue, active users,
   signups. Ask for a size floor for each, the smallest monthly number worth
   writing about.
5. **Where the numbers live.** Check which tools are connected (Mixpanel, Metabase,
   Google Analytics, Sheets, or similar). Ask which one holds the monthly numbers.

Then copy `config.example.json` to `config.json` in the same folder and fill it in:
`cv_path`, `cv_section`, `product`, `currency`, `total_metrics`, `lead_metric`,
`metric_labels`, `floors`. Leave `source` on `series-file` with
`career/metrics_series.json`, and leave `notify` on `none`. Leave `root` as it is.
Show the user the finished file in plain words, not as raw JSON.

If no connected tool holds numbers, say so in one line. Point them to **Settings**,
then **Connected tools**. Tell them they can also paste or attach a monthly export and
you will use that instead. Do the rest of the setup, then stop.

## 2. Fill in the numbers (each run)

1. Read the last 7 months of monthly numbers for each configured metric through the
   connected tool. Use only closed months. Skip the month in progress.
2. Write them to `career/metrics_series.json` in this shape. Keep older months already
   in the file.
   `{"total": {"2026-09": {"orders": 700, "revenue": 50000}}, "groups": {"<segment>": {"2026-09": {"revenue": 30000}}}}`
   `groups` is optional. Use it for segments such as a plan, a market or a category.
   `cancel_pct` per month is optional. When present it adds a caveat on cancellations.
3. If the tool fails, needs a sign-in, or has no data, say which one and what the user
   should do. Do not guess. Do not fill gaps with estimates.

## 3. Scan and approve

1. Run `scan` (add `--month YYYY-MM` if an argument was given).
2. Run `auto-approve`.
3. Run `list`.

## 4. Show the result

Say it in plain words. Give the first line as the answer: how many wins were found,
how many were added to the CV, how many are waiting.

For each win show the bullet, its caveats, and the decisions attached to it
(if any). Then ask what to do with each pending one:

- Keep it: `approve ACH-xxxx` (add `--text "<their wording>"` if they rewrite it).
- Drop it: `reject ACH-xxxx --reason "..."`. This also removes a bullet that already went in.
- Leave it: it goes into the CV after 7 days of silence.

If the CV also lives somewhere else, tell the user which copy needs the new bullet.
After they update it, run `synced`.

A scan only sees that a number grew. It cannot see why. Remind the user once per run
that they should check each bullet is true to what they did, and fix the wording.

## 5. Monthly review (offer once)

If a calendar tool is connected and you have not offered this before, ask once:
"Want a monthly 'CV review' event so you check the new bullets?" Create it only after
a clear yes. If they say no, do not ask again. If no calendar is connected, skip this.

Also mention once that **Routines** has a "CV wins" routine, off by default,
that runs this command on the 2nd and 9th of each month.
