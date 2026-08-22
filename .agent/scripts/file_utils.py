import os
import sys
import argparse
import glob
import time
from datetime import datetime, timedelta
import fnmatch
import platform

def assert_drive_result(result, operation, id_keys=('id', 'documentId', 'fileId', 'spreadsheetId')):
    """
    Shared Drive Operation Verification (CLAUDE.md rule): confirm a Drive/Docs
    write actually produced a file or document identifier before the caller
    reports success. Call this right before printing the success line, in
    place of exiting 0 on an unchecked API response.

    `result` accepts three shapes so every writer can pass whatever it
    already has on hand:
      - a dict/API response, checked for any key in `id_keys`
      - a plain id (string/int) the caller already knows is correct
      - a falsy value (None, {}, '', 0, False), always treated as failure

    On success returns the id that was found (or the passed-through value).
    On failure prints a clear message to stderr and exits nonzero. This is
    the single source of truth for the check; it never returns on failure,
    so callers do not need to branch on the return value unless they want
    the id for logging.
    """
    found = None
    if isinstance(result, dict):
        for key in id_keys:
            val = result.get(key)
            if val:
                found = val
                break
    elif result:
        found = result

    if not found:
        print(
            f"[ERROR] Drive Operation Verification failed for '{operation}': "
            "no file/document ID in the result. Treat this as a FAILURE, not "
            "a success. Verify with a search before assuming the write "
            "happened.",
            file=sys.stderr,
        )
        sys.exit(1)
    return found

def require_send_approval(action_label, approved):
    """
    Shared outbound-send gate (CLAUDE.md Slack rule): every action that posts a
    message, uploads a file, or otherwise mutates outbound state must call this
    BEFORE any network request is issued for that action. Import it rather than
    copying it, so a connector cannot drift into shipping an ungated twin.

    Approval is per invocation and explicit: the caller passes `approved=True`
    only once the owner has signed off on that specific draft. There is deliberately
    NO environment-variable escape hatch. An env flag is process-wide and
    permanent, so exporting it once into a shell or cron environment would
    silently un-gate every later send in that process tree, which is the
    opposite of the per-message approval the rule requires. Genuinely unattended
    callers pass approval explicitly at their own call site (see
    dashboard/server.py, which passes --approved on the click path).

    Refuses (message to stderr, exit nonzero) otherwise; never returns on
    refusal, so callers do not need to branch on a return value.
    """
    if approved:
        return
    print(
        f"Error: refusing to {action_label} without approval. This action sends "
        "to Slack and needs the owner's explicit sign-off before it hits the network. "
        "Pass --approved once the owner has approved this specific draft. Do not "
        "retry with --approved on a guess.",
        file=sys.stderr,
    )
    sys.exit(1)

WORK_DOMAIN = 'yourcompany.com'
VISIBILITY_CHOICES = ('domain', 'public', 'private')

def apply_visibility(service, file_id, visibility='domain', domain=WORK_DOMAIN):
    """
    Shared Drive sharing control (CLAUDE.md Google Workspace LANDMINE). Import it
    rather than copying it, for the same reason as require_send_approval: one
    implementation means a connector cannot drift into shipping a twin that
    quietly publishes.

    Every writer used to call a local set_commenter_permission() that added
    {'type': 'anyone', 'role': 'commenter'} unconditionally, so every upload AND
    every subsequent update re-published the file to anyone with the link. Two
    of the connectors even had a --share flag that was dead code, because the
    publish happened whether or not it was passed. That is how the Jira ExampleVendor
    recommendation sat public for a day on 5 Aug 2026, and why restricting after
    the fact had to be done by hand after every single write.

    Levels:
      domain   grant <domain> commenter, and REVOKE any existing anyone-with-link.
               The default, because client work should be visible to the client's
               org and nobody else.
      public   grant anyone-with-link commenter. Opt in explicitly (--share or
               --visibility public) for genuinely published artifacts.
      private  owner only. Revokes anyone-with-link, adds nothing.

    Demotion matters as much as the default: an update to an already-public file
    has to actively strip the public grant, or the file stays public forever
    because the first upload made it so.

    Never raises. Sharing is not the caller's primary operation, and a permission
    hiccup should not lose a completed upload. Failures print a WARNING loudly
    enough to act on.
    """
    if visibility not in VISIBILITY_CHOICES:
        print(f"WARNING: unknown visibility {visibility!r}, falling back to 'domain'.")
        visibility = 'domain'

    if visibility != 'public':
        _revoke_public(service, file_id)

    if visibility == 'private':
        print("Visibility: private (owner only).")
        return

    if visibility == 'public':
        body = {'type': 'anyone', 'role': 'commenter'}
        label = "PUBLIC - anyone with the link can comment"
    else:
        body = {'type': 'domain', 'role': 'commenter', 'domain': domain}
        label = f"{domain} can comment"

    try:
        service.permissions().create(
            fileId=file_id, body=body, fields='id', supportsAllDrives=True
        ).execute()
        print(f"Visibility: {label}.")
    except Exception as exc:
        # A duplicate domain grant is the normal case on re-update, not a problem.
        if 'duplicate' in str(exc).lower() or 'already' in str(exc).lower():
            print(f"Visibility: {label} (already granted).")
        else:
            print(f"WARNING: failed to set visibility to {visibility}: {exc}")

