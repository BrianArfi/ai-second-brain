# Sweep label pass

You label conversations a sweep found. Each one reached you because the rule layer could
not rule it out. Your job is to decide what it asks of the user, not to answer it.

Input: `survivors.json`, a list of conversations. Each has `key`, `sources`, `why_me`
(why it reached the user), `title`, `actors`, `age`, `link`, `root` (the message that
started the thread, when it is not one of the inbound ones), `your_last` (the user's own
last message in it, empty when they never wrote), and `messages` (the inbound messages
after the user's last reply, oldest first).

For each conversation pick exactly one label:

| Label | Use when |
| :--- | :--- |
| `blocking_someone` | A named person cannot move until the user acts: an access request, an approval, a sign-off their work waits on, a question with a deadline. |
| `needs_decision` | The user is explicitly asked to choose between options, approve, prioritise, or sign off. A question is not a decision. |
| `needs_reply` | A direct question or request to the user that expects an answer, and nothing waits on it hard. |
| `fyi` | Information the user may want: a status update, a shared doc with no ask, a meeting change, an RSVP for a routine recurring meeting, a reply that answers the user's own question. |
| `noise` | Nothing for the user: a closing remark, small talk, a message addressed to someone else, an automated notice with no ask, marketing, cold outreach. |

Rules:
- Read the messages, not the title. "Re: invitation" can hold a real question.
- Read `your_last` first. A message that answers the user's own question is `fyi`, even
  when it contains a question mark.
- `needs_decision` needs an explicit choice or approval asked of the user. When in doubt
  between `needs_decision` and `needs_reply`, choose `needs_reply`.
- A message that tags someone else and not the user, in a thread the user only took part in, is `noise` unless it also asks the user something.
- A mention in a large channel that only informs is `fyi`.
- A teammate's status update on work the user owns or tracks ("picked up the login ticket", "retest passed") is `fyi`, not `noise`.
- When unsure between `needs_reply` and `fyi`, choose `needs_reply`. A false drop costs more than a line of noise.
- Never label a DM with a question in it `noise`.
- A helper reports done on something the user asked them to do for a third person ("accepted
  it", "access granted", "fixed"): `needs_reply`. The person who first asked is still waiting
  to hear it is unblocked, and only the user can close that loop.
- Label every conversation in the input, with no exceptions. Count the keys in your output
  against the input before you write it. A skipped item reaches the report with no label.

Output: write `labels.json` next to the input, a JSON object keyed by `key`:

```json
{"slack:<channel>:<thread>": {"label": "needs_reply", "reason": "Noha asks for an update on the Omniful integration"}}
```

`reason` is one line, 15 words or fewer, and names who wants what: "<person> <verb>
<thing>". A reason that only restates the label ("asking to decide") is wrong. No other
output file, and do not contact anyone.
