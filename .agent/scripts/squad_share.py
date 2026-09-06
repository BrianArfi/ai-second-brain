#!/usr/bin/env python3
"""
Share the owner-owned Drive files with a standing collaborator squad.

Why this exists: the writers default to visibility=domain, which is
yourcompany.com only. Every ExampleVendor engineer is external, so a spec written
for them is invisible to them, and the only way they find out is by asking. On
2 Sep 2026, 90 of the owner's 200 most recent owned docs were domain-only with zero
ExampleVendor grants, and Teammate had asked three times in two days.

Squads are defined in .agent/config/collaborator_squads.json, derived from who
already holds individual grants on the owner's docs.

Usage:
  python3 .agent/scripts/squad_share.py list
  python3 .agent/scripts/squad_share.py audit --squad examplevendor [--limit 200]
  python3 .agent/scripts/squad_share.py grant --squad examplevendor --file ID [--file ID ...] --approved

Granting exposes an internal document to people outside the company, so it is
approval-gated exactly like a Slack send. Without --approved this prints the
plan and changes nothing.
"""
import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CONFIG = os.path.join(BASE_DIR, '.agent', 'config', 'collaborator_squads.json')
CONNECTOR = os.path.join(BASE_DIR, '.agent', 'skills', 'work-drive-connector')

sys.path.insert(0, CONNECTOR)
os.chdir(CONNECTOR)  # token.json / credentials.json resolve relative to cwd

from googleapiclient.discovery import build  # type: ignore
from gdrive_manager import authenticate  # type: ignore

DOC_MIMES = (
    "mimeType='application/vnd.google-apps.document' or "
    "mimeType='application/vnd.google-apps.spreadsheet' or "
    "mimeType='application/vnd.google-apps.presentation'"
)

def load_squad(name):
    cfg = json.load(open(CONFIG))
    squads = cfg['squads']
    if name not in squads:
        sys.exit(f"Unknown squad {name!r}. Known: {', '.join(sorted(squads))}")
    return squads[name]

def service():
    return build('drive', 'v3', credentials=authenticate())

def owned_docs(svc, limit):
    out, token = [], None
    while len(out) < limit:
        r = svc.files().list(
            q=f"'me' in owners and trashed=false and ({DOC_MIMES})",
            fields='nextPageToken,files(id,name,modifiedTime)',
            pageSize=200, pageToken=token,
        ).execute()
        out += r.get('files', [])
        token = r.get('nextPageToken')
        if not token:
            break
    out.sort(key=lambda f: f['modifiedTime'], reverse=True)
    return out[:limit]

def emails_on(svc, file_id):
    perms = svc.permissions().list(
        fileId=file_id, fields='permissions(type,role,emailAddress,domain)',
    ).execute().get('permissions', [])
    return perms

def cmd_list(args):
    cfg = json.load(open(CONFIG))
    for name, s in cfg['squads'].items():
        print(f"\n{name}  ({s['label']})  default role: {s['role']}")
        for m in s['members']:
            print('  -', m)

def cmd_audit(args):
    squad = load_squad(args.squad)
    members = set(squad['members'])
    svc = service()
    files = owned_docs(svc, args.limit)
    missing = []
    for f in files:
        perms = emails_on(svc, f['id'])
        if any(p['type'] == 'anyone' for p in perms):
            continue  # anyone-with-link: the squad can already open it
        have = {(p.get('emailAddress') or '').lower() for p in perms}
        if not (members & have):
            missing.append(f)
    print(f"{len(missing)} of {len(files)} owned docs are closed to the "
          f"{args.squad} squad (no member grant, no anyone-with-link).\n")
    for f in missing:
        print(f"  {f['modifiedTime'][:10]}  {f['name'][:90]}  {f['id']}")
    return 1 if missing else 0

def cmd_grant(args):
    squad = load_squad(args.squad)
    role = args.role or squad['role']
    if not args.approved:
        print(f"DRY RUN. Would grant {role} on {len(args.file)} file(s) to "
              f"{len(squad['members'])} member(s) of {args.squad}:")
        for m in squad['members']:
            print('  -', m)
        print("\nRe-run with --approved once the owner has signed off. Granting "
              "exposes an internal document outside the company.")
        return 0
    svc = service()
    for fid in args.file:
        name = svc.files().get(fileId=fid, fields='name').execute()['name']
        have = {(p.get('emailAddress') or '').lower() for p in emails_on(svc, fid)}
        print(f"\n■ {name}")
        for m in squad['members']:
            if m in have:
                print('   = already', m)
                continue
            try:
                svc.permissions().create(
                    fileId=fid, body={'type': 'user', 'role': role, 'emailAddress': m},
                    sendNotificationEmail=False, fields='id',
                ).execute()
                print(f'   + granted {role}', m)
            except Exception as exc:
                print('   ! FAILED', m, exc)
    return 0

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    sub.add_parser('list')
    a = sub.add_parser('audit')
    a.add_argument('--squad', default='examplevendor')
    a.add_argument('--limit', type=int, default=200)
    g = sub.add_parser('grant')
    g.add_argument('--squad', default='examplevendor')
    g.add_argument('--file', action='append', required=True)
    g.add_argument('--role', choices=['reader', 'commenter', 'writer'])
    g.add_argument('--approved', action='store_true')
    args = ap.parse_args()
    if args.cmd == 'audit':
        sys.exit(cmd_audit(args))
    if args.cmd == 'grant':
        sys.exit(cmd_grant(args))
    cmd_list(args)

if __name__ == '__main__':
    main()
