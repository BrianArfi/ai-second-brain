#!/usr/bin/env python3
"""Read a Google Drive file by ID using the WORK-account token (API, not MCP).

  python3 work_drive_read.py --id <FILE_ID>

Exports Google Docs/Sheets/Slides to plain text; downloads and prints text for
other readable types. Requires token_drive_work.json (run work_drive_auth.py
first). This is what lets us read Work-domain / ExampleVendor-shared docs the personal
MCP identity can't see."""
import os, sys, argparse, io
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
# The Work token this skill actually holds is `token.json`: it is the same file
# `gdocs_create.py --account work` authenticates with (its ACCOUNTS map points at
# this skill directory), so both writers and this reader stay on one credential and
# one refresh. `token_drive_work.json` is the older name `work_drive_auth.py`
# writes, kept as a fallback so an existing auth run is not orphaned.
TOKEN_FILE = next(
    (p for p in (os.path.join(SKILL_DIR, 'token.json'),
                 os.path.join(SKILL_DIR, 'token_drive_work.json'))
     if os.path.exists(p)),
    os.path.join(SKILL_DIR, 'token_drive_work.json'),
)
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

EXPORT = {
    'application/vnd.google-apps.document': 'text/plain',
    'application/vnd.google-apps.spreadsheet': 'text/csv',
    'application/vnd.google-apps.presentation': 'text/plain',
}

def _service():
    if not os.path.exists(TOKEN_FILE):
        sys.exit(f"No Work Drive token. Run: python3 work_drive_auth.py auth-url  (then auth-save --code ...)")
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, 'w') as t:
            t.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

def read(file_id):
    svc = _service()
    meta = svc.files().get(fileId=file_id, fields='name,mimeType', supportsAllDrives=True).execute()
    mime = meta['mimeType']
    sys.stderr.write(f"[{meta['name']}] ({mime})\n")
    if mime in EXPORT:
        data = svc.files().export(fileId=file_id, mimeType=EXPORT[mime]).execute()
        sys.stdout.write(data.decode('utf-8', 'replace') if isinstance(data, bytes) else data)
    else:
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=file_id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = dl.next_chunk()
        sys.stdout.buffer.write(buf.getvalue())

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--id', required=True)
    a = p.parse_args()
    read(a.id)
