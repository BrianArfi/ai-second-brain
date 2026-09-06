#!/usr/bin/env python3
"""
Google Docs Creator - Upload markdown/HTML as real editable Google Docs.

Supports accounts: work, personal (you@example.com), secondary
Auto-refreshes OAuth tokens.

Commands:
  create-doc  Convert markdown to proper Google Doc (HTML → GDoc via Drive API)
  upload      Upload any file to Google Drive

Usage:
  python3 gdocs_create.py create-doc --title "My Doc" --file content.md --account work
  python3 gdocs_create.py create-doc --title "My Doc" --content "# Hello" --account personal
  python3 gdocs_create.py create-doc --title "My Doc" --file content.md --account secondary
  python3 gdocs_create.py upload --title "Report.pdf" --file report.pdf --account work --parent-id FOLDER_ID
"""

import os
import sys
import re
import io
import argparse
import signal
import mimetypes

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.join(SKILL_DIR, '..', '..', '..')

sys.path.insert(0, os.path.join(REPO_ROOT, '.agent', 'scripts'))
from file_utils import assert_drive_result  # Drive Operation Verification (CLAUDE.md)
from file_utils import apply_visibility, add_visibility_arg, resolve_visibility  # sharing (CLAUDE.md LANDMINE)

ACCOUNTS = {
    'work':    os.path.join(REPO_ROOT, '.agent/skills/work-drive-connector'),
    'personal': os.path.join(REPO_ROOT, '.agent/skills/personal-drive-connector'),
    'secondary': os.path.join(REPO_ROOT, '.agent/skills/secondary-drive-connector'),
}

DEFAULT_PARENT_IDS = {
    'work':    None,
    'personal': None,
    'secondary': None,
}

SCOPES = ['https://www.googleapis.com/auth/drive']

def timeout_handler(signum, frame):
    print("[ERROR] Timed out after 180 seconds", file=sys.stderr)
    sys.exit(1)

if os.name != 'nt':
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)

def authenticate(account: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if account not in ACCOUNTS:
        print(f"[ERROR] Unknown account '{account}'. Choose from: {list(ACCOUNTS.keys())}")
        sys.exit(1)

    token_path = os.path.join(ACCOUNTS[account], 'token.json')
    if not os.path.exists(token_path):
        print(f"[ERROR] Token not found: {token_path}")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, 'w') as f:
                f.write(creds.to_json())
            print(f"[INFO] Token refreshed for '{account}'. Expiry: {creds.expiry}")
        else:
            print(f"[ERROR] Token invalid and cannot be refreshed for '{account}'. Re-auth needed.")
            sys.exit(1)
    return creds

