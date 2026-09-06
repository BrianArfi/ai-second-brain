#!/usr/bin/env python3
"""
Google Drive Manager
Provides upload, search, and read capabilities for Google Drive.
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
ACCOUNT_NAME = 'personal'

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

# Determine the base directory (where credentials.json lives)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = SCRIPT_DIR
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'credentials.json')
TOKEN_FILE = os.path.join(BASE_DIR, 'token.json')

REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, '.agent', 'scripts'))
from file_utils import assert_drive_result  # Drive Operation Verification (CLAUDE.md)
from file_utils import apply_visibility, add_visibility_arg, resolve_visibility  # sharing (CLAUDE.md LANDMINE)

# Scopes - drive.file only accesses files created by this app
# Change to 'https://www.googleapis.com/auth/drive' for full access (requires re-auth)
SCOPES = ['https://www.googleapis.com/auth/drive']

def authenticate():
    """Authenticate and return credentials."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"Error: {CREDENTIALS_FILE} not found.")
                print("Please download it from Google Cloud Console.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            # Set explicit redirect_uri to avoid 'Missing required parameter' error
            flow.redirect_uri = 'http://localhost:8080/'
            # Use console flow instead of local server for headless environments
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
            print(f"\n[Google Drive] Authentication Required!")
            print(f"1. Visit this URL in your browser:\n   {auth_url}")
            print(f"2. Authorize the application and copy the 'code' parameter from the resulting URL.")
            print(f"   (The page may fail to load, just copy the 'code=' value from the address bar)")
            
            code = input("\n[Google Drive] Enter the authorization code: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return creds
    

def set_commenter_permission(service, file_id, visibility='private'):
    """Deprecated shim. Sharing now lives in file_utils.apply_visibility.

    This is the personal Drive, so there is no Workspace domain to fall back on:
    the default here is 'private' rather than 'domain'. Pass --share (or
    --visibility public) to publish deliberately.
    """
    apply_visibility(service, file_id, visibility)

def update_file(file_id, file_path, convert_to_docs=False, visibility='private'):
    """Update an existing file in Google Drive in-place without deleting it."""
    creds = authenticate()
    if not creds:
        return None

    service = build('drive', 'v3', credentials=creds)

    file_name = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = 'application/octet-stream'

    file_metadata = {}
    if convert_to_docs and (mime_type.startswith('text/') or file_path.endswith('.md')):
        file_metadata['mimeType'] = 'application/vnd.google-apps.document'
        print(f"Updating Google Doc with converted content...")
    else:
        print(f"Updating file ID {file_id} with {file_name}...")

    media = MediaFileUpload(file_path, mimetype=mime_type)

    try:
        file = service.files().update(
            fileId=file_id,
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        assert_drive_result(file, 'gdrive_manager (personal) update')
        _format_pass(file.get('id'), file_metadata, 'update')
        print(f"File ID: {file.get('id')}")
        print(f"Link: {file.get('webViewLink')}")

        apply_visibility(service, file.get('id'), visibility)

        return file.get('id')
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def upload_file(file_path, folder_id=None, convert_to_docs=False, visibility='private', role='commenter'):
    """Upload a file to Google Drive."""
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

    if convert_to_docs and (mime_type.startswith('text/') or file_path.endswith('.md')):
        file_metadata['mimeType'] = 'application/vnd.google-apps.document'
        print(f"Converting {file_name} to Google Doc...")
    else:
        print(f"Uploading {file_name} as {mime_type}...")

    media = MediaFileUpload(file_path, mimetype=mime_type)

    try:
        file = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
        assert_drive_result(file, 'gdrive_manager (personal) upload')
        _format_pass(file.get('id'), file_metadata, 'upload')
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

    print(f"Sharing file {file_id} with {email} as {role}...")
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
        print(f"Successfully shared with {email}.")
    except Exception as e:
        print(f"An error occurred sharing with {email}: {e}")

def search_files(query):
    """Search for files in Google Drive."""
    print(f"DEBUG: Starting search for '{query}'...")
    try:
        creds = authenticate()
        if not creds:
            print("DEBUG: Authentication failed or returned None.")
            return

        service = build('drive', 'v3', credentials=creds)
        print("DEBUG: Service built successfully.")

        try:
            # Check if query looks like a raw Drive API query (contains '=' or other operators)
            if '=' in query or ' and ' in query or ' or ' in query:
                q = f"{query} and trashed = false"
            else:
                # Search for files matching the query in name, excluding trashed files
                q = f"name contains '{query}' and trashed = false"
            
            print(f"DEBUG: Executing query: {q}")

            results = service.files().list(
                q=q,
                pageSize=100,
                fields="files(id, name, webViewLink, mimeType, createdTime, modifiedTime)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            items = results.get('files', [])

            if not items:
                print(f"No files found matching '{query}'.")
                return

            print(f"Found {len(items)} file(s):")
            for item in items:
                print(f"  - {item['name']}")
                print(f"    ID: {item['id']}")
                print(f"    Link: {item.get('webViewLink', 'N/A')}")
                print(f"    Type: {item['mimeType']}")
                print(f"    Created: {item.get('createdTime', 'N/A')}")
                print(f"    Modified: {item.get('modifiedTime', 'N/A')}")
                print()

        except Exception as e:
            print(f"An error occurred during search: {e}")
            import traceback
            traceback.print_exc()

    except Exception as e:
        print(f"An error occurred during setup: {e}")
        import traceback
        traceback.print_exc()

def read_file(file_id):
    """Read/Download content from a Google Drive file."""
    creds = authenticate()
    if not creds:
        return

    service = build('drive', 'v3', credentials=creds)

    try:
        # Get file metadata to check type
        file_meta = service.files().get(fileId=file_id, fields='mimeType, name').execute()
        mime_type = file_meta.get('mimeType')
        file_name = file_meta.get('name')
        print(f"Reading: {file_name} ({mime_type})")
        print("---")

        # If it's a Google Doc, export as plain text
        if mime_type == 'application/vnd.google-apps.document':
            content = service.files().export(fileId=file_id, mimeType='text/plain').execute()
            print(content.decode('utf-8'))
        elif mime_type == 'application/vnd.google-apps.spreadsheet':
            content = service.files().export(fileId=file_id, mimeType='text/csv').execute()
            print(content.decode('utf-8'))
        else:
            # For other files, download content
            content = service.files().get_media(fileId=file_id).execute()
            # Try to decode as text; if fails, indicate binary
            try:
                print(content.decode('utf-8'))
            except:
                print(f"[Binary content, {len(content)} bytes]")

    except Exception as e:
        print(f"An error occurred: {e}")

def delete_file(file_id, permanent=False):
    """Move a file to trash, or permanently delete if --permanent is set."""
    creds = authenticate()
    if not creds:
        return

    service = build('drive', 'v3', credentials=creds)

    try:
        if permanent:
            service.files().delete(fileId=file_id).execute()
            print(f"[OK] Permanently deleted file ID: {file_id}")
        else:
            service.files().update(fileId=file_id, body={'trashed': True}).execute()
            print(f"[OK] Moved to trash: {file_id} (restore from Drive Trash if needed)")
    except Exception as e:
        print(f"An error occurred: {e}")

def list_comments(file_id, json_output=False):
    """List comments on a Google Drive file."""
    creds = authenticate()
    if not creds:
        return

    service = build('drive', 'v3', credentials=creds)

    try:
        # Get file metadata to confirm access/existence
        file_meta = service.files().get(fileId=file_id, fields='name').execute()
        print(f"File: {file_meta.get('name')}")
        print("-" * 40)

        results = service.comments().list(
            fileId=file_id,
            fields="comments(id, content, author(displayName), quotedFileContent, replies(content, author(displayName)), createdTime)",
            pageSize=100
        ).execute()
        comments = results.get('comments', [])

        if not comments:
            print("No comments found.")
            return

        print(f"Found {len(comments)} comment threads:")
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
    parser = argparse.ArgumentParser(description='Google Drive Manager: Upload, Search, Read.')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Upload command
    upload_parser = subparsers.add_parser('upload', help='Upload a file to Drive')
    upload_parser.add_argument('--file', required=True, help='Path to the file to upload')
    upload_parser.add_argument('--folder', help='ID of the folder to upload to')
    upload_parser.add_argument('--convert', action='store_true', help='Convert to Google Docs')
    add_visibility_arg(upload_parser, default='private')
    upload_parser.add_argument('--role', default='commenter', choices=['viewer', 'commenter', 'writer'], help='Role for sharing (default: commenter)')

    # Search command
    search_parser = subparsers.add_parser('search', help='Search for files in Drive')
    search_parser.add_argument('--query', required=True, help='Search query (file name)')

    # Read command
    read_parser = subparsers.add_parser('read', help='Read/Download content from Drive')
    read_parser.add_argument('--id', required=True, help='File ID to read')

    # Share command
    share_parser = subparsers.add_parser('share', help='Share a file with a specific email')
    share_parser.add_argument('--id', required=True, help='File ID to share')
    share_parser.add_argument('--email', required=True, help='Email to share with')
    share_parser.add_argument('--role', default='commenter', choices=['viewer', 'commenter', 'writer'], help='Role for sharing')

    # Comments command
    comments_parser = subparsers.add_parser('comments', help='List comments on a file')
    comments_parser.add_argument('--id', required=True, help='File ID to check')
    comments_parser.add_argument('--json', action='store_true', help='Output as JSON (not implemented yet)')

    # Update command
    update_parser = subparsers.add_parser('update', help='Update an existing file in-place')
    update_parser.add_argument('--id', required=True, help='File ID of existing Drive file to update')
    update_parser.add_argument('--file', required=True, help='Path to the new local file')
    update_parser.add_argument('--convert', action='store_true', help='Convert to Google Doc format')
    add_visibility_arg(update_parser, default='private')

    # Delete command
    delete_parser = subparsers.add_parser('delete', help='Move file to trash (or permanently delete)')
    delete_parser.add_argument('--id', required=True, help='File ID to delete')
    delete_parser.add_argument('--permanent', action='store_true', help='Permanently delete (default: trash)')

    args = parser.parse_args()

    if args.command == 'upload':
        if os.path.exists(args.file):
            upload_file(args.file, args.folder, args.convert, resolve_visibility(args, 'private'), args.role)
        else:
            print(f"File not found: {args.file}")
    elif args.command == 'update':
        if os.path.exists(args.file):
            update_file(args.id, args.file, args.convert, resolve_visibility(args, 'private'))
        else:
            print(f"File not found: {args.file}")
    elif args.command == 'delete':
        delete_file(args.id, args.permanent)
    elif args.command == 'search':
        search_files(args.query)
    elif args.command == 'read':
        read_file(args.id)
    elif args.command == 'share':
        share_file(args.id, args.email, args.role)
    elif args.command == 'comments':
        list_comments(args.id, args.json)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
