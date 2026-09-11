import os, sys, argparse, re, json
from googleapiclient.discovery import build
import httplib2
import google_auth_httplib2

# Configure constants
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))
MASTER_LIST_MD = os.path.join(REPO_ROOT, 'Clients', 'Work', 'Marketplace', 'Master_Product_List_Restructured.md')
SHEET_ID = '<YOUR_DRIVE_ID>'

# Add work-drive-connector to path for auth
DRIVE_CONNECTOR_DIR = os.path.join(SCRIPT_DIR, '..', 'work-drive-connector')
if DRIVE_CONNECTOR_DIR not in sys.path:
    sys.path.append(DRIVE_CONNECTOR_DIR)
from gdrive_manager import authenticate

# The two tabs have DIFFERENT column orders. Writing one row shape to both silently
# corrupts whichever tab does not match, so each tab declares its own schema.
#
#   MECE:     L0 | L1: Component | L2: Feature | Documents | Status | PRD Status | L3 | Phase
#   Roadmap:  L1: Component | L2: Feature | L3 | Phase | PRD Status | Documents
#
# match_col is the column holding the component name (L1) for that tab. In MECE
# column A is the product family (L0), not the component, so matching on column A
# never finds anything.
TAB_SCHEMAS = {
    "Master Product List & Breakdown (MECE)": {
        "match_col": 1,
        "l0_col": 0,
        "build_row": lambda ctx, l3: [
            ctx["l0"], ctx["component"], ctx["feature"], ctx["link"],
            ctx["build_status"], ctx["prd_status"], l3, ctx["phase"],
        ],
    },
    "Roadmap Breakdown": {
        "match_col": 0,
        "l0_col": None,
        "build_row": lambda ctx, l3: [
            ctx["component"], ctx["feature"], l3, ctx["phase"],
            ctx["prd_status"], ctx["link"],
        ],
    },
}

def update_markdown(component, feature, details, version, status, prd_url, prd_title, dry_run=False):
    print(f"Updating local MD file: {MASTER_LIST_MD}...")
    if not os.path.exists(MASTER_LIST_MD):
        print(f"Error: {MASTER_LIST_MD} not found.")
        return False
    with open(MASTER_LIST_MD, 'r') as f:
        content = f.read()

    comp_esc = re.escape(component)
    comp_pattern = rf"##+.*?{comp_esc}"
    comp_match = re.search(comp_pattern, content, re.IGNORECASE)
    if not comp_match:
        print(f"Error: Component '{component}' not found in Markdown.")
        return False

    comp_level = len(comp_match.group(0)) - len(comp_match.group(0).lstrip('#'))
    next_section_pattern = rf"\n#{{1,{comp_level}}} [^#]"
    next_section_match = re.search(next_section_pattern, content[comp_match.end():])
    search_limit = comp_match.end() + next_section_match.start() if next_section_match else len(content)
    comp_section = content[comp_match.start():search_limit]

    version_main = version.split('(')[0].strip()
    version_pattern = rf"##+.*?{re.escape(version_main)}"
    version_match = re.search(version_pattern, comp_section, re.IGNORECASE)
    if not version_match:
        print(f"Error: Version '{version_main}' not found in Markdown.")
        return False

    # Row patterns must be line-anchored with [^\n], never `.*?` under re.DOTALL.
    # With DOTALL, `\|.*?\|` matched only the FIRST cell of the first row, so
    # table_content captured a truncated prefix like "| **SAB Image Export** |".
    # The write-back then appended the new row after that prefix and orphaned the
    # rest of the original row onto its own line. Every registration corrupted one
    # more row that way. Repaired 11 Sep 2026, after three rows in the E-commerce
    # Core and B2C tables had to be re-joined by hand.
    table_pattern = r"\| Feature \|[^\n]*\n\|[^\n]*\n((?:\|[^\n]*\n)+)"
    table_match = re.search(table_pattern, comp_section[version_match.end():])
    if not table_match:
        print("Error: Feature table not found in Markdown.")
        return False

    table_content = table_match.group(1)
    feature_esc = re.escape(feature)
    row_pattern = rf"\| \*\*{feature_esc}\*\* \|[^\n]*\|"
    formatted_details = details.replace(';', '<br>')
    new_row = f"| **{feature}** | {formatted_details} | {status} | [{prd_title}]({prd_url}) |"

    if re.search(row_pattern, table_content, re.IGNORECASE):
        updated_table = re.sub(row_pattern, new_row, table_content, flags=re.IGNORECASE)
    else:
        updated_table = table_content.rstrip() + "\n" + new_row + "\n"

    new_comp_section = comp_section[:version_match.end()] + comp_section[version_match.end():].replace(table_content, updated_table)
    new_content = content[:comp_match.start()] + new_comp_section + content[search_limit:]

    if dry_run:
        print(f"  DRY RUN, would write this row into the MD:\n    {new_row}")
        return True

    with open(MASTER_LIST_MD, 'w') as f:
        f.write(new_content)
    print("Markdown update complete.")
    return True

