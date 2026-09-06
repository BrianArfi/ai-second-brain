#!/usr/bin/env python3
"""
Secondary Google Drive Manager
Provides upload, search, and read for Secondary's Google Drive.
Uses credentials specific to Secondary's Drive.
"""
import os
import sys
import argparse
import mimetypes
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import io
import signal

# Global timeout: 180 seconds
ACCOUNT_NAME = 'secondary'

def _format_pass(file_id, file_metadata, label):
    """Pageless + content-aware column widths, on any convert that produced a Doc.

    The pass used to be a manual step after `--convert`, and a skipped step is
    why table-heavy docs kept landing at the cramped default width. It runs only
    for Google Docs (a Sheet has no table columns to widen) and never raises.
    """
    if file_metadata.get('mimeType') != 'application/vnd.google-apps.document':
        return
    if os.environ.get('GDOC_FORMAT_PASS_DISABLE') == '1':
        return
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, '.agent', 'skills', 'gdocs-create'))
        from format_pass import auto_format
        auto_format(file_id, ACCOUNT_NAME, label=label)
    except Exception as e:
        print(f"[format_pass] skipped ({type(e).__name__}: {e})")

def timeout_handler(signum, frame):
    print("[ERROR] Google Drive Manager timed out after 180 seconds", file=sys.stderr)
    sys.exit(1)

if os.name != 'nt': # signal.alarm is Unix-only
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)
# Credentials are stored in the same directory as this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, 'credentials.json')
TOKEN_FILE = os.path.join(SCRIPT_DIR, 'token.json')

REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, '.agent', 'scripts'))
from file_utils import assert_drive_result  # Drive Operation Verification (CLAUDE.md)

# Full drive access for Secondary's drive
SCOPES = ['https://www.googleapis.com/auth/drive']

