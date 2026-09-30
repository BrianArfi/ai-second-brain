#!/usr/bin/env python3
"""Export one skill folder as its own git repository, leak-audited and tagged.

    python3 .agent/scripts/publish_skill_repo.py artifact-comments
    python3 .agent/scripts/publish_skill_repo.py artifact-comments --target ~/repos/artifact-comments
    python3 .agent/scripts/publish_skill_repo.py artifact-comments --push      # after the owner approves

What it does, in order:

1. Reads the version: the newest released section of the skill's CHANGELOG.md
   (`## [x.y.z] - YYYY-MM-DD`). Every other version declaration it finds (a
   `VERSION = '...'` constant, a package.json "version", a root VERSION file)
   must agree, or it stops. A sub-folder with its own CHANGELOG.md is a bundled
   package (a vendored dependency) and keeps its own version.
2. Copies the skill folder to the target (default ~/repos/<skill>), leaving out
   caches, build output and test state (__pycache__, node_modules, .wrangler,
   .kit-build, _site, state/, *.db).
   An existing target repo keeps its .git; its other files are replaced.
3. `git init` if needed, then runs the leak audit, the `audit()` from
   .agent/skills/sync-public/sync.py, over the whole export. Any finding stops
   the run before anything is committed.
4. Commits and tags `v<version>` locally.
5. Only with --push: `gh repo create <owner>/<name> --public` when the repo has
   no origin yet, then pushes the branch and the tag. Pushing publishes the
   code, so it runs only after explicit approval.
"""
import argparse
import fnmatch
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / '.agent' / 'skills'
SYNC_PY = SKILLS / 'sync-public' / 'sync.py'

# Caches, dependencies and test state never leave the private repo.
EXCLUDE = ['__pycache__', '*.pyc', '*.pyo', '.pytest_cache', 'node_modules', '.wrangler',
           'state', '.kit-build', '_site', '*.db', '*.db-wal', '*.db-shm', '*.sqlite', '.DS_Store', '*.log', '.git']

DEFAULT_GITIGNORE = """__pycache__/
*.pyc
node_modules/
.wrangler/
state/
*.db
*.db-wal
*.db-shm
*.log
.DS_Store
"""

VERSION_HEAD = re.compile(r'^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}', re.M)
VERSION_CONST = re.compile(r'''\bVERSION\s*=\s*['"](\d+\.\d+\.\d+)['"]''')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

def run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and r.returncode != 0:
        sys.exit(f'{" ".join(cmd)} failed ({r.returncode}):\n{r.stdout}{r.stderr}')
    return r

def load_audit():
    spec = importlib.util.spec_from_file_location('sync_public', SYNC_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.audit

IDENTITY_LABELS = {"bare 'owner'", "'owner arfi' (work identity)"}

def bundled_notice(dst, finding):
    """True for the copyright holder's name in a bundled package's LICENSE or NOTICE.

    The audit already skips LICENSE and NOTICE at the repo root, because naming
    the copyright holder is what those files are for. A bundled package (a
    sub-folder with its own CHANGELOG.md) must ship its own LICENSE and NOTICE
    under Apache-2.0, so the same holds there. Only the identity labels are
    exempt: a token, an id or a client name in those files still fails.
    """
    path, _line, label, _match = finding
    rel = Path(path)
    return (rel.name in ('LICENSE', 'NOTICE') and len(rel.parts) > 1
            and label in IDENTITY_LABELS
            and (dst / rel.parent / 'CHANGELOG.md').exists())

def excluded(name):
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDE)

def find_version(src):
    log = src / 'CHANGELOG.md'
    if not log.exists():
        sys.exit(f'{log} is missing: a published skill carries its changelog.')
    m = VERSION_HEAD.search(log.read_text(encoding='utf-8'))
    if not m:
        sys.exit(f'No released "## [x.y.z] - YYYY-MM-DD" section in {log}.')
    version = m.group(1)
    found = {'CHANGELOG.md': version}
    # A plain VERSION file at the root (one line, x.y.z) is a declaration too.
    vfile = src / 'VERSION'
    if vfile.exists():
        found['VERSION'] = vfile.read_text(encoding='utf-8').strip()
    for dirpath, dirnames, filenames in os.walk(src):
        # A sub-folder with its own CHANGELOG.md is a bundled package (for example
        # a vendored dependency) with its own version. It is not checked against
        # the root version.
        dirnames[:] = [d for d in dirnames if not excluded(d)
                       and not (Path(dirpath) / d / 'CHANGELOG.md').exists()]
        for f in filenames:
            path = Path(dirpath) / f
            rel = path.relative_to(src).as_posix()
            if f == 'package.json':
                v = json.loads(path.read_text(encoding='utf-8')).get('version')
                if v:
                    found[rel] = v
            elif path.suffix in ('.js', '.mjs', '.py', '.ts') and not rel.startswith('tests/'):
                for v in VERSION_CONST.findall(path.read_text(encoding='utf-8', errors='replace')):
                    found[rel] = v
    wrong = {k: v for k, v in found.items() if v != version}
    if wrong:
        sys.exit('Version declarations disagree with CHANGELOG.md (' + version + '): '
                 + ', '.join(f'{k}={v}' for k, v in wrong.items()))
    return version, found