def get_sheets_service():
    creds = authenticate()
    return build('sheets', 'v4', http=google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=60)))

def _norm_cell(v):
    return str(v).strip()

def _norm_row(row):
    """Canonical form of a row for equality checks: strings, stripped, no trailing blanks.

    The Sheets API trims trailing empty cells off existing rows, so a freshly built
    8-column row must be trimmed the same way or it can never compare equal.
    """
    cells = [_norm_cell(c) for c in row]
    while cells and not cells[-1]:
        cells.pop()
    return tuple(cells)

def _find_component(grid, component, match_col, l0_col):
    """Return (found, l0) by matching the component name in that tab's component column."""
    target = component.lower().replace(' ', '').replace('-', '')
    for vals in grid:
        if len(vals) <= match_col:
            continue
        val = _norm_cell(vals[match_col])
        if target and target in val.lower().replace(' ', '').replace('-', ''):
            l0 = ''
            if l0_col is not None and len(vals) > l0_col:
                l0 = _norm_cell(vals[l0_col])
            return True, l0
    return False, ''

# Two sentinels that are never real detail text.
_ITEM_PROBE_A = '\x00__L3_PROBE_A__'
_ITEM_PROBE_B = '\x00__L3_PROBE_B__'

def _row_consumes_item(build_row, ctx):
    """True if this tab's row schema actually places the L3 detail item in the row.

    Splitting --details on ';' is only correct for tabs whose rows differ per item.
    For a schema that drops the item, one row per item is N IDENTICAL rows, which is
    how duplicate blocks get into the sheet. Rather than hardcoding a tab name, probe
    the schema: build the row twice with different sentinels and see if it changes.
    """
    try:
        return build_row(ctx, _ITEM_PROBE_A) != build_row(ctx, _ITEM_PROBE_B)
    except Exception:
        return True

