#!/usr/bin/env python3
"""One source for the owner's Slack voice, shared by every automated drafter.

Three scripts used to carry their own hand-written "write in the owner's voice" paragraph,
and all three described a voice the corpus does not support: flowing prose, two to five
sentences, sentence case. His median Slack message is 61 characters. So the description
lived in three places and was wrong in all three.

`voice_block()` returns the measured model, read from the no-ai-slop skill so editing the
skill updates every drafter at once.

    from brian_voice import voice_block
    PROMPT = voice_block() + "\\n\\n" + task_specific_instructions
"""
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
VOICE_PATH = os.path.join(BASE_DIR, '.agent', 'skills', 'no-ai-slop', 'brian_voice_prompt.txt')

# Used when the skill file is missing (a partial checkout, or the public template). Short
# on purpose: the point is that a drafter never silently falls back to no voice guidance.
FALLBACK = (
    "HOW OWNER WRITES ON SLACK\n"
    "His median Slack message is 61 characters and 75% are under 200. Write short. One "
    "message, one thing. Lowercase is normal. Soften every ask with a sentence-final "
    "'ya' (no question mark), a question, or 'right?'. 'ya?' with a question mark is only "
    "for a yes/no confirmation, never on a statement or an instruction. Ask rather than "
    "instruct. No em-dash, no heading, no bold, no table, no sign-off, no closing recap."
)

_cache = None

def voice_block():
    """The measured voice model, as a prompt block."""
    global _cache
    if _cache is None:
        try:
            with open(VOICE_PATH, encoding='utf-8') as f:
                _cache = f.read().strip()
        except OSError:
            _cache = FALLBACK
    return _cache

if __name__ == '__main__':
    print(voice_block())
