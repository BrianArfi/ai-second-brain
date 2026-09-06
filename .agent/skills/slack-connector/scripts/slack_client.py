import argparse
import http.client
import json
import os
import re
import signal
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SKILL_DIR, '..', '..', '..', '..'))

sys.path.insert(0, os.path.join(REPO_ROOT, '.agent', 'scripts'))
from file_utils import require_send_approval  # Outbound send gate (CLAUDE.md)

# Ensure terminal outputs are always encoded in UTF-8 to prevent Windows UnicodeEncodeError
try:
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr.encoding != 'utf-8':
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# Rate limiting configuration
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 1
DEFAULT_TIMEOUT = 60 # increased from 15 to allow for larger payloads, but capped by global timeout
MAX_THREADS_PER_CHANNEL = 5 # only fetch replies for the first 5 threads

# Shared display-name cache, also written by inbox-hub. Used to seed DM names so
# a failed users.list sweep degrades to slightly stale names instead of raw IDs.
USER_NAMES_CACHE = os.path.join(REPO_ROOT, 'journal', 'state', 'slack_user_names.json')
MAX_REPLIES_PER_THREAD = 20 # limit replies per thread

# Global timeout: 180 seconds
def timeout_handler(signum, frame):
    print("[ERROR] Slack Connector timed out after 180 seconds", file=sys.stderr)
    sys.exit(1)

if os.name != 'nt': # signal.alarm is Unix-only
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)

def make_slack_request(endpoint, token, params=None, retry_count=0):
    """
    Makes a request to the Slack API using urllib (standard library).
    Handles rate limiting with Retry-After header and exponential backoff.
    """
    base_url = "https://slack.com/api/"
    url = base_url + endpoint
    
    if params:
        # Filter out None values
        params = {k: v for k, v in params.items() if v is not None}
        data = urllib.parse.urlencode(params)
        url += "?" + data

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    print(f"[DEBUG] Calling Slack API: {endpoint}...", file=sys.stderr)
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as response:
            response_body = response.read().decode("utf-8")
            data = json.loads(response_body)
            
            if not data.get("ok"):
                error_code = data.get("error", "unknown_error")
                print(f"[DEBUG] Slack API returned error: {error_code}", file=sys.stderr)
                if error_code == "missing_scope":
                    print(f"[ERROR] Missing required scope. Needed for: {endpoint}", file=sys.stderr)
                return data
                
            print(f"[DEBUG] Slack API {endpoint} success.", file=sys.stderr)
            return data
    except urllib.error.HTTPError as e:
        # Handle rate limiting (429 Too Many Requests)
        if e.code == 429:
            if retry_count >= MAX_RETRIES:
                print(f"Rate limit exceeded after {MAX_RETRIES} retries. Giving up.", file=sys.stderr)
                sys.exit(1)
            
            # Check for Retry-After header
            retry_after = e.headers.get("Retry-After")
            if retry_after:
                wait_time = int(retry_after)
                print(f"Rate limited. Waiting {wait_time} seconds (from Retry-After header)...", file=sys.stderr)
            else:
                # Exponential backoff: 1, 2, 4, 8, 16 seconds
                wait_time = BASE_BACKOFF_SECONDS * (2 ** retry_count)
                print(f"Rate limited. Waiting {wait_time} seconds (exponential backoff, attempt {retry_count + 1}/{MAX_RETRIES})...", file=sys.stderr)
            
            time.sleep(wait_time)
            return make_slack_request(endpoint, token, params, retry_count + 1)
        else:
            print(f"HTTP Error: {e.code} - {e.reason}", file=sys.stderr)
            sys.exit(1)
    except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as e:
        # Transient transport failures: truncated body (IncompleteRead), reset
        # connection, DNS blip, read timeout. Slack does this often enough on the
        # large paginated sweeps (users.list, users.conversations) that a single
        # blip used to abort the whole morning harvest. Retry, then degrade.
        if retry_count >= MAX_RETRIES:
            print(f"[ERROR] {endpoint} failed after {MAX_RETRIES} retries: {type(e).__name__}: {e}", file=sys.stderr)
            return {"ok": False, "error": f"transport_error: {type(e).__name__}"}

        wait_time = BASE_BACKOFF_SECONDS * (2 ** retry_count)
        print(
            f"[WARN] {endpoint} transport error ({type(e).__name__}: {e}). "
            f"Retrying in {wait_time}s (attempt {retry_count + 1}/{MAX_RETRIES})...",
            file=sys.stderr,
        )
        time.sleep(wait_time)
        return make_slack_request(endpoint, token, params, retry_count + 1)

def list_all_channels(token):
    """
    Lists all public channels in the workspace.
    """
    channels = []
    cursor = None
    page = 1
    
    while True:
        print(f"[DEBUG] Fetching all public channels page {page}...", file=sys.stderr)
        params = {"limit": 100, "types": "public_channel"}
        if cursor:
            params["cursor"] = cursor
            
        response = make_slack_request("conversations.list", token, params)
        if not response.get("ok"):
            print(f"Error listing all channels: {response.get('error')}", file=sys.stderr)
            break

        channels_in_page = response.get("channels", [])
        channels.extend(channels_in_page)
        print(f"[DEBUG] Received {len(channels_in_page)} channels in page {page} (Total: {len(channels)})", file=sys.stderr)
        
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        page += 1

    print(f"Found {len(channels)} public channels:")
    for channel in channels:
        print(f"- {channel['name']} (ID: {channel['id']})")

