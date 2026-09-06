import argparse
import json
import os
import re
import signal
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# Default timeout for API requests
DEFAULT_TIMEOUT = 60 # increased from 30 to allow for larger payloads, but capped by global timeout
TOKEN_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "token.env")

# Global timeout: 180 seconds
def timeout_handler(signum, frame):
    print("[ERROR] Fathom Connector timed out after 180 seconds", file=sys.stderr)
    sys.exit(1)

if os.name != 'nt': # signal.alarm is Unix-only
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)

def load_fathom_token():
    """Load the Fathom API key from the token.env file."""
    if os.path.exists(TOKEN_ENV_PATH):
        with open(TOKEN_ENV_PATH, 'r') as f:
            for line in f:
                if line.startswith('FATHOM_API_KEY='):
                    return line.split('=', 1)[1].strip()
    return os.environ.get('FATHOM_API_KEY')

def make_fathom_request(endpoint, token, method='GET', params=None, data=None):
    """
    Makes a request to the Fathom API using urllib.
    """
    base_url = "https://api.fathom.ai/external/v1"
    url = f"{base_url}{endpoint}"
    
    if params:
        query_string = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url += f"?{query_string}"
    
    headers = {
        "X-Api-Key": token,
        "Accept": "application/json"
    }
    
    encoded_data = None
    if data:
        encoded_data = json.dumps(data).encode('utf-8')
        headers["Content-Type"] = "application/json"

    print(f"[DEBUG] Calling Fathom API: {method} {endpoint}...", file=sys.stderr)
    req = urllib.request.Request(url, headers=headers, data=encoded_data, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f"[ERROR] Fathom API Error: {e.code} - {body}", file=sys.stderr)
        return {"error": e.code, "message": body}
    except Exception as e:
        print(f"[ERROR] Connection Error: {str(e)}", file=sys.stderr)
        return {"error": "connection_error", "message": str(e)}

def list_meetings(token, limit=20, created_after=None, include_all=True):
    """List recent meetings with optional full data."""
    params = {"limit": limit}
    if created_after:
        params["created_after"] = created_after
    
    if include_all:
        params["include_transcript"] = "true"
        params["include_summary"] = "true"
        params["include_action_items"] = "true"
    
    response = make_fathom_request("/meetings", token, params=params)
    if "error" in response:
        return []
    
    # Fathom API returns a list in the 'items' key
    meetings = response.get("items", [])
    print(f"Found {len(meetings)} meetings:", file=sys.stderr)
    for m in meetings:
        mid = m.get("recording_id") or m.get("id")
        title = m.get("title", "Untitled")
        start = m.get("recording_start_time") or m.get("start_at", "Unknown")
        print(f"- [{mid}] {title} ({start})", file=sys.stderr)
    return meetings

def get_meeting(token, meeting_id, action="get"):
    """Retrieve a specific meeting or transcript."""
    if action == "transcript":
        endpoint = f"/recordings/{meeting_id}/transcript"
        return make_fathom_request(endpoint, token)
    else:
        # Try meetings endpoint first
        endpoint = f"/meetings/{meeting_id}"
        res = make_fathom_request(endpoint, token)
        if "error" in res and res["error"] == 404:
            # Fallback to recordings endpoint
            endpoint = f"/recordings/{meeting_id}"
            return make_fathom_request(endpoint, token)
        return res

# --------------------------------------------------------------- share links --
# A fathom.video/calls/<id> URL is the INTERNAL one. It needs a Fathom account and
# a manual approval from the recording owner, which is how Teammate Meer sat blocked
# on the OTO fulfillment demo for a day on 1 Sep 2026 while the owner was asked for
# access in a Slack thread. Every meeting the API returns already carries a
# share_url, a public link that opens with no account and no approval. So the fix
# is to look the share link up before the call URL ever leaves the machine.
#
# There is no per-recording endpoint (both /meetings/<id> and /recordings/<id>
# return 404), so the lookup pages /meetings until it matches. Results are cached
# so the send guard can resolve a link with no network call at all.