def authenticate():
    """Authenticate and return credentials."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    else:
        # Fallback to base directory token if not found locally
        BASE_TOKEN = os.path.join(SCRIPT_DIR, '..', '..', '..', 'token.json')
        if os.path.exists(BASE_TOKEN):
            creds = Credentials.from_authorized_user_file(BASE_TOKEN, SCOPES)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                # Fallback to base directory credentials if not found here
                BASE_CREDENTIALS = os.path.join(SCRIPT_DIR, '..', '..', '..', 'credentials.json')
                if os.path.exists(BASE_CREDENTIALS):
                    CREDENTIALS_FILE_USED = BASE_CREDENTIALS
                else:
                    print(f"Error: credentials.json not found in {SCRIPT_DIR} or base directory.")
                    return None
            else:
                CREDENTIALS_FILE_USED = CREDENTIALS_FILE
            
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE_USED, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save credentials to local token file
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return creds

def upload_file(file_path, folder_id=None, convert_to_docs=False, share=False):
    """Upload a file to Secondary's Google Drive."""
    creds = authenticate()
    if not creds:
        return None

    service = build('drive', 'v3', credentials=creds)

    file_name = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = 'application/octet-stream'

    file_metadata = {'name': file_name}
    if folder_id:
        file_metadata['parents'] = [folder_id]

    import tempfile
    from datetime import datetime
    
    # User requested default accessibility to everyone can comment
    share = True

    if convert_to_docs and file_path.endswith('.md'):
        try:
            import markdown
            import re
            with open(file_path, 'r', encoding='utf-8') as f:
                md_content = f.read()

            title_match = re.search(r'(?m)^#\s+(.*)', md_content)
            if title_match:
                clean_title = title_match.group(1).strip()
            else:
                base_title = os.path.splitext(file_name)[0]
                clean_title = base_title.replace('_', ' ').replace('—', '-').replace('--', '-')

            file_metadata['name'] = clean_title
            
            # Clean up existing titles and metadata so we can standardize it
            md_content = re.sub(r'(?i)^#\s+.*?\n+', '', md_content.lstrip())
            md_content = re.sub(r'(?i)^\*\*Prepared By:\*\*.*?\n+', '', md_content)
            md_content = re.sub(r'(?i)^\*\*Author:\*\*.*?\n+', '', md_content)
            md_content = re.sub(r'(?i)^\*\*Date:\*\*.*?\n+', '', md_content)
            
            # Fix Markdown parsing bugs where <br> tags break subsequent markdown links
            md_content = md_content.replace('<br>', '<br> ').replace('<br/>', '<br/> ')
            md_content = re.sub(r'([^\n])\n(\s*[-*]\s)', r'\1\n\n\2', md_content)

            today = datetime.now().strftime('%Y-%m-%d')
            # Author and date placed perfectly after title
            header = f"# {clean_title}\n\n**Author:** Your Name\n\n**Date:** {today}\n\n---\n\n"
            md_content = header + md_content

            html_body = markdown.markdown(md_content, extensions=['tables', 'nl2br'])

            # Added extra large page size for pageless feel, and auto-sizing table columns without forcing 100% width
            styled_html = f"""
            <html>
            <head>
            <style>
            @page {{ size: 20in 30in; margin: 1in; }}
            body {{ font-family: 'Calibri', sans-serif; font-size: 11pt; }}
            table {{ border-collapse: collapse; margin-bottom: 20px; }}
            th, td {{ border: 1px solid #000000; padding: 10px 14px; text-align: left; vertical-align: top; }}
            th {{ background-color: #f3f3f3; }}
            h1, h2, h3 {{ font-family: 'Calibri', sans-serif; }}
            ul, ol {{ margin-top: 0; margin-bottom: 10px; padding-left: 20px; }}
            li {{ margin-bottom: 5px; }}
            </style>
            </head>
            <body>
            {html_body}
            </body>
            </html>
            """
            
            fd, temp_path = tempfile.mkstemp(suffix='.html')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(styled_html)
                
            file_path = temp_path
            mime_type = 'text/html'
            file_metadata['mimeType'] = 'application/vnd.google-apps.document'
            print(f"[Secondary Drive] Converting markdown to formatted Google Doc: {clean_title}...")
        except ImportError:
            file_metadata['mimeType'] = 'application/vnd.google-apps.document'
            print(f"[Secondary Drive] python-markdown not installed. Converting {file_name} as plain text...")
    elif convert_to_docs and mime_type and mime_type.startswith('text/'):
        file_metadata['mimeType'] = 'application/vnd.google-apps.document'
        print(f"[Secondary Drive] Converting {file_name} to Google Doc...")
    else:
        print(f"[Secondary Drive] Uploading {file_name} as {mime_type}...")

    media = MediaFileUpload(file_path, mimetype=mime_type)

    try:
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink',
            supportsAllDrives=True
        ).execute()
        assert_drive_result(file, 'gdrive_manager (secondary) upload')
        _format_pass(file.get('id'), file_metadata, 'upload')
        print(f"File ID: {file.get('id')}")
        print(f"Link: {file.get('webViewLink')}")

        if share:
            print("Setting permissions to 'anyone' can 'comment'...")
            user_permission = {'type': 'anyone', 'role': 'commenter'}
            service.permissions().create(
                fileId=file.get('id'), 
                body=user_permission, 
                fields='id',
                supportsAllDrives=True
            ).execute()
            print("Permission set successfully.")
        
        return file.get('id')

    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def update_file(file_id, file_path, convert_to_docs=False):
    """Update an existing file in Secondary's Google Drive."""
    creds = authenticate()
    if not creds:
        return None

    service = build('drive', 'v3', credentials=creds)

    file_name = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = 'application/octet-stream'

    file_metadata = {}
    
    import tempfile
    from datetime import datetime

    if convert_to_docs and file_path.endswith('.md'):
        try:
            import markdown
            import re
            with open(file_path, 'r', encoding='utf-8') as f:
                md_content = f.read()

            title_match = re.search(r'(?m)^#\s+(.*)', md_content)
            if title_match:
                clean_title = title_match.group(1).strip()
            else:
                base_title = os.path.splitext(file_name)[0]
                clean_title = base_title.replace('_', ' ').replace('—', '-').replace('--', '-')

            file_metadata['name'] = clean_title
            
            md_content = re.sub(r'(?i)^#\s+.*?\n+', '', md_content.lstrip())
            md_content = re.sub(r'(?i)^\*\*Prepared By:\*\*.*?\n+', '', md_content)
            md_content = re.sub(r'(?i)^\*\*Author:\*\*.*?\n+', '', md_content)
            md_content = re.sub(r'(?i)^\*\*Date:\*\*.*?\n+', '', md_content)
            
            md_content = md_content.replace('<br>', '<br> ').replace('<br/>', '<br/> ')
            md_content = re.sub(r'([^\n])\n(\s*[-*]\s)', r'\1\n\n\2', md_content)

            today = datetime.now().strftime('%Y-%m-%d')
            header = f"# {clean_title}\n\n**Author:** Your Name\n\n**Date:** {today}\n\n---\n\n"
            md_content = header + md_content

            html_body = markdown.markdown(md_content, extensions=['tables', 'nl2br'])

            styled_html = f"""
            <html>
            <head>
            <style>
            @page {{ size: 20in 30in; margin: 1in; }}
            body {{ font-family: 'Calibri', sans-serif; font-size: 11pt; }}
            table {{ border-collapse: collapse; margin-bottom: 20px; }}
            th, td {{ border: 1px solid #000000; padding: 10px 14px; text-align: left; vertical-align: top; }}
            th {{ background-color: #f3f3f3; }}
            h1, h2, h3 {{ font-family: 'Calibri', sans-serif; }}
            ul, ol {{ margin-top: 0; margin-bottom: 10px; padding-left: 20px; }}
            li {{ margin-bottom: 5px; }}
            </style>
            </head>
            <body>
            {html_body}
            </body>
            </html>
            """
            
            fd, temp_path = tempfile.mkstemp(suffix='.html')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(styled_html)
                
            file_path = temp_path
            mime_type = 'text/html'
            file_metadata['mimeType'] = 'application/vnd.google-apps.document'
            print(f"[Secondary Drive] Updating Google Doc {file_id} with new markdown...")
        except ImportError:
            file_metadata['mimeType'] = 'application/vnd.google-apps.document'
            print(f"[Secondary Drive] python-markdown not installed. Converting {file_name} as plain text...")
    elif convert_to_docs and mime_type and mime_type.startswith('text/'):
        file_metadata['mimeType'] = 'application/vnd.google-apps.document'
        print(f"[Secondary Drive] Updating {file_id} to Google Doc...")
    else:
        print(f"[Secondary Drive] Updating {file_id} as {mime_type}...")

    media = MediaFileUpload(file_path, mimetype=mime_type)

    try:
        file = service.files().update(
            fileId=file_id,
            media_body=media,
            fields='id, webViewLink',
            supportsAllDrives=True
        ).execute()
        assert_drive_result(file, 'gdrive_manager (secondary) update')
        _format_pass(file.get('id'), file_metadata, 'update')
        print(f"Successfully updated Document ID: {file.get('id')}")
        print(f"Link: {file.get('webViewLink')}")
        return file.get('id')
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def search_files(query):
    """Search for files in Secondary's Google Drive."""
    creds = authenticate()
    if not creds:
        return

    service = build('drive', 'v3', credentials=creds)

    try:
        # Search for files matching the query in name, excluding trashed files
        q = f"name contains '{query}' and trashed = false"
        
        results = service.files().list(
            q=q,
            pageSize=10,
            fields="files(id, name, webViewLink, mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        items = results.get('files', [])

        if not items:
            print(f"[Secondary Drive] No files found matching '{query}'.")
            return

        print(f"[Secondary Drive] Found {len(items)} file(s):")
        for item in items:
            print(f"  - {item['name']}")
            print(f"    ID: {item['id']}")
            print(f"    Link: {item.get('webViewLink', 'N/A')}")
            print()

    except Exception as e:
        print(f"An error occurred: {e}")

def read_file(file_id):
    """Read/Download content from Secondary's Google Drive."""
    creds = authenticate()
    if not creds:
        return

    service = build('drive', 'v3', credentials=creds)

    try:
        file_meta = service.files().get(
            fileId=file_id, 
            fields='mimeType, name',
            supportsAllDrives=True
        ).execute()
        mime_type = file_meta.get('mimeType')
        file_name = file_meta.get('name')
        print(f"[Secondary Drive] Reading: {file_name} ({mime_type})")
        print("---")

        if mime_type == 'application/vnd.google-apps.document':
            content = service.files().export(
                fileId=file_id, 
                mimeType='text/plain'
            ).execute()
            print(content.decode('utf-8'))
        elif mime_type == 'application/vnd.google-apps.spreadsheet':
            content = service.files().export(
                fileId=file_id, 
                mimeType='text/csv'
            ).execute()
            print(content.decode('utf-8'))
        else:
            content = service.files().get_media(
                fileId=file_id,
                supportsAllDrives=True
            ).execute()
            try:
                print(content.decode('utf-8'))
            except:
                print(f"[Binary content, {len(content)} bytes]")

    except Exception as e:
        print(f"An error occurred: {e}")

def list_comments(file_id, json_output=False):
    """List comments on a Secondary Drive file."""
    creds = authenticate()
    if not creds:
        return

    service = build('drive', 'v3', credentials=creds)

    try:
        # Get file metadata to confirm access/existence
        file_meta = service.files().get(
            fileId=file_id, 
            fields='name',
            supportsAllDrives=True
        ).execute()
        print(f"[Secondary Drive] File: {file_meta.get('name')}")
        print("-" * 40)

        results = service.comments().list(
            fileId=file_id,
            fields="comments(id, content, author(displayName), quotedFileContent, replies(content, author(displayName)), createdTime)",
            pageSize=100,
            includeDeleted=False
        ).execute()
        comments = results.get('comments', [])

        if not comments:
            print("[Secondary Drive] No comments found.")
            return

        print(f"[Secondary Drive] Found {len(comments)} comment threads:")
        for i, comment in enumerate(comments, 1):
            author = comment.get('author', {}).get('displayName', 'Unknown')
            content = comment.get('content', '').replace('\n', ' ')
            quoted = comment.get('quotedFileContent', {}).get('value', '')
            created = comment.get('createdTime', '')

            print(f"\n[{i}] {author} ({created})")
            if quoted:
                print(f"    Context: \"{quoted}\"")
            print(f"    Comment: {content}")
            
            replies = comment.get('replies', [])
            if replies:
                print(f"    Replies ({len(replies)}):")
                for reply in replies:
                    r_author = reply.get('author', {}).get('displayName', 'Unknown')
                    r_content = reply.get('content', '').replace('\n', ' ')
                    print(f"      - {r_author}: {r_content}")

    except Exception as e:
        print(f"An error occurred: {e}")

def main():
    parser = argparse.ArgumentParser(description='Secondary Drive Manager: Upload, Search, Read.')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    upload_parser = subparsers.add_parser('upload', help='Upload a file')
    upload_parser.add_argument('--file', required=True, help='Path to the file')
    upload_parser.add_argument('--folder', help='Folder ID to upload to')
    upload_parser.add_argument('--convert', action='store_true', help='Convert to Google Docs')
    upload_parser.add_argument('--share', action='store_true', help='Share with anyone to comment')

    update_parser = subparsers.add_parser('update', help='Update an existing file')
    update_parser.add_argument('--id', required=True, help='File ID to update')
    update_parser.add_argument('--file', required=True, help='Path to the new file')
    update_parser.add_argument('--convert', action='store_true', help='Convert to Google Docs')

    search_parser = subparsers.add_parser('search', help='Search for files')
    search_parser.add_argument('--query', required=True, help='Search query')

    read_parser = subparsers.add_parser('read', help='Read a file')
    read_parser.add_argument('--id', required=True, help='File ID')

    comments_parser = subparsers.add_parser('comments', help='List comments on a file')
    comments_parser.add_argument('--id', required=True, help='File ID')
    comments_parser.add_argument('--json', action='store_true', help='Output as JSON (not implemented yet)')

    args = parser.parse_args()

    if args.command == 'upload':
        if os.path.exists(args.file):
            upload_file(args.file, args.folder, args.convert, args.share)
        else:
            print(f"File not found: {args.file}")
    elif args.command == 'update':
        if os.path.exists(args.file):
            update_file(args.id, args.file, args.convert)
        else:
            print(f"File not found: {args.file}")
    elif args.command == 'search':
        search_files(args.query)
    elif args.command == 'read':
        read_file(args.id)
    elif args.command == 'comments':
        list_comments(args.id, args.json)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
