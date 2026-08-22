#!/usr/bin/env python3
"""Git merge driver for journal/state/slack_mention_ledger.json.

Merging inside `save_state` stops one machine's write from erasing the other's.
It does nothing for the other half of the problem: two machines that each commit
a change to this file produce a git conflict, and a person resolving a conflict
in a 900 item JSON document resolves it by choosing a side. Choosing a side is
the same data loss, arriving through a different door.

So git is taught the same rule the writer uses. Union, advance, never revert.

Registered per clone (a merge driver cannot be committed, only its declaration
in .gitattributes can):

    bash .agent/scripts/install_ledger_merge_driver.sh

Called by git as: driver %O %A %B
    %O  the common ancestor
    %A  our version, and the file the result must be written to
    %B  their version

Exit 0 means resolved. A non-zero exit leaves the normal conflict markers, which
is the right outcome if anything here fails: a visible conflict is recoverable,
a silently wrong merge is not.
"""

import json
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(BASE_DIR, '.agent', 'skills', 'slack-tracker', 'scripts'))

def main():
    if len(sys.argv) < 4:
        print('usage: driver %O %A %B', file=sys.stderr)
        return 2
    _ancestor, ours_path, theirs_path = sys.argv[1], sys.argv[2], sys.argv[3]

    try:
        from ledger_merge import merge_states
        with open(ours_path) as f:
            ours = json.load(f)
        with open(theirs_path) as f:
            theirs = json.load(f)
    except Exception as e:
        # Fall back to a normal conflict rather than guessing.
        print(f'slack ledger merge driver: {type(e).__name__}: {e}', file=sys.stderr)
        return 1

    merged = merge_states(ours, theirs)
    tmp = ours_path + '.merged'
    with open(tmp, 'w') as f:
        json.dump(merged, f, indent=1, ensure_ascii=False)
    os.replace(tmp, ours_path)

    kept = len(merged.get('items') or {})
    print(f'slack ledger merged: {kept} item(s), no side discarded', file=sys.stderr)
    return 0

if __name__ == '__main__':
    sys.exit(main())
