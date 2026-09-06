#!/usr/bin/env python3
"""
Work Google Drive Manager
Provides upload, search, and read for Work's Google Drive.
Uses credentials specific to Work's Drive.
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
ACCOUNT_NAME = 'work'

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
from file_utils import apply_visibility, add_visibility_arg, resolve_visibility  # sharing (CLAUDE.md LANDMINE)

# Full drive access for Work's shared drive
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
            # Set explicit redirect_uri to avoid 'Missing required parameter' error
            flow.redirect_uri = 'http://localhost:8080/'
            # Use console flow instead of local server for headless environments
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
            print(f"\n[Work Drive] Authentication Required!")
            print(f"1. Visit this URL in your browser:\n   {auth_url}")
            print(f"2. Authorize the application and copy the 'code' parameter from the resulting URL.")
            print(f"   (The page may fail to load, just copy the 'code=' value from the address bar)")
            
            code = input("\n[Work Drive] Enter the authorization code: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials
        # Save credentials to local token file
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return creds

def set_commenter_permission(service, file_id, visibility='domain'):
    """Deprecated shim. Sharing now lives in file_utils.apply_visibility.

    Kept so any caller outside this file keeps working, but it no longer
    publishes: the default is domain-restricted. See file_utils.apply_visibility
    for why this stopped being a local 'always make it public' helper.
    """
    apply_visibility(service, file_id, visibility)

def update_file(file_id, file_path, convert_to_docs=False, visibility='domain'):
    """Update an existing file in Work's Google Drive without deleting it."""
    creds = authenticate()
    if not creds:
        return None

    service = build('drive', 'v3', credentials=creds)

    file_name = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = 'application/octet-stream'

    import tempfile
    from datetime import datetime

    file_metadata = {}

    if convert_to_docs and file_path.endswith('.md'):
        try:
            import markdown
            import re
            # Fetch existing name to preserve it in the header
            existing_meta = service.files().get(fileId=file_id, fields='name').execute()
            existing_name = existing_meta.get('name', 'Untitled Document')

            with open(file_path, 'r', encoding='utf-8') as f:
                md_content = f.read()

            # Clean up existing titles and metadata in the source markdown
            md_content = re.sub(r'(?i)^#\s+.*?\n+', '', md_content.lstrip())
            md_content = re.sub(r'(?i)^\*\*Prepared By:\*\*.*?\n+', '', md_content)
            md_content = re.sub(r'(?i)^\*\*Author:\*\*.*?\n+', '', md_content)
            md_content = re.sub(r'(?i)^\*\*Date:\*\*.*?\n+', '', md_content)
            md_content = md_content.replace('<br>', '<br> ').replace('<br/>', '<br/> ')
            md_content = re.sub(r'([^\n])\n(\s*[\*\-]\s)', r'\1\n\n\2', md_content)

            today = datetime.now().strftime('%Y-%m-%d')
            # Use existing_name to keep the header consistent with the Drive title
            header = f"# {existing_name}\n\n**Author:** Your Name\n\n**Date:** {today}\n\n"
            md_content = header + md_content

            html_body = markdown.markdown(md_content, extensions=['tables', 'nl2br', 'fenced_code', 'sane_lists'])
            styled_html = f"""
            <html>
            <head>
            <style>
            @page {{ size: 20in 30in; margin: 1in; }}
            body {{ font-family: 'Calibri', sans-serif; font-size: 12pt; line-height: 1.6; color: #111; }}
            p {{ margin-top: 0; margin-bottom: 12px; }}
            h1 {{ font-size: 19pt; margin-top: 0; margin-bottom: 16px; }}
            h2 {{ font-size: 15pt; margin-top: 28px; margin-bottom: 10px; }}
            h3 {{ font-size: 13pt; margin-top: 20px; margin-bottom: 8px; }}
            h1, h2, h3 {{ font-family: 'Calibri', sans-serif; }}
            table {{ border-collapse: collapse; table-layout: auto; margin-bottom: 20px; width: auto; }}
            th, td {{ border: 1px solid #ccc; padding: 8px 14px; text-align: left; vertical-align: top; }}
            th {{ background-color: #f0f0f0; font-weight: bold; }}
            ul, ol {{ margin-top: 6px; margin-bottom: 14px; padding-left: 24px; }}
            li {{ margin-bottom: 8px; line-height: 1.6; }}
            li p {{ margin-bottom: 4px; }}
            blockquote {{ margin: 12px 0; padding: 10px 16px; background: #f9f9f9; border-left: 4px solid #ccc; color: #444; }}
            strong {{ font-weight: bold; }}
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
            print(f"[Work Drive] Updating Google Doc with converted markdown...")
        except ImportError:
            print(f"[Work Drive] python-markdown not installed. Updating {file_name} as plain text...")
    elif convert_to_docs and file_path.endswith('.csv'):
        file_metadata['mimeType'] = 'application/vnd.google-apps.spreadsheet'
        print(f"[Work Drive] Updating file ID {file_id} and converting {file_name} to Google Sheet...")
    else:
        print(f"[Work Drive] Updating file ID {file_id} with {file_name}...")

    media = MediaFileUpload(file_path, mimetype=mime_type)

    try:
        file = service.files().update(
            fileId=file_id,
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        assert_drive_result(file, 'gdrive_manager (work) update')
        _format_pass(file.get('id'), file_metadata, 'update')
        print(f"File ID: {file.get('id')}")
        print(f"Link: {file.get('webViewLink')}")

        apply_visibility(service, file.get('id'), visibility)

        return file.get('id')
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def share_file(file_id, email, role='commenter'):
    """Share a file with a specific email address."""
    creds = authenticate()
    if not creds:
        return None

    service = build('drive', 'v3', credentials=creds)

    print(f"[Work Drive] Sharing file {file_id} with {email} as {role}...")
    user_permission = {
        'type': 'user',
        'role': role,
        'emailAddress': email,
    }
    try:
        service.permissions().create(
            fileId=file_id,
            body=user_permission,
            fields='id',
        ).execute()
        print(f"[Work Drive] Successfully shared with {email}.")
    except Exception as e:
        print(f"[Work Drive] An error occurred sharing with {email}: {e}")

def upload_file(file_path, folder_id=None, convert_to_docs=False, visibility='domain'):
    """Upload a file to Work's Google Drive."""
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
            
            # Ensure lists (bullet and numbered) have an empty line before them
            md_content = re.sub(r'([^\n])\n(\s*[\*\-]\s)', r'\1\n\n\2', md_content)
            md_content = re.sub(r'([^\n])\n(\s*\d+\.\s)', r'\1\n\n\2', md_content)

            today = datetime.now().strftime('%Y-%m-%d')
            # Author and date placed perfectly after title
            header = f"# {clean_title}\n\n**Author:** Your Name\n\n**Date:** {today}\n\n"
            md_content = header + md_content

            html_body = markdown.markdown(md_content, extensions=['tables', 'nl2br', 'fenced_code', 'sane_lists'])

            # Added extra large page size for pageless feel, and auto-sizing table columns without forcing 100% width
            styled_html = f"""
            <html>
            <head>
            <style>
            @page {{ size: 20in 30in; margin: 1in; }}
            body {{ font-family: 'Calibri', sans-serif; font-size: 12pt; line-height: 1.6; color: #111; }}
            table {{ border-collapse: collapse; table-layout: auto; margin-bottom: 20px; }}
            th, td {{ border: 1px solid #000000; padding: 10px 14px; text-align: left; vertical-align: top; }}
            th {{ background-color: #f3f3f3; }}
            h1, h2, h3 {{ font-family: 'Calibri', sans-serif; }}
            h1 {{ font-size: 19pt; }}
            h2 {{ font-size: 15pt; }}
            h3 {{ font-size: 13pt; }}
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
            print(f"[Work Drive] Converting markdown to formatted Google Doc: {clean_title}...")
        except ImportError:
            file_metadata['mimeType'] = 'application/vnd.google-apps.document'
            print(f"[Work Drive] python-markdown not installed. Converting {file_name} as plain text...")
    elif convert_to_docs and file_path.endswith('.csv'):
        file_metadata['mimeType'] = 'application/vnd.google-apps.spreadsheet'
        print(f"[Work Drive] Converting {file_name} to Google Sheet...")
    elif convert_to_docs and mime_type and mime_type.startswith('text/'):
        file_metadata['mimeType'] = 'application/vnd.google-apps.document'
        print(f"[Work Drive] Converting {file_name} to Google Doc...")
    else:
        print(f"[Work Drive] Uploading {file_name} as {mime_type}...")

    media = MediaFileUpload(file_path, mimetype=mime_type)

    try:
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        assert_drive_result(file, 'gdrive_manager (work) upload')
        _format_pass(file.get('id'), file_metadata, 'upload')
        print(f"File ID: {file.get('id')}")
        print(f"Link: {file.get('webViewLink')}")

        apply_visibility(service, file.get('id'), visibility)

        return file.get('id')

    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def search_files(query):
    """Search for files in Work's Google Drive."""
    import sys
    print(f"DEBUG: Starting search for '{query}'", file=sys.stderr, flush=True)
    try:
        creds = authenticate()
        if not creds:
            print("DEBUG: No creds", file=sys.stderr, flush=True)
            return

        service = build('drive', 'v3', credentials=creds)

        print(f"DEBUG: Service built, searching for '{query}'...", file=sys.stderr, flush=True)
        
        # Build the 'q' parameter for search
        # We search for files that have the query in their name
        q = f"name contains '{query}' and trashed = false"
        
        results = service.files().list(
            q=q,
            pageSize=50,
            fields="files(id, name, webViewLink, mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        
        items = results.get('files', [])
        print(f"DEBUG: Found {len(items)} items", file=sys.stderr, flush=True)

        if not items:
            print(f"[Work Drive] No files found for query: {query}", file=sys.stderr)
            return

        print(f"[Work Drive] Found {len(items)} file(s):", file=sys.stderr)
        for item in items:
            print(f"  - {item['name']}", file=sys.stderr)
            print(f"    ID: {item['id']}", file=sys.stderr)
            print(f"    Link: {item.get('webViewLink', 'N/A')}", file=sys.stderr)
            print("", file=sys.stderr)

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"An error occurred: {e}", file=sys.stderr)