def _revoke_public(service, file_id):
    """Remove any anyone-with-link grant. Used by every non-public visibility."""
    try:
        perms = service.permissions().list(
            fileId=file_id, fields='permissions(id,type,role)', supportsAllDrives=True
        ).execute().get('permissions', [])
    except Exception as exc:
        print(f"WARNING: could not check for public access on {file_id}: {exc}")
        print("         Verify sharing by hand before sending this link.")
        return
    for perm in perms:
        if perm.get('type') != 'anyone':
            continue
        try:
            service.permissions().delete(
                fileId=file_id, permissionId=perm['id'], supportsAllDrives=True
            ).execute()
            print("Revoked pre-existing anyone-with-link access.")
        except Exception as exc:
            print(f"WARNING: failed to revoke public access on {file_id}: {exc}")
            print("         This file is still PUBLIC. Fix before sharing the link.")

def add_visibility_arg(parser, default='domain'):
    """Attach the standard --visibility/--share pair to a writer subcommand.

    default='domain' for work/client accounts that have a Workspace domain to
    grant to; default='private' for personal accounts, where there is no domain
    and the only alternative to private would be public.
    """
    parser.add_argument(
        '--visibility', choices=list(VISIBILITY_CHOICES), default=default,
        help=f"Sharing level (default: {default}). "
             "Use 'public' only for genuinely published artifacts.")
    parser.add_argument(
        '--share', action='store_true',
        help="Back-compat alias for --visibility public. Publishes to anyone with the link.")

def resolve_visibility(args, default='domain'):
    """--share wins if passed, else --visibility, else the safe default."""
    if getattr(args, 'share', False):
        return 'public'
    return getattr(args, 'visibility', default) or default

def get_creation_time(path):
    """
    Try to get the creation time. 
    On Windows, os.path.getctime is creation time.
    On Unix/Mac, os.stat().st_birthtime is birth time (if available), 
    otherwise st_ctime is metadata change time (not creation).
    """
    if platform.system() == 'Windows':
        return os.path.getctime(path)
    else:
        stat = os.stat(path)
        try:
            return stat.st_birthtime
        except AttributeError:
            # Fallback for Linux/Unix where birthtime isn't standard
            # We return modification time as a fallback if creation isn't available
            return stat.st_mtime

def is_excluded(path, exclude_dirs):
    for exclude in exclude_dirs:
        if exclude in path.split(os.sep):
            return True
    return False

def find_recent(base_dir, hours, mode='modified', exclude_dirs=None, limit=None):
    if exclude_dirs is None:
        exclude_dirs = ['.git', '.DS_Store', 'node_modules']
    
    matches = []
    cutoff_time = time.time() - (hours * 3600)
    
    for root, dirs, files in os.walk(base_dir):
        # Modify dirs in-place to skip excluded directories
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            if file in exclude_dirs:
                continue
                
            full_path = os.path.join(root, file)
            try:
                if mode == 'modified':
                    file_time = os.path.getmtime(full_path)
                elif mode == 'created':
                    file_time = get_creation_time(full_path)
                else:
                    continue
                
                if file_time > cutoff_time:
                    matches.append((full_path, file_time))
            except OSError:
                continue

    # Sort by time descending (newest first)
    matches.sort(key=lambda x: x[1], reverse=True)
    
    if limit:
        matches = matches[:limit]
        
    for path, timestamp in matches:
        dt = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{dt}] {path}")

def find_by_pattern(base_dir, patterns, exclude_dirs=None, limit=None):
    if exclude_dirs is None:
        exclude_dirs = ['.git', '.DS_Store', 'node_modules']
    
    matches = []
    
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            for pattern in patterns:
                if fnmatch.fnmatch(file, pattern):
                    matches.append(os.path.join(root, file))
                    break # Matched one pattern, move to next file
    
    if limit:
        matches = matches[:limit]
        
    for path in matches:
        print(path)

def main():
    parser = argparse.ArgumentParser(description="Cross-platform file utility.")
    parser.add_argument("--action", choices=['recent_modified', 'recent_created', 'find'], required=True, help="Action to perform")
    parser.add_argument("--dir", required=True, help="Base directory to search")
    parser.add_argument("--hours", type=int, default=24, help="Hours lookback for recent actions")
    parser.add_argument("--patterns", nargs='*', help="File patterns to match (e.g. *.md)")
    parser.add_argument("--exclude", nargs='*', default=['.git', '.DS_Store', 'node_modules'], help="Directories to exclude")
    parser.add_argument("--limit", type=int, default=50, help="Max results to return")

    args = parser.parse_args()

    if args.action == 'recent_modified':
        find_recent(args.dir, args.hours, mode='modified', exclude_dirs=args.exclude, limit=args.limit)
    elif args.action == 'recent_created':
        find_recent(args.dir, args.hours, mode='created', exclude_dirs=args.exclude, limit=args.limit)
    elif args.action == 'find':
        if not args.patterns:
            print("Error: --patterns required for find action")
            return
        find_by_pattern(args.dir, args.patterns, exclude_dirs=args.exclude, limit=args.limit)

if __name__ == "__main__":
    main()