def export(src, dst):
    if dst.exists():
        for item in dst.iterdir():
            if item.name == '.git':
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    else:
        dst.mkdir(parents=True)
    copied = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if not excluded(d)]
        rel_dir = Path(dirpath).relative_to(src)
        for f in filenames:
            if excluded(f):
                continue
            out = dst / rel_dir / f
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(dirpath) / f, out)
            copied += 1
    if not (dst / '.gitignore').exists():
        (dst / '.gitignore').write_text(DEFAULT_GITIGNORE, encoding='utf-8', newline='\n')
        copied += 1
    return copied

def main():
    ap = argparse.ArgumentParser(description='Export a skill folder as its own repo, audited and tagged.',
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('skill', help='skill folder name under .agent/skills/, or a path to one')
    ap.add_argument('--target', help='export directory (default: ~/repos/<skill>)')
    ap.add_argument('--name', help='repository name (default: the skill folder name)')
    ap.add_argument('--owner', default='you', help='GitHub owner for --push (default: you)')
    ap.add_argument('--force', action='store_true',
                    help='allow exporting into an existing folder that is not a git repo')
    ap.add_argument('--push', action='store_true',
                    help='create the public GitHub repo if needed and push branch and tag. Needs approval.')
    a = ap.parse_args()

    src = Path(a.skill) if os.sep in a.skill or '/' in a.skill else SKILLS / a.skill
    src = src.resolve()
    if not (src / 'SKILL.md').exists() and not (src / 'README.md').exists():
        sys.exit(f'{src} does not look like a skill folder (no SKILL.md or README.md).')
    name = a.name or src.name
    dst = Path(a.target).expanduser().resolve() if a.target else Path.home() / 'repos' / name
    if dst == src or src in dst.parents:
        sys.exit('The target must be outside the skill folder.')
    if dst.exists() and any(dst.iterdir()) and not (dst / '.git').exists() and not a.force:
        sys.exit(f'{dst} exists, is not empty and is not a git repo. Pass --force to replace its contents.')

    version, found = find_version(src)
    tag = f'v{version}'
    print(f'{name} {tag}  (version agrees in: {", ".join(sorted(found))})')

    n = export(src, dst)
    print(f'exported {n} files to {dst}')

    if not (dst / '.git').exists():
        run(['git', 'init', '-b', 'main'], dst)
    run(['git', 'config', 'core.autocrlf', 'false'], dst)

    findings = [f for f in load_audit()(dst) if not bundled_notice(dst, f)]
    if findings:
        print(f'LEAK AUDIT FAILED: {len(findings)} finding(s). Nothing committed.')
        for path, line, label, match in findings[:80]:
            print(f'  {Path(path).as_posix()}:{line}  [{label}]  {match!r}')
        return 2
    print('leak audit: clean (sync-public audit(), 0 findings)')

    run(['git', 'add', '-A'], dst)
    if run(['git', 'diff', '--cached', '--quiet'], dst, check=False).returncode != 0:
        run(['git', 'commit', '-m', f'{name} {tag}',
             '-m', 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'], dst)
        print(f'committed: {run(["git", "log", "-1", "--format=%h %s"], dst).stdout.strip()}')
    else:
        print('no changes since the last export')

    head = run(['git', 'rev-parse', 'HEAD'], dst).stdout.strip()
    existing = run(['git', 'rev-parse', '-q', '--verify', f'refs/tags/{tag}^{{commit}}'], dst, check=False)
    if existing.returncode != 0:
        run(['git', 'tag', '-a', tag, '-m', f'{name} {tag}'], dst)
        print(f'tagged {tag} at {head[:9]}')
    elif existing.stdout.strip() != head:
        print(f'WARNING: tag {tag} already points at {existing.stdout.strip()[:9]}, not HEAD {head[:9]}. '
              'Bump the version in CHANGELOG.md (and every VERSION) before publishing this change.')
        if a.push:
            return 2
    else:
        print(f'tag {tag} already at HEAD')

    if not a.push:
        print(f'\nLocal only. To publish, after approval:\n  python3 {Path(__file__).as_posix()} {a.skill} --push')
        return 0

    repo = f'{a.owner}/{name}'
    if run(['git', 'remote', 'get-url', 'origin'], dst, check=False).returncode != 0:
        desc = ''
        readme = dst / 'README.md'
        if readme.exists():
            lines = [ln.strip() for ln in readme.read_text(encoding='utf-8').splitlines()]
            desc = next((ln for ln in lines if ln and not ln.startswith(('#', '!', '<', '['))), '')[:300]
        run(['gh', 'repo', 'create', repo, '--public', '--source', str(dst), '--remote', 'origin',
             '--description', desc], dst)
        print(f'created https://github.com/{repo}')
    # gh may set an SSH remote, and Windows has no SSH key (30 Sep 2026: both
    # first pushes failed). HTTPS goes through gh's credential helper instead.
    run(['gh', 'auth', 'setup-git'], dst, check=False)
    run(['git', 'remote', 'set-url', 'origin', f'https://github.com/{repo}.git'], dst)
    run(['git', 'push', '-u', 'origin', 'main'], dst)
    run(['git', 'push', 'origin', tag], dst)
    print(f'pushed main and {tag} to https://github.com/{repo}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