def read_file(file_id):
    """Read/Download content from Work's Google Drive."""
    creds = authenticate()
    if not creds:
        return

    service = build('drive', 'v3', credentials=creds)

    try:
        file_meta = service.files().get(fileId=file_id, fields='mimeType, name', supportsAllDrives=True).execute()
        mime_type = file_meta.get('mimeType')
        file_name = file_meta.get('name')
        print(f"[Work Drive] Reading: {file_name} ({mime_type})")
        print("---")

        if mime_type == 'application/vnd.google-apps.document':
            content = service.files().export(fileId=file_id, mimeType='text/plain').execute()
            print(content.decode('utf-8'))
        elif mime_type == 'application/vnd.google-apps.spreadsheet':
            content = service.files().export(fileId=file_id, mimeType='text/csv').execute()
            print(content.decode('utf-8'))
        else:
            content = service.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
            try:
                print(content.decode('utf-8'))
            except:
                print(f"[Binary content, {len(content)} bytes]")

    except Exception as e:
        print(f"An error occurred: {e}")

def list_comments(file_id, json_output=False):
    """List comments on a Work Drive file."""
    creds = authenticate()
    if not creds:
        return

    service = build('drive', 'v3', credentials=creds)

    try:
        # Get file metadata to confirm access/existence
        file_meta = service.files().get(fileId=file_id, fields='name').execute()
        print(f"[Work Drive] File: {file_meta.get('name')}")
        print("-" * 40)

        results = service.comments().list(
            fileId=file_id,
            fields="comments(id, content, author(displayName), quotedFileContent, replies(content, author(displayName)), createdTime)",
            pageSize=100
        ).execute()
        comments = results.get('comments', [])

        if not comments:
            print("[Work Drive] No comments found.")
            return

        print(f"[Work Drive] Found {len(comments)} comment threads:")
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

