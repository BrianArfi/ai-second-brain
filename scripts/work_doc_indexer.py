import os
import sys
import argparse
import re
import json
from googleapiclient.discovery import build
import httplib2
import google_auth_httplib2

# Add work-drive-connector to path for auth
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Note: the path below assumes the script is one level under the repo root, in scripts/
DRIVE_CONNECTOR_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '.agent', 'skills', 'work-drive-connector'))
if DRIVE_CONNECTOR_DIR not in sys.path:
    sys.path.append(DRIVE_CONNECTOR_DIR)

from gdrive_manager import authenticate

# ── Execution Guard ──────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_GUARD_PATH = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '.agent', 'skills', 'execution-guard', 'scripts'))
if _GUARD_PATH not in sys.path:
    sys.path.append(_GUARD_PATH)
try:
    from execution_guard import timeout_guard, TimeoutException
except ImportError:
    # No-op if skill missing
    def timeout_guard(s): return lambda f: f
    TimeoutException = Exception

# Configuration
WORK_ROOT = os.path.join(os.path.dirname(SCRIPT_DIR), 'Clients', 'Work')
SUPPORTED_EXTENSIONS = ('.md', '.pdf', '.pptx', '.docx', '.xlsx', '.html')
IGNORE_DIRS = ('.git', 'node_modules', '_temp', 'data', 'incidents')

def get_drive_service():
    creds = authenticate()
    return build('drive', 'v3', http=google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=60)))

def get_sheets_service():
    creds = authenticate()
    return build('sheets', 'v4', http=google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=60)))

def search_drive(service, filename):
    query = f"name = '{filename}' and trashed = false"
    results = service.files().list(
        q=query,
        fields="files(id, name, webViewLink)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    items = results.get('files', [])
    return items[0] if items else None

def upload_to_drive(service, local_path, filename):
    from googleapiclient.http import MediaFileUpload
    
    file_metadata = {'name': filename}
    # Optional: specify a parent folder if known, for now upload to root or search for "Work" folder
    
    media = MediaFileUpload(local_path, resumable=True)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webViewLink',
        supportsAllDrives=True
    ).execute()
    return file

def get_existing_sheet(service, title):
    query = f"name = '{title}' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get('files', [])
    return items[0].get('id') if items else None

def create_new_sheet(service, title):
    spreadsheet = {
        'properties': {
            'title': title
        }
    }
    spreadsheet = service.spreadsheets().create(body=spreadsheet, fields='spreadsheetId').execute()
    return spreadsheet.get('spreadsheetId')

def format_sheet(service, spreadsheet_id):
    # Professional formatting: Bold headers, frozen row, nice colors
    requests = [
        # Bold Headers & Background Color
        {
            "repeatCell": {
                "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True, "fontSize": 11},
                        "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
                        "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True},
                        "horizontalAlignment": "CENTER"
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)"
            }
        },
        # Freeze Top Row
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": 0,
                    "gridProperties": {"frozenRowCount": 1}
                },
                "fields": "gridProperties.frozenRowCount"
            }
        },
        # Auto-resize columns
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": 0,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 6
                }
            }
        }
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={'requests': requests}).execute()

@timeout_guard(300) # 5 minute hard timeout
def index_work_docs(auto_upload=True):
    print("Starting Work document indexing...")
    drive_service = get_drive_service()
    sheets_service = get_sheets_service()
    
    docs = []
    
    for root, dirs, files in os.walk(WORK_ROOT):
        # Filter directories
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        
        # Determine Component (first level sub-folder)
        rel_path = os.path.relpath(root, WORK_ROOT)
        if rel_path == '.':
            component = "General"
        else:
            component = rel_path.split(os.sep)[0]
            
        for file in files:
            if file.lower().endswith(SUPPORTED_EXTENSIONS):
                full_path = os.path.join(root, file)
                # print(f"Processing: {file}...")
                
                # Check Drive
                drive_item = search_drive(drive_service, file)
                
                if not drive_item and auto_upload:
                    print(f"  Uploading {file} to Drive...")
                    try:
                        # For markdowns, use convert_to_docs
                        convert = file.lower().endswith('.md')
                        # Import upload_file from gdrive_manager
                        from gdrive_manager import upload_file
                        drive_id = upload_file(full_path, convert_to_docs=convert)
                        if drive_id:
                            drive_item = drive_service.files().get(fileId=drive_id, fields='id, webViewLink').execute()
                    except Exception as e:
                        print(f"  Error uploading {file}: {e}")
                        drive_item = None
                
                docs.append({
                    'Component': component,
                    'Document Name': file,
                    'Type': os.path.splitext(file)[1].upper().replace('.', ''),
                    'Google Drive Link': drive_item.get('webViewLink') if drive_item else 'N/A',
                    'Local Path': os.path.relpath(full_path, os.path.dirname(WORK_ROOT)),
                    'Status': 'On Drive' if drive_item else 'Local Only'
                })

    # Sort docs by Component
    docs.sort(key=lambda x: (x['Component'], x['Document Name']))
    
    # Write to Sheet
    title = "Work Master Document Index 2026"
    spreadsheet_id = get_existing_sheet(drive_service, title)
    
    if not spreadsheet_id:
        spreadsheet_id = create_new_sheet(sheets_service, title)
        print(f"Created new Sheet: {title} (ID: {spreadsheet_id})")
        format_sheet(sheets_service, spreadsheet_id)
    else:
        print(f"Updating existing Sheet: {title} (ID: {spreadsheet_id})")
    
    headers = ['Component', 'Document Name', 'Type', 'Google Drive Link', 'Local Path', 'Status']
    values = [headers]
    for d in docs:
        link = d['Google Drive Link']
        link_formula = f'=HYPERLINK("{link}", "Open Document")' if link != 'N/A' else 'N/A'
        values.append([
            d['Component'],
            d['Document Name'],
            d['Type'],
            link_formula,
            d['Local Path'],
            d['Status']
        ])
    
    # Clear existing content before update to handle deletions
    sheets_service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range='Sheet1!A1:Z500'
    ).execute()
    
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range='Sheet1!A1',
        valueInputOption='USER_ENTERED',
        body={'values': values}
    ).execute()
    
    print(f"Index complete! View here: https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit")
    return f"Success: Indexed {len(docs)} documents. Link: https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"

if __name__ == "__main__":
    index_work_docs(auto_upload=True)