def _update_sheet_tab(service, tab_name, component, feature, details, phase,
                      prd_status, build_status, prd_url, prd_title, dry_run=False):
    print(f"Updating Sheet Tab: \"{tab_name}\"...")
    schema = TAB_SCHEMAS.get(tab_name)
    if not schema:
        print(f"Error: no schema defined for tab '{tab_name}'. Refusing to write.")
        return False

    # FORMULA render so existing hyperlink cells come back as '=HYPERLINK(...)', the
    # same shape build_row emits. Under the default render they come back as display
    # text and no already-written row would ever match, defeating the dedupe below.
    grid = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{tab_name}'!A1:H2000",
        valueRenderOption='FORMULA',
    ).execute().get('values', [])

    found, l0 = _find_component(grid, component, schema["match_col"], schema["l0_col"])
    if not found:
        print(f"Error: component '{component}' not found in tab '{tab_name}' "
              f"(matched against column {chr(65 + schema['match_col'])}). Nothing written.")
        return False

    ctx = {
        "l0": l0,
        "component": component,
        "feature": feature,
        "phase": phase,
        "prd_status": prd_status,
        "build_status": build_status,
        "link": f'=HYPERLINK("{prd_url}", "{prd_title}")',
    }

    items = [d.strip() for d in details.split(';') if d.strip()] or [details.strip()]

    if _row_consumes_item(schema["build_row"], ctx):
        # One row per detail item, matching how every existing block in this tab is laid out.
        new_rows = [schema["build_row"](ctx, item) for item in items]
    else:
        # This tab's row schema drops the detail item, so one row per item would be N
        # byte-identical rows. Emit exactly one, carrying the full detail text.
        new_rows = [schema["build_row"](ctx, "; ".join(items))]
        if len(items) > 1:
            print(f"  Note: '{tab_name}' rows do not carry the L3 detail item; "
                  f"collapsed {len(items)} detail items into 1 row.")

    # Idempotency: re-running the same registration must not append the same rows again.
    # Skip anything already present verbatim, and de-dup within this batch too.
    seen = {_norm_row(r) for r in grid}
    to_append, skipped = [], []
    for r in new_rows:
        key = _norm_row(r)
        if key in seen:
            skipped.append(r)
        else:
            seen.add(key)
            to_append.append(r)

    for r in skipped:
        print(f"  SKIP (already present in '{tab_name}'): "
              + " | ".join(str(x)[:30] for x in r))

    if dry_run:
        print(f"  DRY RUN, would append {len(to_append)} row(s) to '{tab_name}' "
              f"({len(skipped)} skipped as duplicates):")
        for r in to_append:
            print("   ", " | ".join(str(x)[:30] for x in r))
        return True

    if not to_append:
        print(f"Sheet tab '{tab_name}' already up to date; nothing appended.")
        return True

    # Google Sheets "Table" objects reject insertRange ("cannot insert cells over part
    # of a table"), so append at the end rather than inserting mid-section.
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"'{tab_name}'!A1",
        valueInputOption='USER_ENTERED',
        insertDataOption='INSERT_ROWS',
        body={'values': to_append},
    ).execute()
    print(f"Sheet tab '{tab_name}' updated successfully (appended {len(to_append)} row(s), "
          f"skipped {len(skipped)} duplicate(s)).")
    return True

def main():
    parser = argparse.ArgumentParser(
        description="Register a PRD in the Work Master Product List (local MD + both sheet tabs).")
    parser.add_argument('--component', required=True,
                        help="L1 component, e.g. 'E-commerce Front-end Builder'. Must already exist in the sheet.")
    parser.add_argument('--feature', required=True, help="L2 feature name.")
    parser.add_argument('--details', required=True,
                        help="L3 detail items, semicolon-separated. Each becomes its own row.")
    parser.add_argument('--version', required=True,
                        help="Phase, e.g. 'V2 Phase (Q3-Q4 2026: Jul-Dec)'. Must match a phase heading in the MD.")
    parser.add_argument('--prd-status', default=None,
                        help="PRD maturity: Full | Stub | API/Overview | Draft. Default Full.")
    parser.add_argument('--build-status', default='To Do',
                        help="Build state for the MECE tab: To Do | In Progress | Released. Default To Do.")
    parser.add_argument('--status', default=None,
                        help="Deprecated alias for --prd-status, kept for older callers.")
    parser.add_argument('--url', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--dry-run', action='store_true',
                        help="Show what would be written without touching the sheet.")
    args = parser.parse_args()

    prd_status = args.prd_status or args.status or 'Full'

    ok = update_markdown(args.component, args.feature, args.details, args.version,
                    prd_status, args.url, args.title, dry_run=args.dry_run)
    service = get_sheets_service()

    for tab in TAB_SCHEMAS:
        ok &= _update_sheet_tab(service, tab, args.component, args.feature, args.details,
                                args.version, prd_status, args.build_status,
                                args.url, args.title, dry_run=args.dry_run)
    if not ok:
        print("\nOne or more tabs were not updated. Fix the component name and re-run; "
              "nothing was partially written to a tab that failed lookup.")
        sys.exit(1)

if __name__ == "__main__":
    main()