def delete_file(file_id, permanent=False):
    """Move a file to trash, or permanently delete if permanent=True."""
    creds = authenticate()
    if not creds:
        return

    service = build('drive', 'v3', credentials=creds)

    try:
        if permanent:
            service.files().delete(fileId=file_id).execute()
            print(f"[Work Drive] Permanently deleted file ID: {file_id}")
        else:
            service.files().update(fileId=file_id, body={'trashed': True}).execute()
            print(f"[Work Drive] Moved to trash: {file_id} (restore from Drive Trash if needed)")
    except Exception as e:
        print(f"An error occurred: {e}")

def rename_file(file_id, new_name):
    """Rename a file in Work's Google Drive."""
    creds = authenticate()
    if not creds:
        return None

    service = build('drive', 'v3', credentials=creds)

    try:
        file = service.files().update(
            fileId=file_id,
            body={'name': new_name},
            fields='id, name'
        ).execute()
        assert_drive_result(file, 'gdrive_manager (work) rename')
        print(f"Renamed File ID: {file.get('id')}")
        print(f"New Name: {file.get('name')}")
        return file.get('id')
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description='Work Drive Manager: Upload, Search, Read.')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    upload_parser = subparsers.add_parser('upload', help='Upload a file')
    upload_parser.add_argument('--file', required=True, help='Path to the file')
    upload_parser.add_argument('--folder', help='Folder ID to upload to')
    upload_parser.add_argument('--convert', action='store_true', help='Convert to Google Docs')
    add_visibility_arg(upload_parser)

    search_parser = subparsers.add_parser('search', help='Search for files')
    search_parser.add_argument('--query', required=True, help='Search query')

    read_parser = subparsers.add_parser('read', help='Read a file')
    read_parser.add_argument('--id', required=True, help='File ID')

    comments_parser = subparsers.add_parser('comments', help='List comments on a file')
    comments_parser.add_argument('--id', required=True, help='File ID')
    comments_parser.add_argument('--json', action='store_true', help='Output as JSON (not implemented yet)')

    update_parser = subparsers.add_parser('update', help='Update an existing file in-place')
    update_parser.add_argument('--id', required=True, help='File ID of existing Drive file to update')
    update_parser.add_argument('--file', required=True, help='Path to the new local file')
    update_parser.add_argument('--convert', action='store_true', help='Convert markdown to Google Doc')
    add_visibility_arg(update_parser)

    rename_parser = subparsers.add_parser('rename', help='Rename a file')
    rename_parser.add_argument('--id', required=True, help='File ID')
    rename_parser.add_argument('--name', required=True, help='New name for the file')

    # Share command
    share_parser = subparsers.add_parser('share', help='Share a file with a specific email')
    share_parser.add_argument('--id', required=True, help='File ID to share')
    share_parser.add_argument('--email', required=True, help='Email to share with')
    share_parser.add_argument('--role', default='commenter', choices=['viewer', 'commenter', 'writer'], help='Role for sharing')

    delete_parser = subparsers.add_parser('delete', help='Move file to trash (or permanently delete)')
    delete_parser.add_argument('--id', required=True, help='File ID to delete')
    delete_parser.add_argument('--permanent', action='store_true', help='Permanently delete (default: trash)')

    args = parser.parse_args()

    if args.command == 'upload':
        if os.path.exists(args.file):
            upload_file(args.file, args.folder, args.convert, resolve_visibility(args))
        else:
            print(f"File not found: {args.file}")
    elif args.command == 'update':
        if os.path.exists(args.file):
            update_file(args.id, args.file, args.convert, resolve_visibility(args))
        else:
            print(f"File not found: {args.file}")
    elif args.command == 'search':
        search_files(args.query)
    elif args.command == 'read':
        read_file(args.id)
    elif args.command == 'comments':
        list_comments(args.id, args.json)
    elif args.command == 'rename':
        rename_file(args.id, args.name)
    elif args.command == 'share':
        share_file(args.id, args.email, args.role)
    elif args.command == 'delete':
        delete_file(args.id, args.permanent)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