def md_to_html(md_text: str, title: str = '') -> str:
    css = """
body{font-family:Calibri,Arial,sans-serif;max-width:900px;margin:40px auto;font-size:12pt;color:#222;line-height:1.5}
h1{font-size:19pt;margin-top:24pt;color:#1a1a1a}
h2{font-size:15pt;margin-top:16pt;margin-bottom:4pt;color:#1a1a1a}
h3{font-size:13pt;margin-top:12pt;margin-bottom:4pt;color:#1a1a1a}
h4{font-size:12pt;margin-top:10pt;margin-bottom:2pt;color:#333}
table{border-collapse:collapse;width:100%;margin:10px 0}
th,td{border:1px solid #ccc;padding:6px 10px;text-align:left}
th{background:#f0f0f0;font-weight:bold}
ul,ol{margin:6px 0;padding-left:24px}
li{margin:2px 0}
pre,code{background:#f5f5f5;font-family:monospace;font-size:11pt}
pre{border-left:3px solid #bbb;padding:8px 12px;white-space:pre-wrap}
code{padding:1px 4px;border-radius:2px}
blockquote{border-left:3px solid #bbb;margin:8px 0;padding:4px 16px;color:#555;font-style:italic}
p{margin:4px 0 8px 0}
strong{font-weight:bold}
em{font-style:italic}
"""
    def convert(md):
        lines = md.split('\n')
        out = []
        i = 0
        in_table = False
        table_buf = []

        def flush_table():
            if not table_buf:
                return ''
            rows = []
            header_done = False
            for row in table_buf:
                if re.match(r'^\|[\s\-\|:]+\|$', row.strip()):
                    header_done = True
                    continue
                cells = [c.strip() for c in row.strip().strip('|').split('|')]
                tag = 'th' if not header_done else 'td'
                row_html = ''.join(f'<{tag}>{inline(c)}</{tag}>' for c in cells)
                rows.append(f'<tr>{row_html}</tr>')
            return '<table>' + ''.join(rows) + '</table>'

        def inline(text):
            # Bold
            text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
            # Italic
            text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
            # Inline code
            text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
            # Links
            text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'<a href="\2">\1</a>', text)
            return text

        while i < len(lines):
            line = lines[i]

            # Table detection
            if line.strip().startswith('|') and line.strip().endswith('|'):
                if not in_table:
                    in_table = True
                    table_buf = []
                table_buf.append(line)
                i += 1
                continue
            elif in_table:
                out.append(flush_table())
                table_buf = []
                in_table = False

            # Headings
            m = re.match(r'^(#{1,4})\s+(.*)', line)
            if m:
                level = len(m.group(1))
                out.append(f'<h{level}>{inline(m.group(2))}</h{level}>')
                i += 1
                continue

            # HR — skip horizontal rules (--- / *** / ___) to avoid visual separators in Google Docs
            if re.match(r'^[-*_]{3,}\s*$', line.strip()):
                out.append('')
                i += 1
                continue

            # Unordered list
            if re.match(r'^[\s]*[-*+]\s+', line):
                out.append('<ul>')
                while i < len(lines) and re.match(r'^[\s]*[-*+]\s+', lines[i]):
                    item = re.sub(r'^[\s]*[-*+]\s+', '', lines[i])
                    out.append(f'<li><p>{inline(item)}</p></li>')
                    i += 1
                out.append('</ul>')
                continue

            # Ordered list
            if re.match(r'^\s*\d+\.\s+', line):
                out.append('<ol>')
                while i < len(lines) and re.match(r'^\s*\d+\.\s+', lines[i]):
                    item = re.sub(r'^\s*\d+\.\s+', '', lines[i])
                    out.append(f'<li><p>{inline(item)}</p></li>')
                    i += 1
                out.append('</ol>')
                continue

            # Blockquote
            if line.startswith('>'):
                out.append(f'<blockquote>{inline(line[1:].strip())}</blockquote>')
                i += 1
                continue

            # Code block
            if line.startswith('```'):
                out.append('<pre>')
                i += 1
                while i < len(lines) and not lines[i].startswith('```'):
                    out.append(lines[i].replace('<', '&lt;').replace('>', '&gt;'))
                    i += 1
                out.append('</pre>')
                i += 1
                continue

            # Empty line
            if line.strip() == '':
                out.append('')
                i += 1
                continue

            # Paragraph — group consecutive non-special lines into one <p> with <br>
            # This matches standard markdown: a single newline doesn't break paragraphs
            para_lines = []
            while i < len(lines):
                l = lines[i]
                if (l.strip() == ''
                        or (l.strip().startswith('|') and l.strip().endswith('|'))
                        or re.match(r'^#{1,4}\s+', l)
                        or re.match(r'^[-*_]{3,}\s*$', l.strip())
                        or re.match(r'^[\s]*[-*+]\s+', l)
                        or re.match(r'^\s*\d+\.\s+', l)
                        or l.startswith('>')
                        or l.startswith('```')):
                    break
                para_lines.append(inline(l))
                i += 1
            if para_lines:
                out.append(f'<p>{"<br>".join(para_lines)}</p>')

        if in_table:
            out.append(flush_table())

        return '\n'.join(out)

    body = convert(md_text)
    title_tag = f'<title>{title}</title>' if title else ''
    return f'<!DOCTYPE html><html><head><meta charset="utf-8">{title_tag}<style>{css}</style></head><body>{body}</body></html>'

