#!/usr/bin/env python3
"""Move this harness, and everything it remembers, to another computer.

The repo is only part of the workspace. Three other stores hold real work, all
of them outside git and none of them synced by `git push`:

  1. Session transcripts -- ~/.claude/projects/<slug>/*.jsonl
     Every session ever run, and the only thing `claude --resume` reads. The
     <slug> is the session's working directory with every non-alphanumeric
     character turned into a dash, so the SAME repo checked out at a different
     path on another machine gets a DIFFERENT slug and finds no history at all.
     That is why a plain copy of ~/.claude does not survive the move.

  2. Auto-memory -- ~/.claude/projects/<slug>/memory/
     The durable facts, corrections and preferences the harness has learned.
     Losing it silently un-learns every lesson. `link-memory` moves it into the
     repo at journal/memory/ and symlinks it back, after which it travels with
     git like everything else and stops being machine-local at all.

  3. ASB desktop app state -- sessions.json (titles, models, cost), branches.json
     (the sub-session tree), workspace.json (which folder each workspace points
     at, as an absolute path that has to be rewritten on the target).

`check` also looks for the failure that motivated this script: sessions whose
working directory was not inside any git repo. On 10 and 11 Aug 2026 ten
sessions ran against the app's bundled template workspace instead of this
checkout, and wrote 21 real notes and inbox items into a folder that no push
could ever reach.

Usage:
    harness_migrate.py check                      # audit, writes nothing
    harness_migrate.py link-memory [--dry-run]    # memory -> repo, then symlink
    harness_migrate.py export  --out ~/Desktop [--no-transcripts]
    harness_migrate.py import  --bundle <file.tar.gz> [--repo <path>] [--dry-run]

Run `check` on both machines: once here before exporting, once there after
importing. The numbers should match.
"""
import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
HOME = os.path.expanduser('~')
CLAUDE_HOME = os.path.join(HOME, '.claude')
PROJECTS_DIR = os.path.join(CLAUDE_HOME, 'projects')
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Where the repo keeps the memory that used to live under ~/.claude.
REPO_MEMORY_REL = os.path.join('journal', 'memory')

# App state worth carrying. account.json (auth + install id) and whatsapp/ (a
# paired QR session) are deliberately absent: they are credentials bound to this
# machine, and copying them either breaks or leaks. runtime/ is a redownloadable
# binary payload.
ASB_STATE_FILES = ['sessions.json', 'branches.json', 'workspace.json', 'providers.json']
ASB_STATE_SKIP = {'account.json', 'whatsapp', 'runtime', 'workspace.json.v1.bak'}

ASB_DIR_CANDIDATES = [
    os.path.join(HOME, 'Library', 'Application Support', 'com.aisecondbrain.desktop'),
    os.path.join(HOME, '.config', 'com.aisecondbrain.desktop'),
    os.path.join(HOME, '.local', 'share', 'com.aisecondbrain.desktop'),
    os.path.join(os.environ.get('APPDATA', '/nonexistent'), 'com.aisecondbrain.desktop'),
]

# Regenerable caches and machine-local runtime under ~/.claude. Excluded from the
# bundle: together they are most of its 200 MB and none of it is work. 'sessions'
# is keyed by process id, so it describes this machine's running processes and
# means nothing on the target.
CLAUDE_SKIP_DIRS = {'shell-snapshots', 'plugins', 'cache', 'session-env',
                    'file-history', 'telemetry', 'downloads', 'ide', 'backups',
                    'sessions'}

def now_wib():
    return datetime.now(WIB).strftime('%Y-%m-%d %H:%M WIB')

def slugify_cwd(path):
    """Claude Code's project-directory name for a working directory.

    Verified against this machine: . becomes
    -Users-you-product-second-brain, and a path holding spaces and dots
    (.../com.aisecondbrain.desktop/workspace) becomes
    ...-com-aisecondbrain-desktop-workspace. So the rule is simply: every
    character that is not a letter or digit becomes a dash.
    """
    return re.sub(r'[^A-Za-z0-9]', '-', os.path.abspath(path))