def _load_cached_user_names():
    try:
        with open(USER_NAMES_CACHE, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        return cached if isinstance(cached, dict) else {}
    except Exception:
        return {}

def _save_cached_user_names(names):
    try:
        os.makedirs(os.path.dirname(USER_NAMES_CACHE), exist_ok=True)
        tmp = USER_NAMES_CACHE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(names, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, USER_NAMES_CACHE)
    except Exception as e:
        print(f"[WARN] could not refresh user-name cache: {e}", file=sys.stderr)

def _build_user_name_map(token):
    """
    id -> display name, one users.list sweep. Used to give DM conversations a
    readable name, since an im object carries only a user ID.

    DM labels are cosmetic, so this NEVER raises: it seeds from the on-disk
    cache, overlays whatever pages it manages to fetch, and returns. A partial
    or stale map costs a few readable names; letting it throw used to cost the
    entire channel listing and every downstream history fetch.
    """
    names = _load_cached_user_names()
    fetched_any = False
    cursor = None
    try:
        while True:
            params = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            response = make_slack_request("users.list", token, params)
            if not response.get("ok"):
                print(
                    f"[WARN] users.list failed ({response.get('error')}), "
                    f"using {len(names)} cached DM names",
                    file=sys.stderr,
                )
                break
            for u in response.get("members", []):
                profile = u.get("profile", {}) or {}
                label = profile.get("display_name") or profile.get("real_name") or u.get("name") or u["id"]
                names[u["id"]] = label
            fetched_any = True
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except Exception as e:
        print(
            f"[WARN] users.list sweep aborted ({type(e).__name__}: {e}), "
            f"continuing with {len(names)} known DM names",
            file=sys.stderr,
        )

    if fetched_any and names:
        _save_cached_user_names(names)
    return names

def list_joined_channels(token, include_dms=False):
    """
    List conversations the authenticated user is actually IN.

    include_dms also pulls im/mpim. DMs carry no 'name' field, only a user ID,
    so they are labelled dm-<display name> via a users.list map and are
    rendered with a [DM] marker so downstream filters can keep them.
    """
    channels = []
    cursor = None
    page = 1

    types = "public_channel,private_channel"
    if include_dms:
        types += ",im,mpim"

    while True:
        print(f"[DEBUG] Fetching joined channels page {page}...", file=sys.stderr)
        params = {"limit": 100, "types": types}
        if cursor:
            params["cursor"] = cursor

        # users.conversations is the API for "channels I am in"
        response = make_slack_request("users.conversations", token, params)
        if not response.get("ok"):
            print(f"Error listing joined channels: {response.get('error')}", file=sys.stderr)
            break

        channels_in_page = response.get("channels", [])
        channels.extend(channels_in_page)
        print(f"[DEBUG] Received {len(channels_in_page)} channels in page {page} (Total: {len(channels)})", file=sys.stderr)

        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        page += 1

    # Belt and braces: the channel list is the valuable output here, so no
    # failure inside the cosmetic name lookup may reach the caller.
    try:
        user_names = _build_user_name_map(token) if include_dms else {}
    except Exception as e:
        print(f"[WARN] DM name resolution failed ({type(e).__name__}: {e}), using raw IDs", file=sys.stderr)
        user_names = {}

    print(f"Found {len(channels)} joined channels:")
    for channel in channels:
        is_dm = channel.get("is_im") or channel.get("is_mpim")
        if not is_dm:
            print(f"- {channel['name']} (ID: {channel['id']})")
            continue
        # updated is ms epoch; emit seconds so callers can filter dormant DMs
        updated_s = int(channel.get("updated", 0)) // 1000
        if channel.get("is_im"):
            uid = channel.get("user", "")
            label = f"dm-{user_names.get(uid, uid or 'unknown')}"
        else:
            label = channel.get("name", channel["id"])
        print(f"- {label} (ID: {channel['id']}) [DM updated={updated_s}]")

def get_thread_replies(token, channel_id, thread_ts):
    """
    Gets replies for a specific thread.
    """
    replies = []
    cursor = None
    
    while True:
        params = {"channel": channel_id, "ts": thread_ts, "limit": 100}
        if cursor:
            params["cursor"] = cursor
            
        response = make_slack_request("conversations.replies", token, params)
        if not response.get("ok"):
            print(f"Error getting replies for thread {thread_ts}: {response.get('error')}", file=sys.stderr)
            break

        messages = response.get("messages", [])
        replies.extend(messages)
        
        if len(replies) >= MAX_REPLIES_PER_THREAD:
            print(f"  [DEBUG] Thread reply limit reached ({MAX_REPLIES_PER_THREAD}). Stopping.", file=sys.stderr)
            break
        
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
            
    return replies

FULL_OUTPUT = False  # when True, disable the 100-char truncation in history/search prints

def _clip(text):
    """Truncate to 100 chars for display unless --full was passed."""
    if FULL_OUTPUT or len(text) <= 100:
        return text
    return text[:100] + "..."

def get_channel_history(token, channel_id, limit=20, fetch_replies=False):
    """
    Gets history for a channel.
    """
    response = make_slack_request("conversations.history", token, {"channel": channel_id, "limit": limit})
    if not response.get("ok"):
        print(f"Error getting history: {response.get('error')}", file=sys.stderr)
        return

    messages = response.get("messages", [])
    print(f"Last {len(messages)} messages in {channel_id}:")
    threads_fetched = 0
    for msg in messages:
        user = msg.get("user", "Unknown")
        text = msg.get("text", "")
        ts = msg.get("ts", "")
        thread_ts = msg.get("thread_ts")
        reply_count = msg.get("reply_count", 0)
        files = msg.get("files", [])
        
        file_info = ""
        if files:
            file_info = " [FILES: " + ", ".join([f"{f.get('name')} (ID: {f.get('id')})" for f in files]) + "]"
        
        # Basic formatting
        print(f"[{ts}] {user}: {_clip(text)}{file_info}")
        
        if fetch_replies and thread_ts and reply_count > 0:
            if threads_fetched >= MAX_THREADS_PER_CHANNEL:
                print(f"  [Thread] Skipping replies (channel thread limit {MAX_THREADS_PER_CHANNEL} reached)")
                continue

            print(f"  [Thread] Fetching {reply_count} replies...")
            replies = get_thread_replies(token, channel_id, thread_ts)
            threads_fetched += 1
            # Skip the first one as it is the parent message
            for reply in replies[1:]:
                r_user = reply.get("user", "Unknown")
                r_text = reply.get("text", "")
                r_ts = reply.get("ts", "")
                print(f"    [{r_ts}] {r_user}: {_clip(r_text)}")

def list_channel_members(token, channel_id):
    """
    Lists members of a specific channel.
    """
    members = []
    cursor = None
    
    while True:
        params = {"channel": channel_id, "limit": 100}
        if cursor:
            params["cursor"] = cursor
            
        response = make_slack_request("conversations.members", token, params)
        if not response.get("ok"):
            print(f"Error listing members for {channel_id}: {response.get('error')}", file=sys.stderr)
            break

        members.extend(response.get("members", []))
        
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    print(f"Found {len(members)} members in {channel_id}:")
    for member_id in members:
        print(f"- {member_id}")

def search_messages(token, query):
    """
    Searches for messages matching a query.
    """
    response = make_slack_request("search.messages", token, {"query": query, "count": 20})
    if not response.get("ok"):
        print(f"Error searching messages for '{query}': {response.get('error')}", file=sys.stderr)
        return

    messages = response.get("messages", {}).get("matches", [])
    print(f"Found {len(messages)} matches for '{query}':")
    for msg in messages:
        channel = msg.get("channel", {}).get("name", "N/A")
        channel_id = msg.get("channel", {}).get("id", "N/A")
        user = msg.get("user", "N/A")
        text = msg.get("text", "")
        ts = msg.get("ts", "")
        print(f"[{ts}] {user} in #{channel} ({channel_id}): {_clip(text)}")

def list_users(token):
    """
    Lists users in the workspace with pagination.
    """
    users = []
    cursor = None
    
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
            
        response = make_slack_request("users.list", token, params)
        if not response.get("ok"):
            print(f"Error listing users: {response.get('error')}", file=sys.stderr)
            break

        users.extend(response.get("members", []))
        
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    print(f"Found {len(users)} users:")
    for user in users:
        name = user.get("name", "N/A")
        real_name = user.get("real_name", "N/A")
        user_id = user.get("id", "N/A")
        print(f"- {real_name} (@{name}) [ID: {user_id}]")

def get_user_info(token, user_id):
    """
    Gets info for a specific user.
    """
    response = make_slack_request("users.info", token, {"user": user_id})
    if not response.get("ok"):
        print(f"Error getting user info for {user_id}: {response.get('error')}", file=sys.stderr)
        return

    user = response.get("user", {})
    name = user.get("name", "N/A")
    real_name = user.get("real_name", "N/A")
    email = user.get("profile", {}).get("email", "N/A")
    print(f"User {user_id}: {real_name} (@{name})")
    print(f"Email: {email}")

def get_channel_info(token, channel_id):
    """
    Gets info for a specific channel.
    """
    response = make_slack_request("conversations.info", token, {"channel": channel_id})
    if not response.get("ok"):
        print(f"Error getting channel info for {channel_id}: {response.get('error')}", file=sys.stderr)
        return

    channel = response.get("channel", {})
    name = channel.get("name", "N/A")
    purpose = channel.get("purpose", {}).get("value", "N/A")
    topic = channel.get("topic", {}).get("value", "N/A")
    print(f"Channel {channel_id}: #{name}")
    print(f"Purpose: {purpose}")
    print(f"Topic: {topic}")

def get_file_info(token, file_id):
    """
    Gets info for a specific file.
    """
    response = make_slack_request("files.info", token, {"file": file_id})
    if not response.get("ok"):
        print(f"Error getting file info for {file_id}: {response.get('error')}", file=sys.stderr)
        return

    file = response.get("file", {})
    name = file.get("name", "N/A")
    user = file.get("user", "N/A")
    url = file.get("url_private", "N/A")
    print(f"File {file_id}: {name}")
    print(f"Owner: {user}")
    print(f"URL: {url}")

def download_file(token, url, save_path):
    """
    Downloads a file from Slack using the token.
    """
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as response:
            with open(save_path, 'wb') as f:
                f.write(response.read())
            print(f"File downloaded to {save_path}")
            return True
    except Exception as e:
        print(f"Error downloading file: {e}", file=sys.stderr)
        return False

def upload_file(token, channel_id, file_path, initial_comment=None, thread_ts=None):
    """
    Uploads a file to a channel using the modern Slack API (files.getUploadURLExternal).
    """
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    # Step 1: Get upload URL
    params = {
        "filename": file_name,
        "length": str(file_size)
    }
    response = make_slack_request("files.getUploadURLExternal", token, params)
    if not response.get('ok'):
        print(f"Error getting upload URL: {response.get('error')}", file=sys.stderr)
        return False

    upload_url = response.get('upload_url')
    file_id = response.get('file_id')

    # Step 2: Upload to external URL (using PUT as per Slack docs)
    try:
        with open(file_path, 'rb') as f:
            file_data = f.read()
            
        req = urllib.request.Request(upload_url, data=file_data, method='POST')
        with urllib.request.urlopen(req) as upload_res:
            if upload_res.status != 200:
                print(f"Error uploading file data: {upload_res.status}", file=sys.stderr)
                return False
    except Exception as e:
        print(f"Error uploading file data: {e}", file=sys.stderr)
        return False

    # Step 3: Complete upload
    complete_params = {
        "files": json.dumps([{"id": file_id, "title": file_name}]),
        "channel_id": channel_id,
        "initial_comment": initial_comment
    }
    # Without thread_ts the file lands as a loose channel message, detached from
    # the message it belongs to. Pass the parent ts to keep it in the thread.
    if thread_ts:
        complete_params["thread_ts"] = thread_ts
    # completeUploadExternal requires POST with form data
    complete_response = make_slack_request("files.completeUploadExternal", token, complete_params)
    if not complete_response.get('ok'):
        print(f"Error completing upload: {complete_response.get('error')}", file=sys.stderr)
        return False

    print(f"File uploaded successfully: {file_id}")
    return True

def lookup_user_by_name(token, name, channel_id=None):
    """
    Looks up a user ID by their Slack handle (@name) or real name.
    Falls back to channel members if not found in global list.
    """
    search_name = name.lower().lstrip('@')
    
    # 1. Global search
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor: params["cursor"] = cursor
        response = make_slack_request("users.list", token, params)
        if not response.get("ok"): break
        for member in response.get("members", []):
            if member.get("name", "").lower() == search_name or \
               member.get("real_name", "").lower() == search_name or \
               member.get("profile", {}).get("display_name", "").lower() == search_name:
                return member.get("id")
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor: break

    # 2. Fallback: Search channel members (reliable for guest users)
    if channel_id:
        cursor = None
        while True:
            params = {"channel": channel_id, "limit": 100}
            if cursor: params["cursor"] = cursor
            response = make_slack_request("conversations.members", token, params)
            if not response.get("ok"): break
            
            for uid in response.get("members", []):
                u_res = make_slack_request("users.info", token, {"user": uid})
                if u_res.get("ok"):
                    u = u_res.get("user", {})
                    uname = u.get("name", "").lower()
                    ureal = u.get("real_name", "").lower()
                    udisp = u.get("profile", {}).get("display_name", "").lower()
                    if uname == search_name or ureal == search_name or udisp == search_name:
                        return uid
            
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor: break
            
    return None

def warn_shell_mangled_text(text, label="message"):
    """
    Detect the fingerprint of shell variable expansion eating a currency amount.

    On 2026-08-07 a message went to the CPO reading "~,763/yr" and "~0k/yr"
    because it was passed through `bash -c "... --text \"...$1,763...\""`, where
    $1 and $5 expanded to nothing. Slack accepted it happily and nobody noticed
    until it had been sitting in front of three people for two hours.

    The tell is a thousands separator with no leading digit (",763"), or a
    stray "~," / "~0k". Narrow on purpose: this warns, it never blocks, because
    a false positive that stops a legitimate send is worse than the bug.

    Fix when this fires: pass the text with --text-file instead of --text.
    """
    import re

    hits = []
    if re.search(r'(?<![\d.])[,]\d{3}\b', text):
        hits.append('a thousands separator with no digit before it, e.g. ",763" where "$1,763" was meant')
    if re.search(r'~\s*,', text):
        hits.append('a tilde immediately followed by a comma, e.g. "~,763"')

    if hits:
        print("", file=sys.stderr)
        print(f"[WARN] This {label} looks like it lost characters to shell expansion:", file=sys.stderr)
        for h in hits:
            print(f"       - {h}", file=sys.stderr)
        print("       Cause is usually $1 / $5 expanding to nothing inside a double-quoted", file=sys.stderr)
        print("       bash -c. Pass the text with --text-file instead of --text.", file=sys.stderr)
        print("", file=sys.stderr)
    return bool(hits)

SLACK_TEXT_LIMIT = 4000

def check_length(text, allow_split=False, label="message"):
    """
    Refuse a send that Slack would silently break into several messages.

    Slack accepts a long chat.postMessage happily and then splits it at roughly
    4000 characters, choosing the break itself. On 2026-08-19 a 5000 character
    reply to an engineer landed as two messages with the seam falling mid-way
    through a bullet list, and nothing in this script's output said so: it
    printed one "Message posted successfully" and one permalink, for two
    messages. The recipient sees a truncated-looking argument and a second
    notification; the sender sees a clean success.

    So the length check happens here rather than being left to the caller to
    remember. This BLOCKS, because the repo runs defaultMode bypassPermissions
    where an ask never reaches a prompt, and because a split that has already
    been sent can only be fixed by deleting it in front of the recipient.

    Pass allow_split=True (CLI: --allow-split) to send anyway when the split is
    genuinely fine, for example a log dump nobody reads as prose.
    """
    if len(text) <= SLACK_TEXT_LIMIT:
        return True

    if allow_split:
        print(f"[WARN] This {label} is {len(text)} characters and will be split by "
              f"Slack into several messages at a point it chooses. Sending anyway "
              f"because --allow-split was passed.", file=sys.stderr)
        return True

    over = len(text) - SLACK_TEXT_LIMIT
    print("", file=sys.stderr)
    print(f"[BLOCKED] This {label} is {len(text)} characters, {over} over Slack's "
          f"{SLACK_TEXT_LIMIT} limit.", file=sys.stderr)
    print("          Slack will split it and pick the break point itself, which "
          "lands mid-sentence", file=sys.stderr)
    print("          more often than not. Nothing sent.", file=sys.stderr)

    # Offer the last paragraph boundary that fits, so a deliberate split is one
    # copy-paste rather than a hunt through the draft.
    head = text[:SLACK_TEXT_LIMIT]
    boundary = head.rfind("\n\n")
    if boundary > 0:
        line_no = text[:boundary].count("\n") + 1
        preview = text[boundary:boundary + 90].strip().replace("\n", " ")
        print("", file=sys.stderr)
        print(f"          Last paragraph break that fits is at line {line_no}, "
              f"{boundary} characters in:", file=sys.stderr)
        print(f"            ...{preview}...", file=sys.stderr)
        print("          Either cut the draft under the limit, or split it there "
              "yourself and send the", file=sys.stderr)
        print("          second part as a thread reply with --thread-ts.", file=sys.stderr)
    print("", file=sys.stderr)
    print("          To send anyway and accept Slack's own split: --allow-split", file=sys.stderr)
    print("", file=sys.stderr)
    return False

def find_dm(token, user_ids):
    """
    Finds an EXISTING direct message or group DM that holds exactly the given
    user ids, and prints its channel id. Feed that id to `--action post`.

    This reads conversations.list rather than calling conversations.open,
    because the owner's user token carries im:read / mpim:read but not im:write or
    mpim:write. So a conversation that has never been opened in the Slack app
    cannot be created from here: open it once by hand, then this finds it.
    """
    wanted = {u.strip() for u in user_ids.split(",") if u.strip()}
    if not wanted:
        print("Error: --users must name at least one user id.", file=sys.stderr)
        return False

    me = make_slack_request("auth.test", token, {})
    my_id = me.get("user_id") if me.get("ok") else None

    cursor = None
    while True:
        params = {"types": "im,mpim", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        response = make_slack_request("conversations.list", token, params)
        if not response.get("ok"):
            print(f"Error listing conversations: {response.get('error')}", file=sys.stderr)
            return False
        for conv in response.get("channels", []):
            if conv.get("is_im"):
                if wanted == {conv.get("user")}:
                    print(f"DM channel: {conv['id']}")
                    return conv["id"]
                continue
            # A group DM only exposes its members through its generated name.
            members = set()
            member_page = make_slack_request(
                "conversations.members", token, {"channel": conv["id"], "limit": 100}
            )
            if member_page.get("ok"):
                members = set(member_page.get("members", []))
            if my_id:
                members.discard(my_id)
            if members == wanted:
                print(f"Group DM channel: {conv['id']}")
                return conv["id"]
        cursor = (response.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    print(
        "No existing conversation holds exactly those users. Open it once in the "
        "Slack app, then run this again.",
        file=sys.stderr,
    )
    return False

def post_message(token, channel_id, text, thread_ts=None, unfurl=False, allow_split=False):
    """
    Posts a message to a channel (or as a thread reply when thread_ts is set),
    automatically resolving @usernames to <@ID>. Prints the permalink on success.
    """
    import re

    if not check_length(text, allow_split=allow_split, label="message"):
        return False

    warn_shell_mangled_text(text, "message")

    # Self-heal broken mention syntax that slips in from hand-written drafts.
    # 1. HTML-escaped brackets render as literal text and never ping:
    #    &lt;@U123&gt; -> <@U123>  (also #channel and !here/!channel/!subteam)
    text = re.sub(r'&lt;([@#!][A-Z0-9]+(?:\|[^&]*?)?)&gt;', r'<\1>', text)
    text = re.sub(r'&lt;(![a-z]+(?:\^[A-Z0-9]+)?(?:\|[^&]*?)?)&gt;', r'<\1>', text)
    # 2. Legacy "<@ID|Name>" label form often renders literally; Slack shows the
    #    canonical name from a bare "<@ID>", so strip any |label.
    text = re.sub(r'<@([A-Z0-9]+)\|[^>]*>', r'<@\1>', text)

    # Find @username (alphanumeric, dots, underscores, dashes).
    # Negative lookbehind for '<' so already-formed <@USERID> mentions are left
    # alone (otherwise each would trigger a full users.list scan and get mangled).
    mentions = re.findall(r'(?<!<)@([a-zA-Z0-9\._-]+)', text)
    for name in mentions:
        user_id = lookup_user_by_name(token, name, channel_id)
        if user_id:
            text = text.replace(f'@{name}', f'<@{user_id}>')
            print(f"Resolved @{name} to <@{user_id}>")
        else:
            print(f"Warning: Could not resolve @{name}")

    params = {"channel": channel_id, "text": text}
    if thread_ts:
        params["thread_ts"] = thread_ts
    if not unfurl:
        params["unfurl_links"] = "false"
        params["unfurl_media"] = "false"
    response = make_slack_request("chat.postMessage", token, params)
    if not response.get("ok"):
        print(f"Error posting message: {response.get('error')}", file=sys.stderr)
        return False
    print("Message posted successfully")
    ts = response.get("ts")
    ch = response.get("channel", channel_id)
    if ts:
        pl = make_slack_request("chat.getPermalink", token, {"channel": ch, "message_ts": ts})
        if pl.get("ok"):
            print(f"Permalink: {pl.get('permalink')}")
    return True

def update_message(token, channel_id, message_ts, text, unfurl=False, allow_split=False):
    """
    Edits an existing message the owner authored (chat.update, requires the xoxp
    user token). Applies the same mention self-healing as post_message.
    Prints the permalink on success.
    """
    import re

    # An edit is truncated rather than split, which is worse: the tail simply
    # disappears with no second message to hint that anything is missing.
    if not check_length(text, allow_split=allow_split, label="edit"):
        return False

    warn_shell_mangled_text(text, "edit")

    text = re.sub(r'&lt;([@#!][A-Z0-9]+(?:\|[^&]*?)?)&gt;', r'<\1>', text)
    text = re.sub(r'&lt;(![a-z]+(?:\^[A-Z0-9]+)?(?:\|[^&]*?)?)&gt;', r'<\1>', text)
    text = re.sub(r'<@([A-Z0-9]+)\|[^>]*>', r'<@\1>', text)

    mentions = re.findall(r'(?<!<)@([a-zA-Z0-9\._-]+)', text)
    for name in mentions:
        user_id = lookup_user_by_name(token, name, channel_id)
        if user_id:
            text = text.replace(f'@{name}', f'<@{user_id}>')
            print(f"Resolved @{name} to <@{user_id}>")
        else:
            print(f"Warning: Could not resolve @{name}")

    params = {"channel": channel_id, "ts": message_ts, "text": text}
    if not unfurl:
        params["unfurl_links"] = "false"
        params["unfurl_media"] = "false"
    response = make_slack_request("chat.update", token, params)
    if not response.get("ok"):
        print(f"Error updating message: {response.get('error')}", file=sys.stderr)
        return False
    print("Message updated successfully")
    ch = response.get("channel", channel_id)
    ts = response.get("ts", message_ts)
    pl = make_slack_request("chat.getPermalink", token, {"channel": ch, "message_ts": ts})
    if pl.get("ok"):
        print(f"Permalink: {pl.get('permalink')}")
    return True

def delete_message(token, channel_id, message_ts):
    """
    Deletes a message the owner authored (chat.delete, requires the xoxp user
    token — Slack only allows deleting your own messages with a user token,
    admin scope aside).
    """
    params = {"channel": channel_id, "ts": message_ts}
    response = make_slack_request("chat.delete", token, params)
    if not response.get("ok"):
        print(f"Error deleting message: {response.get('error')}", file=sys.stderr)
        return False
    print(f"Message {message_ts} deleted successfully from {channel_id}")
    return True

def invite_user(token, channel_id, user_ids):
    """
    Invites users to a specific channel.
    user_ids: comma-separated list of user IDs.
    """
    params = {"channel": channel_id, "users": user_ids}
    response = make_slack_request("conversations.invite", token, params)
    if not response.get("ok"):
        print(f"Error inviting users: {response.get('error')}", file=sys.stderr)
        return False
    print(f"Users invited successfully to {channel_id}")
    return True

def create_channel(token, name, private=False):
    """
    Creates a channel (conversations.create) and prints its ID.

    Creating a channel is an outward-facing act: the name is visible to the
    whole workspace and it cannot be un-created, only archived. So it carries
    the same --approved gate as post and invite.

    Slack normalises the name itself (lowercase, no spaces), but it rejects
    rather than fixes some inputs, so normalise here and say what was used.
    """
    normalised = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")[:80]
    params = {"name": normalised, "is_private": "true" if private else "false"}
    response = make_slack_request("conversations.create", token, params)
    if not response.get("ok"):
        err = response.get("error")
        if err == "name_taken":
            print(f"Error: a channel named #{normalised} already exists. Use --action lookup or list_channels to find its ID.", file=sys.stderr)
        else:
            print(f"Error creating channel: {err}", file=sys.stderr)
        return None
    ch = response.get("channel", {})
    print(f"Channel created: #{ch.get('name')} ({ch.get('id')})")
    return ch.get("id")

def set_channel_purpose(token, channel_id, purpose=None, topic=None):
    """Sets purpose and/or topic on a channel. Not gated: it writes metadata on
    a channel that already exists and notifies nobody."""
    ok = True
    if purpose:
        r = make_slack_request("conversations.setPurpose", token, {"channel": channel_id, "purpose": purpose})
        if not r.get("ok"):
            print(f"Error setting purpose: {r.get('error')}", file=sys.stderr)
            ok = False
    if topic:
        r = make_slack_request("conversations.setTopic", token, {"channel": channel_id, "topic": topic})
        if not r.get("ok"):
            print(f"Error setting topic: {r.get('error')}", file=sys.stderr)
            ok = False
    return ok

def join_channel(token, channel_id):
    """
    Joins a channel.
    """
    params = {"channel": channel_id}
    response = make_slack_request("conversations.join", token, params)
    if not response.get("ok"):
        print(f"Error joining channel: {response.get('error')}", file=sys.stderr)
        return False
    print(f"Joined channel {channel_id} successfully")
    return True

def leave_channel(token, channel_id):
    """
    Leaves a channel.
    """
    params = {"channel": channel_id}
    response = make_slack_request("conversations.leave", token, params)
    if not response.get("ok"):
        print(f"Error leaving channel: {response.get('error')}", file=sys.stderr)
        return False
    print(f"Left channel {channel_id} successfully")
    return True

def main():
    parser = argparse.ArgumentParser(description="Slack Connector Helper")
    parser.add_argument("--action", required=True, choices=["list_channels", "list_joined_channels", "history", "list_users", "user_info", "channel_members", "search", "channel_info", "file_info", "download", "upload", "post", "update", "delete", "lookup", "find_dm", "invite", "join", "leave", "create_channel", "set_purpose"], help="Action to perform")
    parser.add_argument("--token", help="Explicit Slack token. Default for all actions is SLACK_USER_TOKEN (xoxp, the owner's), falling back to SLACK_BOT_TOKEN. Use --bot to force the bot token.")
    parser.add_argument("--channel", help="Channel ID for history, channel_members, upload, post, lookup, invite, join, and leave actions")
    parser.add_argument("--user", help="User ID/Name for user_info or lookup action")
    parser.add_argument("--users", help="User IDs (comma-separated) for invite and find_dm actions")
    parser.add_argument("--name", help="Channel name for create_channel action")
    parser.add_argument("--private", action="store_true", help="Create a private channel instead of a public one (create_channel)")
    parser.add_argument("--purpose", help="Channel purpose (create_channel, set_purpose)")
    parser.add_argument("--topic", help="Channel topic (create_channel, set_purpose)")
    parser.add_argument("--file", help="File ID for file_info action")
    parser.add_argument("--url", help="URL for download action")
    parser.add_argument("--path", help="Local path for download/upload action")
    parser.add_argument("--text", help="Text for post action")
    parser.add_argument("--comment", help="Comment for upload action")
    parser.add_argument("--query", help="Query for search action")
    parser.add_argument("--limit", type=int, default=20, help="Number of messages to retrieve")
    parser.add_argument("--replies", action="store_true", help="Fetch thread replies in history")
    parser.add_argument("--as-user", dest="as_user", action="store_true", help="Post as the owner using SLACK_USER_TOKEN (no Claude-bot footer). This is the default for the post action.")
    parser.add_argument("--bot", action="store_true", help="Force the bot token (xoxb) for post instead of the user token.")
    parser.add_argument("--thread-ts", dest="thread_ts", help="Parent message ts to reply in-thread (post action).")
    parser.add_argument("--ts", dest="message_ts", help="Timestamp of the message to edit (update action).")
    parser.add_argument("--text-file", dest="text_file", help="Read post text from a file instead of --text (avoids shell escaping).")
    parser.add_argument("--unfurl", action="store_true", help="Enable link/media unfurling on post (default: off).")
    parser.add_argument("--allow-split", dest="allow_split", action="store_true", help="Send a message longer than Slack's 4000-character limit and accept Slack breaking it into several messages at a point it picks. Off by default: the break usually lands mid-sentence.")
    parser.add_argument("--approved", action="store_true", help="Confirm the owner has explicitly approved this specific send before it goes out. Required for post/upload/invite; there is no environment override.")
    parser.add_argument("--full", action="store_true", help="Disable the 100-char truncation in history/search output (print full message text).")
    parser.add_argument("--include-dms", action="store_true", help="list_joined_channels only: also list im/mpim conversations, labelled dm-<name> and marked [DM].")

    args = parser.parse_args()

    global FULL_OUTPUT
    FULL_OUTPUT = args.full

    # Auto-load token.env from the connector directory so SLACK_USER_TOKEN /
    # SLACK_BOT_TOKEN are available without a manual export.
    #
    # token.env carries credentials only. It must never be able to grant
    # authorization, so keys that would relax the send gate are refused here
    # even though the gate itself no longer consults the environment. This
    # keeps a future re-introduction of an env flag from silently becoming
    # writable by a credentials file.
    _TOKEN_ENV_DENYLIST = {"SLACK_SEND_UNATTENDED"}
    _token_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "token.env")
    if os.path.exists(_token_env):
        with open(_token_env) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    if _k in _TOKEN_ENV_DENYLIST:
                        print(
                            f"[WARN] Ignoring '{_k}' in token.env: that file supplies "
                            "credentials only and cannot grant send authorization.",
                            file=sys.stderr,
                        )
                        continue
                    os.environ.setdefault(_k, _v.strip())

    # Token selection. Default to the owner's user token (xoxp) for EVERY action:
    # it is a member of every channel the owner is in and is the only token type
    # Slack allows for search.messages. The bot token (xoxb) is only in a couple
    # of channels and lacks history scope, so reads with it fail with
    # channel_not_found / not_in_channel / not_allowed_token_type. This also keeps
    # sends going out AS the owner (no Claude-bot footer), per the standing rule.
    # --token overrides everything; --bot forces the bot token (e.g. for actions
    # that must run as the app, like join); --as-user is kept for back-compat.
    if args.token:
        token = args.token
    elif args.bot:
        token = os.environ.get("SLACK_BOT_TOKEN")
    else:
        token = os.environ.get("SLACK_USER_TOKEN") or os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print("Error: Slack token not found. Pass --token, or set SLACK_USER_TOKEN / SLACK_BOT_TOKEN (token.env).", file=sys.stderr)
        sys.exit(1)

    if args.action == "list_channels":
        list_all_channels(token)
    elif args.action == "list_joined_channels":
        list_joined_channels(token, include_dms=args.include_dms)
    elif args.action == "history":
        if not args.channel:
            print("Error: --channel is required for history action.", file=sys.stderr)
            sys.exit(1)
        get_channel_history(token, args.channel, args.limit, args.replies)
    elif args.action == "list_users":
        list_users(token)
    elif args.action == "user_info":
        if not args.user:
            print("Error: --user is required for user_info action.", file=sys.stderr)
            sys.exit(1)
        get_user_info(token, args.user)
    elif args.action == "lookup":
        if not args.user:
            print("Error: --user (name) is required for lookup action.", file=sys.stderr)
            sys.exit(1)
        uid = lookup_user_by_name(token, args.user, args.channel)
        if uid:
            print(f"User ID for {args.user}: {uid}")
        else:
            print(f"User {args.user} not found.")
    elif args.action == "find_dm":
        if not args.users:
            print("Error: --users (comma-separated user IDs) is required for find_dm.", file=sys.stderr)
            sys.exit(1)
        if not find_dm(token, args.users):
            sys.exit(1)
    elif args.action == "channel_members":
        if not args.channel:
            print("Error: --channel is required for channel_members action.", file=sys.stderr)
            sys.exit(1)
        list_channel_members(token, args.channel)
    elif args.action == "search":
        if not args.query:
            print("Error: --query is required for search action.", file=sys.stderr)
            sys.exit(1)
        search_messages(token, args.query)
    elif args.action == "channel_info":
        if not args.channel:
            print("Error: --channel is required for channel_info action.", file=sys.stderr)
            sys.exit(1)
        get_channel_info(token, args.channel)
    elif args.action == "file_info":
        if not args.file:
            print("Error: --file is required for file_info action.", file=sys.stderr)
            sys.exit(1)
        get_file_info(token, args.file)
    elif args.action == "download":
        if not args.url or not args.path:
            print("Error: --url and --path are required for download action.", file=sys.stderr)
            sys.exit(1)
        download_file(token, args.url, args.path)
    elif args.action == "upload":
        if not args.channel or not args.path:
            print("Error: --channel and --path are required for upload action.", file=sys.stderr)
            sys.exit(1)
        require_send_approval("upload a file to Slack", args.approved)
        upload_file(token, args.channel, args.path, args.comment, args.thread_ts)
    elif args.action == "post":
        text = args.text
        if args.text_file:
            with open(args.text_file) as _tf:
                text = _tf.read().strip()
        if not args.channel or not text:
            print("Error: --channel and (--text or --text-file) are required for post action.", file=sys.stderr)
            sys.exit(1)
        require_send_approval("post a Slack message", args.approved)
        post_message(token, args.channel, text, thread_ts=args.thread_ts, unfurl=args.unfurl, allow_split=args.allow_split)
    elif args.action == "update":
        text = args.text
        if args.text_file:
            with open(args.text_file) as _tf:
                text = _tf.read().strip()
        if not args.channel or not args.message_ts or not text:
            print("Error: --channel, --ts and (--text or --text-file) are required for update action.", file=sys.stderr)
            sys.exit(1)
        require_send_approval("edit an existing Slack message", args.approved)
        update_message(token, args.channel, args.message_ts, text, unfurl=args.unfurl, allow_split=args.allow_split)
    elif args.action == "delete":
        if not args.channel or not args.message_ts:
            print("Error: --channel and --ts are required for delete action.", file=sys.stderr)
            sys.exit(1)
        require_send_approval("delete an existing Slack message", args.approved)
        delete_message(token, args.channel, args.message_ts)
    elif args.action == "invite":
        if not args.channel or not args.users:
            print("Error: --channel and --users are required for invite action.", file=sys.stderr)
            sys.exit(1)
        require_send_approval("invite users to a Slack channel", args.approved)
        invite_user(token, args.channel, args.users)
    elif args.action == "join":
        if not args.channel:
            print("Error: --channel is required for join action.", file=sys.stderr)
            sys.exit(1)
        join_channel(token, args.channel)
    elif args.action == "create_channel":
        if not args.name:
            print("Error: --name is required for create_channel action.", file=sys.stderr)
            sys.exit(1)
        require_send_approval("create a Slack channel", args.approved)
        cid = create_channel(token, args.name, private=args.private)
        if not cid:
            sys.exit(1)
        if args.purpose or args.topic:
            set_channel_purpose(token, cid, args.purpose, args.topic)
    elif args.action == "set_purpose":
        if not args.channel or not (args.purpose or args.topic):
            print("Error: --channel and --purpose/--topic are required for set_purpose action.", file=sys.stderr)
            sys.exit(1)
        set_channel_purpose(token, args.channel, args.purpose, args.topic)
    elif args.action == "leave":
        if not args.channel:
            print("Error: --channel is required for leave action.", file=sys.stderr)
            sys.exit(1)
        leave_channel(token, args.channel)

if __name__ == "__main__":
    main()