def create_doc(args):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    creds = authenticate(args.account)
    service = build('drive', 'v3', credentials=creds)

    # Get content
    if args.file:
        with open(args.file, 'r', encoding='utf-8') as f:
            raw = f.read()
    elif args.content:
        raw = args.content
    else:
        raw = sys.stdin.read()

    # Convert MD to HTML if input looks like markdown
    if args.html:
        html = raw
    else:
        html = md_to_html(raw, title=args.title)

    # Use explicit parent-id if given, else fall back to account default (may be None)
    parent_id = args.parent_id if args.parent_id is not None else DEFAULT_PARENT_IDS.get(args.account)

    metadata = {
        'name': args.title,
        'mimeType': 'application/vnd.google-apps.document',
    }
    if parent_id:
        metadata['parents'] = [parent_id]

    media = MediaIoBaseUpload(
        io.BytesIO(html.encode('utf-8')),
        mimetype='text/html',
        resumable=False
    )

    file = service.files().create(
        body=metadata,
        media_body=media,
        fields='id,name,webViewLink'
    ).execute()
    assert_drive_result(file, 'gdocs_create create-doc')

    # Sharing defaults to the work domain, never to anyone-with-link. This used
    # to TRY anyone-with-link first and only fall back to the domain if that
    # call failed, which meant the safe outcome depended on an API error.
    # Set WORK_DOMAIN (env) to YOUR Google Workspace domain.
    work_domain = os.environ.get('WORK_DOMAIN', 'yourcompany.com')
    apply_visibility(service, file['id'], resolve_visibility(args), domain=work_domain)

    # Column widths and pageless are part of creating the doc, not a step a
    # caller has to remember. The manual pass was skipped often enough that
    # table-heavy docs kept reaching the owner at the cramped ~468pt default.
    if not getattr(args, 'no_format_pass', False):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from format_pass import auto_format
        auto_format(file['id'], args.account, label='create-doc')

    print(f"[OK] Created Google Doc: {file['name']}")
    print(f"     ID: {file['id']}")
    print(f"     URL: {file.get('webViewLink', 'https://docs.google.com/document/d/' + file['id'] + '/edit')}")
    return file

def upload_file(args):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = authenticate(args.account)
    service = build('drive', 'v3', credentials=creds)

    if not args.file or not os.path.exists(args.file):
        print(f"[ERROR] File not found: {args.file}")
        sys.exit(1)

    mime_type = args.mime_type or mimetypes.guess_type(args.file)[0] or 'application/octet-stream'
    title = args.title or os.path.basename(args.file)
    parent_id = args.parent_id or DEFAULT_PARENT_IDS.get(args.account)

    metadata = {'name': title}
    if parent_id:
        metadata['parents'] = [parent_id]

    media = MediaFileUpload(args.file, mimetype=mime_type, resumable=True)

    file = service.files().create(
        body=metadata,
        media_body=media,
        fields='id,name,webViewLink,mimeType'
    ).execute()
    assert_drive_result(file, 'gdocs_create upload')

    print(f"[OK] Uploaded: {file['name']} ({file['mimeType']})")
    print(f"     ID: {file['id']}")
    print(f"     URL: {file.get('webViewLink', 'https://drive.google.com/file/d/' + file['id'] + '/view')}")
    return file

def main():
    parser = argparse.ArgumentParser(description='Google Docs Creator')
    sub = parser.add_subparsers(dest='command')

    # create-doc
    p_doc = sub.add_parser('create-doc', help='Create Google Doc from markdown or HTML')
    p_doc.add_argument('--title', required=True, help='Document title')
    p_doc.add_argument('--file', help='Path to markdown or HTML file')
    p_doc.add_argument('--content', help='Inline markdown content string')
    p_doc.add_argument('--account', default='work', choices=list(ACCOUNTS.keys()), help='Drive account to use')
    p_doc.add_argument('--parent-id', help='Drive folder ID (defaults to My Drive root)')
    p_doc.add_argument('--html', action='store_true', help='Input is already HTML (skip MD conversion)')
    p_doc.add_argument('--no-format-pass', dest='no_format_pass', action='store_true',
                       help='Skip the automatic pageless + column-width pass (it runs by default)')
    add_visibility_arg(p_doc)

    # upload
    p_up = sub.add_parser('upload', help='Upload any file to Google Drive')
    p_up.add_argument('--file', required=True, help='Path to file to upload')
    p_up.add_argument('--title', help='Title in Drive (defaults to filename)')
    p_up.add_argument('--account', default='work', choices=list(ACCOUNTS.keys()), help='Drive account to use')
    p_up.add_argument('--parent-id', help='Drive folder ID')
    p_up.add_argument('--mime-type', help='MIME type override (auto-detected if omitted)')

    args = parser.parse_args()

    if args.command == 'create-doc':
        create_doc(args)
    elif args.command == 'upload':
        upload_file(args)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == '__main__':
    main()