def asb_dir():
    for c in ASB_DIR_CANDIDATES:
        if os.path.isdir(c):
            return c
    return None

def du(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total

def human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024.0

def transcript_cwd(project_path):
    """The working directory these sessions ran in, read out of a transcript.

    Authoritative, unlike un-slugifying the directory name: a dash in the slug
    could have been a dash, a slash, a space or a dot, so the mapping back is
    ambiguous and guessing it is how a restore lands in the wrong folder.
    """
    try:
        names = [n for n in os.listdir(project_path) if n.endswith('.jsonl')]
    except OSError:
        return None
    for name in sorted(names, key=lambda n: -os.path.getsize(os.path.join(project_path, n)))[:3]:
        try:
            with open(os.path.join(project_path, name), 'r', encoding='utf-8',
                      errors='replace') as fh:
                for _ in range(200):
                    line = fh.readline()
                    if not line:
                        break
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and rec.get('cwd'):
                        return rec['cwd']
        except OSError:
            continue
    return None

def is_git_repo(path):
    if not path or not os.path.isdir(path):
        return None                      # cannot tell: the path is gone
    p = subprocess.run(['git', '-C', path, 'rev-parse', '--show-toplevel'],
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else False

def survey_projects():
    """Every project directory under ~/.claude/projects, with what it points at."""
    out = []
    if not os.path.isdir(PROJECTS_DIR):
        return out
    for name in sorted(os.listdir(PROJECTS_DIR)):
        path = os.path.join(PROJECTS_DIR, name)
        if not os.path.isdir(path):
            continue
        try:
            sessions = len([n for n in os.listdir(path) if n.endswith('.jsonl')])
        except OSError:
            sessions = 0
        cwd = transcript_cwd(path)
        out.append({'slug': name, 'path': path, 'sessions': sessions,
                    'bytes': du(path), 'cwd': cwd, 'repo': is_git_repo(cwd)})
    return out

def repo_root(start=None):
    p = subprocess.run(['git', '-C', start or BASE_DIR, 'rev-parse', '--show-toplevel'],
                       capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else (start or BASE_DIR)

# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def cmd_check(args):
    root = repo_root()
    slug = slugify_cwd(root)
    mine = os.path.join(PROJECTS_DIR, slug)

    print(f'harness_migrate check -- {now_wib()}')
    print(f'  host      {socket.gethostname()} ({platform.system()})')
    print(f'  repo      {root}')
    print(f'  slug      {slug}')
    print()

    # 1. this repo's own session history
    print('SESSION HISTORY (claude --resume reads only this)')
    if os.path.isdir(mine):
        n = len([x for x in os.listdir(mine) if x.endswith('.jsonl')])
        print(f'  {n} session transcript(s), {human(du(mine))}')
        print(f'  {mine}')
    else:
        print(f'  MISSING: {mine}')
        print('  No session history for this checkout. If you just moved machines,')
        print('  the slug differs from the source and needs `import`.')
    print()

    # 2. memory
    print('AUTO-MEMORY (the harness\'s learned facts)')
    mem = os.path.join(mine, 'memory')
    repo_mem = os.path.join(root, REPO_MEMORY_REL)
    if os.path.islink(mem):
        target = os.path.realpath(mem)
        linked_here = os.path.realpath(repo_mem) == target
        print(f'  symlink -> {target}')
        if linked_here:
            tracked = subprocess.run(
                ['git', '-C', root, 'ls-files', REPO_MEMORY_REL],
                capture_output=True, text=True).stdout.strip().splitlines()
            print(f'  IN REPO, travels with git ({len(tracked)} file(s) tracked)')
        else:
            print('  WARNING: symlinked outside this repo; it will not sync.')
    elif os.path.isdir(mem):
        n = len([x for x in os.listdir(mem) if x.endswith('.md')])
        print(f'  {n} memory file(s), real directory, NOT in the repo')
        print(f'  {mem}')
        print('  These are lost on a machine move unless exported. Fix once:')
        print('    python3 .agent/scripts/harness_migrate.py link-memory')
    else:
        print('  none yet')
    print()

    # 3. stranded sessions: work that no push can reach
    all_projects = survey_projects()
    elsewhere = [p for p in all_projects if p['slug'] != slug and p['sessions'] > 0]

    print('STRANDED SESSIONS (cwd was not inside a git repo)')
    stranded = [p for p in elsewhere if p['repo'] is False]
    if not stranded:
        print('  none')
    else:
        for p in stranded:
            print(f'  {p["sessions"]:>3} session(s)  {human(p["bytes"]):>9}  {p["cwd"]}')
        print()
        print('  Sessions ran here, so anything they wrote sits outside version')
        print('  control and cannot reach the other machine. Check those folders')
        print('  for real work before wiping this computer.')
    print()

    # 4. sessions whose working directory has since been deleted. Worse than
    #    stranded: there is no folder left to go and look in, so whatever they
    #    wrote is already gone and only the transcript can say what it was.
    vanished = [p for p in elsewhere if p['repo'] is None]
    if vanished:
        print('SESSIONS WHOSE WORKING DIRECTORY NO LONGER EXISTS')
        for p in vanished:
            print(f'  {p["sessions"]:>3} session(s)  {human(p["bytes"]):>9}  {p["cwd"]}')
        print('  The transcripts survive and are worth carrying, but any file these')
        print('  sessions wrote is unrecoverable except from the transcript itself.')
        print()

    # 5. other repos seen on this machine, for completeness
    others = [p for p in elsewhere if p['repo']]
    if others:
        print('OTHER REPOS WITH SESSION HISTORY (their own git remotes carry them)')
        for p in others:
            print(f'  {p["sessions"]:>3} session(s)  {human(p["bytes"]):>9}  {p["repo"]}')
        print()

    # 5. ASB app state
    print('ASB DESKTOP APP STATE')
    ad = asb_dir()
    if not ad:
        print('  app support directory not found (app not installed here?)')
    else:
        print(f'  {ad}')
        for f in ASB_STATE_FILES:
            fp = os.path.join(ad, f)
            if os.path.exists(fp):
                extra = ''
                if f == 'sessions.json':
                    try:
                        extra = f' -- {len(json.load(open(fp)))} session record(s)'
                    except Exception:
                        pass
                if f == 'workspace.json':
                    try:
                        ws = json.load(open(fp)).get('workspaces', [])
                        extra = ' -- roots: ' + ', '.join(w.get('root', '?') for w in ws)
                    except Exception:
                        pass
                print(f'    {f:<16} {human(os.path.getsize(fp)):>9}{extra}')
            else:
                print(f'    {f:<16} missing')
        bundled = os.path.join(ad, 'workspace')
        if os.path.isdir(bundled):
            md = sum(1 for r, _d, fs in os.walk(bundled) for f in fs if f.endswith('.md'))
            print(f'  bundled template workspace: {md} markdown file(s), {human(du(bundled))}')
            print(f'    {bundled}')
            print('    Not a git repo. Anything written here is invisible to origin/main.')
    print()

    # 6. repo sync state
    print('REPO SYNC STATE (origin/main is the source of truth)')
    dirty = subprocess.run(['git', '-C', root, 'status', '--porcelain'],
                           capture_output=True, text=True).stdout.strip()
    n_dirty = len([l for l in dirty.splitlines() if l.strip()])
    ahead = subprocess.run(['git', '-C', root, 'rev-list', '--count', 'origin/main..HEAD'],
                           capture_output=True, text=True).stdout.strip() or '?'
    behind = subprocess.run(['git', '-C', root, 'rev-list', '--count', 'HEAD..origin/main'],
                            capture_output=True, text=True).stdout.strip() or '?'
    print(f'  {n_dirty} uncommitted path(s), {ahead} unpushed commit(s), {behind} behind origin')
    if n_dirty or (ahead not in ('0', '?')):
        print('  Push before you copy anything: python3 .agent/scripts/worktree_sync.py sync')
    return 0

# ---------------------------------------------------------------------------
# link-memory
# ---------------------------------------------------------------------------

def cmd_link_memory(args):
    root = repo_root()
    slug = slugify_cwd(root)
    mem = os.path.join(PROJECTS_DIR, slug, 'memory')
    repo_mem = os.path.join(root, REPO_MEMORY_REL)
    dry = args.dry_run

    def say(msg):
        print(('would ' if dry else '') + msg)

    if os.path.islink(mem):
        if os.path.realpath(mem) == os.path.realpath(repo_mem):
            print(f'already linked: {mem} -> {repo_mem}')
            return 0
        print(f'ERROR: {mem} is a symlink to {os.path.realpath(mem)}, not to the repo.')
        print('Remove it by hand first; refusing to guess which side is authoritative.')
        return 1

    if not dry:
        os.makedirs(os.path.dirname(mem), exist_ok=True)
        os.makedirs(repo_mem, exist_ok=True)

    moved = kept = 0
    if os.path.isdir(mem):
        for name in sorted(os.listdir(mem)):
            src, dst = os.path.join(mem, name), os.path.join(repo_mem, name)
            if os.path.isdir(src):
                continue
            if os.path.exists(dst):
                # Both sides have it. Keep the repo's copy authoritative and park
                # this machine's under a suffix rather than overwriting either.
                if open(src, 'rb').read() == open(dst, 'rb').read():
                    kept += 1
                    continue
                alt = dst + f'.from-{socket.gethostname().split(".")[0]}'
                say(f'park differing copy: {name} -> {os.path.basename(alt)}')
                if not dry:
                    shutil.copy2(src, alt)
                kept += 1
                continue
            say(f'move {name} into {REPO_MEMORY_REL}/')
            if not dry:
                shutil.copy2(src, dst)
            moved += 1
        if not dry:
            backup = mem + '.pre-link'
            shutil.move(mem, backup)
            print(f'original directory kept at {backup} until you are satisfied')

    if not dry:
        os.symlink(repo_mem, mem)
    say(f'symlink {mem} -> {repo_mem}')
    print()
    print(f'{moved} file(s) moved, {kept} already present.')
    if not dry:
        print('Memory now lives in the repo and syncs with git. On every OTHER')
        print('machine, run this same command once so its symlink points here too.')
    return 0

# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def cmd_export(args):
    root = repo_root()
    slug = slugify_cwd(root)
    stamp = datetime.now(WIB).strftime('%Y-%m-%d-%H%M')
    outdir = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(outdir, exist_ok=True)
    bundle = os.path.join(outdir, f'asb-transfer-{stamp}.tar.gz')

    projects = survey_projects()
    # This repo's history, plus every session that no git remote carries: the
    # stranded ones (cwd outside any repo) and the vanished ones (cwd deleted
    # since). Sessions belonging to another repo are left out, because that
    # repo's own remote already moves them.
    include = [p for p in projects
               if p['slug'] == slug
               or (p['sessions'] > 0 and p['repo'] in (False, None))]

    manifest = {
        'version': 1,
        'created': now_wib(),
        'host': socket.gethostname(),
        'platform': platform.system(),
        'source_repo': root,
        'source_slug': slug,
        'transcripts_included': not args.no_transcripts,
        'projects': [{'slug': p['slug'], 'cwd': p['cwd'], 'sessions': p['sessions'],
                      'stranded': p['repo'] is False,
                      'cwd_missing': p['repo'] is None} for p in include],
        'asb_dir': asb_dir(),
        'memory_in_repo': os.path.islink(os.path.join(PROJECTS_DIR, slug, 'memory')),
        'notes': [
            'Restore with: harness_migrate.py import --bundle <this file> --repo <target repo path>',
            'account.json and whatsapp/ are intentionally excluded: machine-bound credentials.',
            'Sign in to the ASB app on the target, and re-pair WhatsApp there if used.',
            'Auto-memory is NOT in this bundle when memory_in_repo is true: it lives at '
            'journal/memory/ and arrives with git pull. Run link-memory on the target.',
        ],
    }

    added = []

    def add(src, arc):
        if not os.path.exists(src):
            return
        tf.add(src, arcname=arc, filter=_filter)
        added.append(arc)

    def _filter(info):
        base = os.path.basename(info.name)
        parts = set(info.name.split('/'))
        if base in ASB_STATE_SKIP or parts & ASB_STATE_SKIP:
            return None
        if parts & CLAUDE_SKIP_DIRS:
            return None
        if base in ('.DS_Store', '.lock'):
            return None
        # `memory` is a symlink into the repo once link-memory has run, so git
        # already carries its contents. Archiving the link would restore a
        # dangling pointer at the source machine's repo path, which is exactly
        # the absolute-path breakage this tool exists to avoid.
        if base == 'memory' and info.issym():
            return None
        return info

    print(f'writing {bundle}')
    with tarfile.open(bundle, 'w:gz') as tf:
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as mf:
            json.dump(manifest, mf, indent=1)
            mpath = mf.name
        tf.add(mpath, arcname='manifest.json')
        os.unlink(mpath)

        for p in include:
            if args.no_transcripts:
                # memory only: the smallest bundle that still keeps the lessons.
                # Skipped when it is already a repo symlink, because then git is
                # carrying it and the bundle would only add a broken link.
                m = os.path.join(p['path'], 'memory')
                if os.path.exists(m) and not os.path.islink(m):
                    add(m, f'claude/projects/{p["slug"]}/memory')
            else:
                add(p['path'], f'claude/projects/{p["slug"]}')
            tag = ''
            if p['repo'] is False:
                tag = '  [STRANDED]'
            elif p['repo'] is None:
                tag = '  [CWD GONE]'
            print(f'  + {p["slug"]} ({p["sessions"]} session(s)){tag}')

        for f in ('settings.json', 'settings.local.json'):
            add(os.path.join(CLAUDE_HOME, f), f'claude/{f}')
        for d in ('plans', 'tasks', 'sessions'):
            add(os.path.join(CLAUDE_HOME, d), f'claude/{d}')

        ad = asb_dir()
        if ad:
            for f in ASB_STATE_FILES:
                add(os.path.join(ad, f), f'asb/{f}')
            bundled = os.path.join(ad, 'workspace')
            if os.path.isdir(bundled):
                add(bundled, 'asb/workspace')
                print('  + ASB bundled workspace (holds work written outside any repo)')

    size = os.path.getsize(bundle)
    print()
    print(f'done: {bundle} ({human(size)})')
    print(f'{len(added)} tree(s) included. On the target machine:')
    print(f'  python3 .agent/scripts/harness_migrate.py import --bundle {os.path.basename(bundle)} \\')
    print('      --repo /path/to/product-second-brain')
    return 0

# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def rewrite_jsonl(path, old, new):
    """Replace the source repo root with the target's, line by line.

    Transcripts record an absolute `cwd` on every entry. Resume works without
    this, but every path the history quotes back would point at a folder that
    does not exist on the target, which makes the transcript actively misleading
    when a later session reads it.
    """
    if old == new:
        return 0
    hits = 0
    tmp = path + '.rewrite'
    with open(path, 'r', encoding='utf-8', errors='replace') as src, \
         open(tmp, 'w', encoding='utf-8') as dst:
        for line in src:
            if old in line:
                line = line.replace(old, new)
                hits += 1
            dst.write(line)
    os.replace(tmp, path)
    return hits

def restore_asb_state(staging, bundle, ad, src_repo, target_repo, dry):
    """Put sessions.json, branches.json, workspace.json and providers.json back,
    with every workspace root repointed from the source path to this machine's.

    Anything replaced is copied to <name>.pre-import first.
    """
    s_asb = os.path.join(staging, 'asb')
    if not os.path.isdir(s_asb):
        return

    if not ad:
        # Beside the bundle: a path that by definition exists and that the caller
        # can write. The target repo may not be cloned yet, and _backups/ inside
        # it is not guaranteed writable (on macOS, a Linux target path is not).
        keep = os.path.join(os.path.dirname(bundle), 'asb-state-from-bundle')
        print(f'  ASB app not installed here; app state left at {keep}')
        print('  Move it into the app support directory once the app is set up.')
        if not dry:
            shutil.copytree(s_asb, keep, dirs_exist_ok=True)
        return

    for f in ASB_STATE_FILES:
        s = os.path.join(s_asb, f)
        if not os.path.exists(s):
            continue
        d = os.path.join(ad, f)
        if f == 'workspace.json':
            try:
                with open(s) as fh:
                    ws = json.load(fh)
                changed = 0
                for w in ws.get('workspaces', []):
                    if w.get('root') == src_repo:
                        w['root'] = target_repo
                        changed += 1
                print(f'  asb/workspace.json -> {d} ({changed} root(s) repointed)')
                if not dry:
                    if os.path.exists(d):
                        shutil.copy2(d, d + '.pre-import')
                    with open(d, 'w') as fh:
                        json.dump(ws, fh, indent=2)
                continue
            except Exception as exc:
                print(f'  WARNING: could not rewrite workspace.json ({exc})')
        print(f'  asb/{f} -> {d}')
        if not dry:
            if os.path.exists(d):
                shutil.copy2(d, d + '.pre-import')
            shutil.copy2(s, d)

    bundled = os.path.join(s_asb, 'workspace')
    if os.path.isdir(bundled):
        d = os.path.join(ad, 'workspace')
        print(f'  asb/workspace -> {d}')
        if not dry:
            shutil.copytree(bundled, d, dirs_exist_ok=True)

def cmd_import(args):
    bundle = os.path.abspath(os.path.expanduser(args.bundle))
    if not os.path.isfile(bundle):
        print(f'ERROR: no such bundle: {bundle}')
        return 1
    target_repo = os.path.abspath(os.path.expanduser(args.repo)) if args.repo else repo_root()
    dry = args.dry_run

    with tarfile.open(bundle, 'r:gz') as tf:
        try:
            manifest = json.loads(tf.extractfile('manifest.json').read().decode())
        except Exception as exc:
            print(f'ERROR: bundle has no readable manifest.json ({exc})')
            return 1

    src_repo = manifest['source_repo']
    src_slug = manifest['source_slug']
    dst_slug = slugify_cwd(target_repo)

    print(f'importing {os.path.basename(bundle)}')
    print(f'  created   {manifest["created"]} on {manifest["host"]} ({manifest["platform"]})')
    print(f'  source    {src_repo}')
    print(f'            slug {src_slug}')
    print(f'  target    {target_repo}')
    print(f'            slug {dst_slug}')
    if src_slug != dst_slug:
        print('  paths differ, so the project slug is remapped on restore. This is the')
        print('  step a plain copy of ~/.claude skips, and why it finds no history.')
    if not os.path.isdir(target_repo):
        print()
        print(f'  WARNING: {target_repo} does not exist on this machine.')
        print('  The slug is derived from that exact path, so if it is wrong, the restored')
        print('  history will sit under a name no session ever looks up. Clone the repo')
        print('  first and pass its real path to --repo.')
    print()
    if dry:
        print('DRY RUN: nothing is written.')

    staging = tempfile.mkdtemp(prefix='asb-import-')
    try:
        with tarfile.open(bundle, 'r:gz') as tf:
            tf.extractall(staging)

        # 1. project directories, with the slug remapped
        src_projects = os.path.join(staging, 'claude', 'projects')
        restored = 0
        if os.path.isdir(src_projects):
            for name in sorted(os.listdir(src_projects)):
                arc = os.path.join(src_projects, name)
                out_slug = dst_slug if name == src_slug else name
                dest = os.path.join(PROJECTS_DIR, out_slug)
                n = len([x for x in os.listdir(arc) if x.endswith('.jsonl')]) \
                    if os.path.isdir(arc) else 0
                label = f'{name} -> {out_slug}' if out_slug != name else name
                print(f'  projects: {label} ({n} transcript(s))')
                if dry:
                    continue
                os.makedirs(dest, exist_ok=True)
                for item in os.listdir(arc):
                    s, d = os.path.join(arc, item), os.path.join(dest, item)
                    if os.path.isdir(s):
                        if item == 'memory' and os.path.islink(d):
                            print('    memory is already a repo symlink here; left alone')
                            continue
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        if os.path.exists(d) and not args.force:
                            continue
                        shutil.copy2(s, d)
                        if item.endswith('.jsonl') and not args.no_rewrite_paths:
                            rewrite_jsonl(d, src_repo, target_repo)
                        restored += 1
        if not dry:
            print(f'  restored {restored} transcript file(s)')

        # 2. user-level claude files, only when absent: the target's own
        #    settings are likelier to be right for the target.
        for rel in ('settings.json', 'settings.local.json'):
            s = os.path.join(staging, 'claude', rel)
            d = os.path.join(CLAUDE_HOME, rel)
            if os.path.exists(s) and (not os.path.exists(d) or args.force):
                print(f'  claude/{rel} -> {d}')
                if not dry:
                    shutil.copy2(s, d)
            elif os.path.exists(s):
                print(f'  claude/{rel} kept as-is on this machine (use --force to overwrite)')

        # 3. ASB app state, with workspace roots repointed.
        #
        # Guarded whole: the transcripts restored above are the irreplaceable
        # part, and app state is re-derivable by opening the app. An exception
        # here used to abort the run AFTER the transcripts had landed, printing a
        # traceback that made a successful import look like a failed one.
        try:
            restore_asb_state(staging, bundle, args.asb_dir or asb_dir(),
                              src_repo, target_repo, dry)
        except Exception as exc:
            print(f'  WARNING: ASB app state not restored ({exc}).')
            print('  The session transcripts above are in place; this part is only')
            print('  window titles and the branch tree. Re-run with --asb-dir <path>.')
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print()
    print('Next, on this machine:')
    print('  1. git pull   (the repo carries journal/memory/, the ledgers and every doc)')
    print('  2. python3 .agent/scripts/harness_migrate.py link-memory')
    print('  3. python3 .agent/scripts/harness_migrate.py check   (numbers should match the source)')
    print('  4. Sign in to the ASB app. Re-pair WhatsApp if you use the bridge.')
    print('  5. Copy the credential files .gitignore keeps out of the repo (.env, token_*.json,')
    print('     credentials.json) by hand, over a channel you trust.')
    return 0

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')

    sub.add_parser('check', help='audit what lives outside the repo')

    p_link = sub.add_parser('link-memory', help='move auto-memory into the repo, symlink back')
    p_link.add_argument('--dry-run', action='store_true')

    p_exp = sub.add_parser('export', help='write a transfer bundle')
    p_exp.add_argument('--out', default='.', help='directory to write the bundle into')
    p_exp.add_argument('--no-transcripts', action='store_true',
                       help='memory + app state only, no session history')

    p_imp = sub.add_parser('import', help='restore a transfer bundle onto this machine')
    p_imp.add_argument('--bundle', required=True)
    p_imp.add_argument('--repo', default=None, help='target repo path (default: this one)')
    p_imp.add_argument('--asb-dir', default=None)
    p_imp.add_argument('--dry-run', action='store_true')
    p_imp.add_argument('--force', action='store_true', help='overwrite files already present')
    p_imp.add_argument('--no-rewrite-paths', action='store_true',
                       help='leave the old absolute paths inside transcripts')

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 0
    return {'check': cmd_check, 'link-memory': cmd_link_memory,
            'export': cmd_export, 'import': cmd_import}[args.cmd](args)

if __name__ == '__main__':
    sys.exit(main())
