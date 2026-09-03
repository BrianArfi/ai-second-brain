#!/usr/bin/env python3
"""Regression test for recover_stuck_rebase (ledger_sync.py).

The incident, 2 Sep 2026 21:43-22:45 WIB: a `rebase --autostash` was killed
mid-flight when its parent Stop hook hit the harness timeout, leaving
`.git/rebase-merge` behind and HEAD detached at origin/main. For the next hour
every sync path declined to act ("not on main; left uncommitted") because the
only code able to clear a stuck rebase sat behind the `on_main()` bail-outs.
`recover_stuck_rebase` closes that: an AGED rebase state dir is aborted (or,
when truncated beyond git's ability to abort, removed), while a fresh one is
left alone because its owner may still be alive.

Run: python3 tests/test_stuck_rebase.py
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
LEDGER_SYNC = os.path.join(REPO_ROOT, '.agent', 'scripts', 'ledger_sync.py')

OLD = (10_000_000, 10_000_000)          # epoch mtime far in the past


def load_ledger_sync(base_dir):
    spec = importlib.util.spec_from_file_location('ledger_sync_under_test',
                                                  LEDGER_SYNC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BASE_DIR = base_dir
    return mod


def run_git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True,
                          text=True)


class StuckRebaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='psb-stuck-rebase-')
        run_git(self.tmp, 'init', '-q')
        run_git(self.tmp, 'config', 'user.email', 't@t')
        run_git(self.tmp, 'config', 'user.name', 't')
        with open(os.path.join(self.tmp, 'f.txt'), 'w') as f:
            f.write('a\n')
        run_git(self.tmp, 'add', '.')
        run_git(self.tmp, 'commit', '-qm', 'base')
        run_git(self.tmp, 'branch', '-M', 'main')
        self.mod = load_ledger_sync(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rebase_dir(self):
        return os.path.join(self.tmp, '.git', 'rebase-merge')

    def start_conflicted_rebase(self):
        run_git(self.tmp, 'checkout', '-qb', 'other')
        with open(os.path.join(self.tmp, 'f.txt'), 'w') as f:
            f.write('b\n')
        run_git(self.tmp, 'commit', '-qam', 'other')
        run_git(self.tmp, 'checkout', '-q', 'main')
        with open(os.path.join(self.tmp, 'f.txt'), 'w') as f:
            f.write('c\n')
        run_git(self.tmp, 'commit', '-qam', 'mainside')
        run_git(self.tmp, 'rebase', 'other')      # stops on the conflict
        self.assertTrue(self.mod.rebase_in_progress())

    def test_aged_real_rebase_is_aborted_and_branch_restored(self):
        self.start_conflicted_rebase()
        os.utime(self.rebase_dir(), OLD)
        self.assertTrue(self.mod.recover_stuck_rebase())
        self.assertFalse(self.mod.rebase_in_progress())
        head = run_git(self.tmp, 'rev-parse', '--abbrev-ref', 'HEAD')
        self.assertEqual(head.stdout.strip(), 'main')

    def test_fresh_rebase_left_alone(self):
        self.start_conflicted_rebase()            # mtime = now
        self.assertFalse(self.mod.recover_stuck_rebase())
        self.assertTrue(self.mod.rebase_in_progress())
        run_git(self.tmp, 'rebase', '--abort')

    def test_truncated_state_dir_is_removed(self):
        # A rebase-merge dir with no head-name: the process died before writing
        # its state. `git rebase --abort` cannot handle it; removal can.
        os.makedirs(self.rebase_dir(), exist_ok=True)
        os.utime(self.rebase_dir(), OLD)
        self.assertTrue(self.mod.recover_stuck_rebase())
        self.assertFalse(self.mod.rebase_in_progress())

    def test_commit_and_push_recovers_instead_of_bailing(self):
        # Detached HEAD + aged stuck rebase: commit_and_push used to return
        # "not on main; left uncommitted"; now it clears the rebase and goes on.
        self.start_conflicted_rebase()
        os.utime(self.rebase_dir(), OLD)
        self.assertFalse(self.mod.on_main())
        result = self.mod.commit_and_push('test', do_push=False)
        self.assertNotEqual(result.get('note'), 'not on main; left uncommitted')
        self.assertTrue(self.mod.on_main())


if __name__ == '__main__':
    unittest.main(verbosity=2)