SHARE_CACHE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "journal", "state", "fathom_share_links.json")

def load_share_cache():
    try:
        with open(os.path.abspath(SHARE_CACHE)) as f:
            return json.load(f)
    except Exception:
        return {}

def save_share_cache(cache):
    path = os.path.abspath(SHARE_CACHE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        print(f"[WARN] could not write share cache: {e}", file=sys.stderr)

def normalize_ident(ident):
    """A call URL, a call id or a recording id, all reduced to the digits."""
    m = re.search(r"(?:calls|share)/([A-Za-z0-9_-]+)", str(ident))
    if m:
        return m.group(1)
    return str(ident).strip()

def share_link(token, ident, max_pages=20, page_size=50, refresh=False):
    """(share_url, meeting) for a call id, recording id or fathom.video URL."""
    key = normalize_ident(ident)
    cache = load_share_cache()
    if not refresh and key in cache and cache[key].get("share_url"):
        hit = dict(cache[key])
        hit["from_cache"] = True
        return hit["share_url"], hit

    cursor = None
    for _ in range(max_pages):
        params = {"limit": page_size}
        if cursor:
            params["cursor"] = cursor
        res = make_fathom_request("/meetings", token, params=params)
        if "error" in res:
            return None, {"error": res}
        for m in res.get("items", []):
            call_id = normalize_ident(m.get("url") or "")
            rec_id = str(m.get("recording_id") or "")
            if key not in (call_id, rec_id):
                continue
            entry = {
                "share_url": m.get("share_url"),
                "call_url": m.get("url"),
                "recording_id": m.get("recording_id"),
                "title": m.get("meeting_title") or m.get("title"),
                "recorded_at": m.get("recording_start_time"),
            }
            for k in (call_id, rec_id):        # findable by either id
                if k:
                    cache[k] = entry
            save_share_cache(cache)
            return entry["share_url"], entry
        cursor = res.get("next_cursor")
        if not cursor:
            break
    return None, {"error": "not_found",
                  "message": f"no meeting matched {ident} in the last "
                             f"{max_pages * page_size} recordings"}

def main():
    parser = argparse.ArgumentParser(description="Fathom API Connector")
    parser.add_argument("--action", required=True, choices=["list", "get", "transcript", "share-link"], help="Action to perform")
    parser.add_argument("--id", help="Meeting ID for 'get' or 'transcript' action")
    parser.add_argument("--limit", type=int, default=20, help="Limit for listing meetings")
    parser.add_argument("--after", help="Filter meetings created after (ISO 8601)")
    parser.add_argument("--full", action="store_true", help="Include transcript/summary/action items in list")
    parser.add_argument("--refresh", action="store_true", help="share-link: ignore the cache and re-query Fathom")
    parser.add_argument("--max-pages", type=int, default=20, help="share-link: how far back to page (50 recordings per page)")
    
    args = parser.parse_args()
    
    token = load_fathom_token()
    if not token:
        print("Error: FATHOM_API_KEY not found in token.env or environment.", file=sys.stderr)
        sys.exit(1)
        
    if args.action == "list":
        meetings = list_meetings(token, args.limit, args.after, include_all=args.full)
        print(json.dumps(meetings, indent=2))
    elif args.action == "share-link":
        if not args.id:
            print("Error: --id is required for share-link (call id, recording id, or a fathom.video URL).", file=sys.stderr)
            sys.exit(1)
        url, meta = share_link(token, args.id, max_pages=args.max_pages, refresh=args.refresh)
        if not url:
            print(json.dumps(meta, indent=2), file=sys.stderr)
            sys.exit(1)
        src = "cache" if meta.get("from_cache") else "api"
        print(f"[{src}] {meta.get('title')} ({meta.get('recorded_at')})", file=sys.stderr)
        print(url)
    elif args.action in ["get", "transcript"]:
        if not args.id:
            print("Error: --id is required for get/transcript action.", file=sys.stderr)
            sys.exit(1)
        res = get_meeting(token, args.id, action=args.action)
        print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
