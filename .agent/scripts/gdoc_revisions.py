#!/usr/bin/env python3
"""List and export Google Doc revision history.

Recovery tool for a Doc whose content was overwritten. `gdocs_writer update`
replaces the whole document, so an edit made in the Doc and not mirrored
locally is destroyed by the next push. Drive keeps revisions; this reads them.

    python3 .agent/scripts/gdoc_revisions.py <DOC_ID>              # list
    python3 .agent/scripts/gdoc_revisions.py <DOC_ID> --export DIR # export all as markdown

Note: only work-drive-connector/token.json has canReadRevisions. The other
Drive tokens silently return zero revisions, which reads as "no history" when
it actually means "no permission".
"""
import argparse, os, sys
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOKEN = os.path.join(BASE_DIR, '.agent', 'skills', 'work-drive-connector', 'token.json')
SCOPES = ['https://www.googleapis.com/auth/drive']

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('doc_id')
    ap.add_argument('--export', metavar='DIR', help='write every revision to DIR as markdown')
    ap.add_argument('--token', default=TOKEN)
    a = ap.parse_args()

    creds = Credentials.from_authorized_user_file(a.token, SCOPES)
    svc = build('drive', 'v3', credentials=creds)
    revs = svc.revisions().list(fileId=a.doc_id, fields='*', pageSize=1000).execute().get('revisions', [])

    if not revs:
        print("0 revisions. If that is a surprise, the token cannot read them: "
              "use work-drive-connector/token.json.", file=sys.stderr)
        return 1

    print(f"{len(revs)} revisions")
    for r in revs:
        who = (r.get('lastModifyingUser') or {}).get('displayName', '?')
        print(f"  {r['id']:>6}  {r.get('modifiedTime')}  {who}")

    if a.export:
        os.makedirs(a.export, exist_ok=True)
        for r in revs:
            link = (r.get('exportLinks') or {}).get('text/markdown')
            if not link:
                continue
            body = requests.get(link, headers={'Authorization': 'Bearer ' + creds.token}).text
            out = os.path.join(a.export, f"rev_{r['id']}.md")
            open(out, 'w').write(body)
            print(f"  wrote {out}  ({len(body)} bytes)")
    return 0

if __name__ == '__main__':
    sys.exit(main())
