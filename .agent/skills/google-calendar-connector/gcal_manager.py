#!/usr/bin/env python3
"""
Google Calendar Manager
Provides calendar event listing, search, and sweep capabilities.
"""
import os
import datetime
import argparse
import json
import signal
import secrets
import uuid
from collections import defaultdict
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import sys
# Force UTF-8 stdout
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Global timeout: 180 seconds
def timeout_handler(signum, frame):
    print("[ERROR] Google Calendar Manager timed out after 180 seconds", file=sys.stderr)
    sys.exit(1)

if os.name != 'nt': # signal.alarm is Unix-only
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)

from googleapiclient.discovery import build

# Determine the base directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..'))

# Default locations
DEFAULT_CREDENTIALS = os.path.join(BASE_DIR, 'credentials.json')
DEFAULT_TOKEN = os.path.join(BASE_DIR, 'token_calendar.json')

# Work locations
WORK_DIR = os.path.join(BASE_DIR, '.agent', 'skills', 'work-drive-connector')
WORK_CREDENTIALS = os.path.join(WORK_DIR, 'credentials.json')
WORK_TOKEN = os.path.join(WORK_DIR, 'token_calendar_work.json')

# Secondary client locations (generic: whatever company is currently in use)
SECONDARY_DIR = os.path.join(BASE_DIR, '.agent', 'skills', 'secondary-drive-connector')
SECONDARY_CREDENTIALS = os.path.join(SECONDARY_DIR, 'credentials.json')
SECONDARY_TOKEN = os.path.join(SECONDARY_DIR, 'token_calendar_secondary.json')

# Scopes - calendar.events to create/modify, readonly for fetching
SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/calendar.readonly'
]

def authenticate(profile='default'):
    """Authenticate and return credentials based on profile."""
    if profile == 'work':
        creds_file = WORK_CREDENTIALS
        token_file = WORK_TOKEN
    elif profile == 'secondary':
        creds_file = SECONDARY_CREDENTIALS
        token_file = SECONDARY_TOKEN
    else:
        creds_file = DEFAULT_CREDENTIALS
        token_file = DEFAULT_TOKEN

    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Error refreshing credentials: {e}")
                creds = None # Force re-auth if refresh fails
        
        if not creds or not creds.valid:
            if not os.path.exists(creds_file):
                print(f"Error: {creds_file} not found.")
                return None
            
            if not sys.stdin.isatty():
                # Non-interactive caller (cron, hook, agent session). Blocking on
                # input() here hangs the caller until its timeout and produces no
                # token anyway, so fail fast and point at the two-step flow.
                print(f"Error: {profile} calendar token is missing or its refresh token was revoked, "
                      f"and this is not an interactive terminal.")
                print(f"  Re-authorize without a TTY:")
                print(f"    python3 {os.path.relpath(__file__, BASE_DIR)} auth --profile {profile} --start")
                print(f"    python3 {os.path.relpath(__file__, BASE_DIR)} auth --profile {profile} --code <CODE>")
                return None

            flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
            # Set explicit redirect_uri for manual flow
            flow.redirect_uri = 'http://localhost:8080/'

            # Use console flow instead of local server for headless environments
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')

            print("\n" + "="*60)
            print(f"GOOGLE CALENDAR AUTHENTICATION REQUIRED ({profile.upper()})")
            print("="*60)
            print(f"1. Visit this URL in your browser:\n   {auth_url}")
            print("2. Authorize the application and copy the 'code' parameter from the resulting URL.")
            print("   (The page may fail to load, just copy the 'code=' value from the address bar)")
            print("="*60 + "\n")

            code = input(f"[{profile.upper()} Calendar] Enter the authorization code: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials

        with open(token_file, 'w') as token:
            token.write(creds.to_json())
    return creds

def _profile_paths(profile):
    if profile == 'work':
        return WORK_CREDENTIALS, WORK_TOKEN
    if profile == 'secondary':
        return SECONDARY_CREDENTIALS, SECONDARY_TOKEN
    return DEFAULT_CREDENTIALS, DEFAULT_TOKEN

def _pending_path(token_file):
    return token_file + '.authpending.json'

def auth_start(profile, login_hint=None):
    """Print the authorization URL and persist the PKCE verifier.

    Split from the exchange so a session with no TTY can still re-authorize: the
    verifier lives in a sidecar file instead of on an in-memory flow object.

    login_hint matters here. On 13 Aug 2026 the personal profile was re-authorized
    against the Work account by accident, because the browser was already signed
    into it and the consent screen never asked which account to use. That produced
    a working token for the wrong calendar, which looks identical to success.
    """
    creds_file, token_file = _profile_paths(profile)
    if not os.path.exists(creds_file):
        print(f"Error: {creds_file} not found.")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
    flow.redirect_uri = 'http://localhost:8080/'
    verifier = secrets.token_urlsafe(64)[:128]
    flow.code_verifier = verifier
    extra = {'prompt': 'consent', 'access_type': 'offline'}
    if login_hint:
        extra['login_hint'] = login_hint
    auth_url, state = flow.authorization_url(**extra)

    pending = _pending_path(token_file)
    with open(pending, 'w') as fh:
        json.dump({'code_verifier': verifier, 'state': state,
                   'expected_account': login_hint}, fh)
    os.chmod(pending, 0o600)

    print("=" * 60)
    print(f"AUTHORIZE {profile.upper()} CALENDAR")
    if login_hint:
        print(f"Sign in as: {login_hint}")
    print("=" * 60)
    print("1. Open this URL and approve access:\n")
    print(f"   {auth_url}\n")
    print("2. The browser lands on a localhost page that will not load. That is expected.")
    print("   Copy the whole address bar, or just the code= value.")
    print("3. Finish with:")
    print(f"   python3 {os.path.relpath(__file__, BASE_DIR)} auth --profile {profile} --code '<PASTE>'")
    print("=" * 60)
    return 0

def auth_finish(profile, code):
    """Exchange the authorization code for a token, then prove it works."""
    creds_file, token_file = _profile_paths(profile)
    pending = _pending_path(token_file)
    if not os.path.exists(pending):
        print(f"Error: no pending authorization for '{profile}'. Run --start first.")
        return 1

    with open(pending) as fh:
        saved = json.load(fh)

    code = (code or '').strip().strip('"').strip("'")
    if code.startswith('http'):
        from urllib.parse import urlparse, parse_qs
        code = (parse_qs(urlparse(code).query).get('code') or [''])[0]
    if not code:
        print("Error: no code found in what was pasted.")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(
        creds_file, SCOPES, state=saved.get('state'))
    flow.redirect_uri = 'http://localhost:8080/'
    flow.code_verifier = saved['code_verifier']

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        print(f"Exchange failed: {e}")
        print("Authorization codes are single use and expire in minutes. Run --start again.")
        return 1

    creds = flow.credentials
    if not creds.refresh_token:
        print("Warning: Google returned no refresh token, so this will die again on expiry.")
        print("Run --start again and make sure you approve the consent screen rather than "
              "reusing a previous grant.")

    # Check WHICH account was granted before overwriting anything. A token for the
    # wrong Google account is still a valid token, so this is the only place the
    # mistake is catchable.
    svc = build('calendar', 'v3', credentials=creds)
    primary = svc.calendarList().get(calendarId='primary').execute()
    granted = primary.get('id')
    expected = saved.get('expected_account')

    if expected and granted and granted.lower() != expected.lower():
        print(f"REFUSED: authorized as {granted}, but this profile expects {expected}.")
        print(f"  {token_file} was left untouched.")
        print("  Sign out of the wrong account, or use a private window, then run --start again.")
        os.remove(pending)
        return 1

    with open(token_file, 'w') as fh:
        fh.write(creds.to_json())
    os.chmod(token_file, 0o600)
    os.remove(pending)

    cals = svc.calendarList().list(maxResults=20).execute().get('items', [])
    print(f"Token written: {token_file}")
    print(f"Account: {granted}")
    print(f"Refresh token stored: {bool(creds.refresh_token)}")
    print("Calendars now visible:")
    for c in cals:
        print(f"  - {c.get('summary')}{'  [primary]' if c.get('primary') else ''}")
    return 0

def auth_status():
    """Report token health per profile.

    The personal calendar's refresh token was revoked in June 2026 and nothing
    noticed until August, because every caller passed --profile work and the
    dead profile only failed when someone asked for it by hand.
    """
    rc = 0
    for profile in ('default', 'work', 'secondary'):
        creds_file, token_file = _profile_paths(profile)
        label = f"{profile:<10}"
        if not os.path.exists(token_file):
            print(f"{label} NO TOKEN      {token_file}")
            rc = 1
            continue
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
            creds.refresh(Request())
            svc = build('calendar', 'v3', credentials=creds)
            primary = svc.calendarList().get(calendarId='primary').execute()
            print(f"{label} OK            {primary.get('id')}")
        except Exception as e:
            print(f"{label} DEAD          {str(e)[:90]}")
            print(f"{'':<10} fix: python3 {os.path.relpath(__file__, BASE_DIR)} auth --profile {profile} --start")
            rc = 1
    return rc

def list_events(days_back=7, days_forward=7, profile='default', as_json=False):
    """List calendar events for the specified range.
    as_json=True: emit a clean JSON array on stdout (status goes to stderr) for programmatic callers."""
    creds = authenticate(profile)
    if not creds:
        if as_json:
            print('[]')
        return []

    service = build('calendar', 'v3', credentials=creds)

    # Calculate time range
    now = datetime.datetime.now(datetime.timezone.utc)
    time_min = (now - datetime.timedelta(days=days_back)).isoformat().replace('+00:00', 'Z')
    time_max = (now + datetime.timedelta(days=days_forward)).isoformat().replace('+00:00', 'Z')

    # status line: stderr when emitting JSON so stdout stays parseable
    print(f"Fetching events from {time_min} to {time_max}...", file=sys.stderr if as_json else sys.stdout)

    # The API returns 250 events per page and stops there. A wide window (a
    # month of a busy calendar is ~400 events) therefore came back truncated
    # at the OLD end of the range, so the most recent days looked empty and the
    # work-hours sweep cached them as "no meetings". Follow every page.
    events = []
    page_token = None
    while True:
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime',
            maxResults=2500,
            pageToken=page_token,
        ).execute()
        events.extend(events_result.get('items', []))
        page_token = events_result.get('nextPageToken')
        if not page_token:
            break

    event_list = []
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        summary = event.get('summary', '(No title)')
        event_list.append({
            'id': event.get('id', ''),
            'start': start,
            'end': event['end'].get('dateTime', event['end'].get('date')) if event.get('end') else None,
            'summary': summary,
            # Authoritative cancellation signal. Slack prose is a guess; this is not.
            'status': event.get('status', ''),
            'description': event.get('description', ''),
            'hangoutLink': event.get('hangoutLink', ''),
            'location': event.get('location', ''),
            'attendees': [
                {
                    'email': a.get('email', ''),
                    'displayName': a.get('displayName', ''),
                    'responseStatus': a.get('responseStatus', ''),
                }
                for a in event.get('attendees', [])
            ],
        })
        if not as_json:
            print(f"{start} - {summary}")
            if 'description' in event:
                desc = event['description'].split('\n')[0]
                print(f"  Desc: {desc}")

    if as_json:
        print(json.dumps(event_list))
    elif not events:
        print('No upcoming events found.')
    return event_list

def _parse_event_time(event):
    """Parse event start/end into datetime objects and formatted strings."""
    start_str = event['start'].get('dateTime', event['start'].get('date'))
    end_str = event['end'].get('dateTime', event['end'].get('date'))
    summary = event.get('summary', '(No title)')
    
    # Parse the start time
    if 'T' in start_str:
        # Has time component
        start_dt = datetime.datetime.fromisoformat(start_str)
        end_dt = datetime.datetime.fromisoformat(end_str)
        time_fmt = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
        is_all_day = False
    else:
        start_dt = datetime.datetime.strptime(start_str, '%Y-%m-%d').replace(tzinfo=datetime.timezone.utc)
        end_dt = start_dt
        time_fmt = "All day"
        is_all_day = True
    
    return {
        'start_dt': start_dt,
        'date': start_dt.strftime('%Y-%m-%d'),
        'day_name': start_dt.strftime('%a'),
        'time_range': time_fmt,
        'summary': summary,
        'is_all_day': is_all_day,
    }

def sweep_events(profile='default', output='text'):
    """Sweep calendar events and group by Today / This Week / Last Week."""
    creds = authenticate(profile)
    if not creds:
        return

    service = build('calendar', 'v3', credentials=creds)

    # Calculate ranges: last 7 days + next 7 days
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.strftime('%Y-%m-%d')
    
    # Get start of this week (Monday) and last week
    # Use local-aware "today" for grouping
    today_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    weekday = today_dt.weekday()  # 0=Mon
    this_week_start = today_dt - datetime.timedelta(days=weekday)
    last_week_start = this_week_start - datetime.timedelta(days=7)
    next_week_end = this_week_start + datetime.timedelta(days=14)

    time_min = last_week_start.isoformat().replace('+00:00', 'Z')
    time_max = next_week_end.isoformat().replace('+00:00', 'Z')

    events_result = service.events().list(
        calendarId='primary',
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    raw_events = events_result.get('items', [])

    if not raw_events:
        print(f'No events found for profile: {profile}')
        return

    # Parse and group events
    events = [_parse_event_time(e) for e in raw_events]
    
    today_events = [e for e in events if e['date'] == today]
    this_week_events = [e for e in events if this_week_start.strftime('%Y-%m-%d') <= e['date'] < (this_week_start + datetime.timedelta(days=7)).strftime('%Y-%m-%d')]
    last_week_events = [e for e in events if last_week_start.strftime('%Y-%m-%d') <= e['date'] < this_week_start.strftime('%Y-%m-%d')]

    # Group by day for table views
    def group_by_day(event_list):
        grouped = defaultdict(list)
        for e in event_list:
            grouped[e['date']].append(e)
        return dict(sorted(grouped.items()))

    if output == 'markdown':
        _print_markdown_sweep(profile, today, today_events, 
                               this_week_start, this_week_events,
                               last_week_start, last_week_events,
                               group_by_day)
    else:
        _print_text_sweep(profile, today, today_events,
                          this_week_events, last_week_events, group_by_day)

def _print_markdown_sweep(profile, today, today_events,
                          this_week_start, this_week_events,
                          last_week_start, last_week_events,
                          group_by_day):
    """Print sweep results as structured markdown."""
    profile_label = profile.upper()
    today_dt = datetime.datetime.strptime(today, '%Y-%m-%d')
    today_label = today_dt.strftime('%a, %b %d')
    
    tw_end = this_week_start + datetime.timedelta(days=6)
    lw_end = last_week_start + datetime.timedelta(days=6)
    tw_label = f"{this_week_start.strftime('%b %d')} – {tw_end.strftime('%b %d')}"
    lw_label = f"{last_week_start.strftime('%b %d')} – {lw_end.strftime('%b %d')}"

    print(f"### [{profile_label}] 📅 Calendar Focus")
    print()

    # Today
    print(f"#### 📌 Today ({today_label})")
    if today_events:
        for e in today_events:
            print(f"- {e['time_range']} | **{e['summary']}**")
    else:
        print("- No events scheduled.")
    print()

    # This Week
    print(f"#### ▶️ This Week ({tw_label})")
    tw_grouped = group_by_day(this_week_events)
    if tw_grouped:
        print("| Day | # | Key Meetings |")
        print("|:---|:---|:---|")
        for date_str, evts in tw_grouped.items():
            day_name = evts[0]['day_name']
            marker = " 👈" if date_str == today else ""
            titles = ", ".join(str(e.get('summary') or '(No title)') for e in evts[:3])
            if len(evts) > 3:
                titles = str(titles or "") + f" (+{len(evts)-3} more)"
            print(f"| {day_name}{marker} | {len(evts)} | {titles} |")
    else:
        print("No events this week.")
    print()

    # Last Week
    print(f"#### ◀️ Last Week ({lw_label})")
    lw_grouped = group_by_day(last_week_events)
    if lw_grouped:
        print("| Day | # | Key Meetings |")
        print("|:---|:---|:---|")
        for date_str, evts in lw_grouped.items():
            day_name = evts[0]['day_name']
            titles = ", ".join(str(e.get('summary') or '(No title)') for e in evts[:3])
            if len(evts) > 3:
                titles = str(titles or "") + f" (+{len(evts)-3} more)"
            print(f"| {day_name} | {len(evts)} | {titles} |")
    else:
        print("No events last week.")
    print()

def _print_text_sweep(profile, today, today_events,
                      this_week_events, last_week_events, group_by_day):
    """Print sweep results as plain text."""
    print(f"=== Calendar Sweep ({profile.upper()}) ===")
    print(f"\n--- TODAY ({today}) ---")
    if today_events:
        for e in today_events:
            print(f"  {e['time_range']}  {e['summary']}")
    else:
        print("  No events.")

    print(f"\n--- THIS WEEK ---")
    tw = group_by_day(this_week_events)
    for date_str, evts in tw.items():
        print(f"  {evts[0]['day_name']} ({date_str}): {len(evts)} events")
        for e in evts:
            print(f"    {e['time_range']}  {e['summary']}")

    print(f"\n--- LAST WEEK ---")
    lw = group_by_day(last_week_events)
    for date_str, evts in lw.items():
        print(f"  {evts[0]['day_name']} ({date_str}): {len(evts)} events")
        for e in evts:
            print(f"    {e['time_range']}  {e['summary']}")

# MCP server support (optional — requires: pip install mcp)
try:
    from mcp.server.fastmcp import FastMCP
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

def run_mcp_server(profile='default'):
    """Start an MCP stdio server exposing calendar tools for the given profile."""
    if not MCP_AVAILABLE:
        print("Error: 'mcp' package not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    import io
    from contextlib import redirect_stdout

    server = FastMCP(f"google-calendar-{profile}")

    @server.tool()
    def sweep_calendar() -> str:
        """Get today's events, this week, and last week from the calendar."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            sweep_events(profile, 'text')
        return buf.getvalue()

    @server.tool()
    def list_calendar_events(days_back: int = 7, days_forward: int = 7) -> str:
        """List calendar events within a date range (days relative to today)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            list_events(days_back, days_forward, profile)
        return buf.getvalue()

    @server.tool()
    def create_calendar_event(summary: str, start: str, end: str, description: str = "", attendees: str = "", add_meet: bool = True) -> str:
        """Create a calendar event with a Google Meet link by default. start/end format: YYYY-MM-DDTHH:MM:SS"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            create_event(summary, start, end, description or None, profile, attendees or None, add_meet=add_meet)
        return buf.getvalue()

    server.run(transport="stdio")

def create_event(summary, start_time, end_time, description=None, profile='default', attendees=None, add_meet=True):
    """Create a new calendar event. By default attaches a Google Meet link."""
    creds = authenticate(profile)
    if not creds:
        return

    service = build('calendar', 'v3', credentials=creds)

    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': start_time,
            'timeZone': 'Asia/Jakarta', # Default to Jakarta
        },
        'end': {
            'dateTime': end_time,
            'timeZone': 'Asia/Jakarta',
        },
    }

    if attendees:
        event['attendees'] = [{'email': email.strip()} for email in attendees.split(',')]

    if add_meet:
        event['conferenceData'] = {
            'createRequest': {
                'requestId': uuid.uuid4().hex,
                'conferenceSolutionKey': {'type': 'hangoutsMeet'},
            }
        }

    try:
        event_result = service.events().insert(
            calendarId='primary',
            body=event,
            sendUpdates='all',
            conferenceDataVersion=1 if add_meet else 0,
        ).execute()
        print(f"Event created: {event_result.get('htmlLink')}")
        meet_link = event_result.get('hangoutLink')
        if add_meet:
            if meet_link:
                print(f"Meet link: {meet_link}")
            else:
                print("WARNING: no Meet link returned (account may not allow Meet auto-provisioning).")
        return event_result
    except Exception as e:
        print(f"Error creating event: {e}")
        return None

def update_event(event_id, profile='default', summary=None, start_time=None,
                 end_time=None, description=None, attendees=None, notify=True):
    """Patch an existing event in place. Only the fields passed are touched.

    Kept separate from create_event so that fixing a typo or adding a pre-read
    link never deletes and recreates the event, which would drop existing RSVPs
    and re-notify everyone from scratch.
    """
    creds = authenticate(profile)
    if not creds:
        return None

    service = build('calendar', 'v3', credentials=creds)

    body = {}
    if summary:
        body['summary'] = summary
    if description is not None:
        body['description'] = description
    if start_time:
        body['start'] = {'dateTime': start_time, 'timeZone': 'Asia/Jakarta'}
    if end_time:
        body['end'] = {'dateTime': end_time, 'timeZone': 'Asia/Jakarta'}
    if attendees:
        body['attendees'] = [{'email': e.strip()} for e in attendees.split(',')]

    if not body:
        print("Nothing to update: pass at least one of --summary/--start/--end/--desc/--attendees.")
        return None

    try:
        result = service.events().patch(
            calendarId='primary',
            eventId=event_id,
            body=body,
            sendUpdates='all' if notify else 'none',
        ).execute()
        print(f"Event updated: {result.get('htmlLink')}")
        print(f"Fields changed: {', '.join(sorted(body))}")
        return result
    except Exception as e:
        print(f"Error updating event: {e}")
        return None

def rsvp_event(event_id=None, response='accepted', profile='default', find=None):
    """Answer an invite on someone else's event without touching anyone else's RSVP.

    `update --attendees` replaces the whole attendee list, so using it to answer
    an invite would drop every other guest off an event this account does not
    own. This patches the full list back with only this account's own
    responseStatus changed, which is the only status Google lets a guest set.
    """
    creds = authenticate(profile)
    if not creds:
        return None

    service = build('calendar', 'v3', credentials=creds)

    try:
        me = service.calendarList().get(calendarId='primary').execute().get('id', '').lower()
    except Exception as e:
        print(f"Error resolving this profile's own address: {e}")
        return None
    if not me:
        print("Could not resolve this profile's own address; refusing to guess which attendee is you.")
        return None

    if not event_id:
        if not find:
            print("Pass either --event-id or --find <text in the title>.")
            return None
        now = datetime.datetime.now(datetime.timezone.utc)
        matches = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat().replace('+00:00', 'Z'),
            timeMax=(now + datetime.timedelta(days=30)).isoformat().replace('+00:00', 'Z'),
            singleEvents=True,
            orderBy='startTime',
            q=find,
        ).execute().get('items', [])
        if not matches:
            print(f"No upcoming event in the next 30 days matching '{find}'.")
            return None
        if len(matches) > 1:
            print(f"'{find}' matches {len(matches)} events. Pass --event-id for the one you mean:")
            for m in matches:
                print(f"  {m.get('id')}  {m['start'].get('dateTime', m['start'].get('date'))}  {m.get('summary')}")
            return None
        event_id = matches[0]['id']

    try:
        event = service.events().get(calendarId='primary', eventId=event_id).execute()
    except Exception as e:
        print(f"Error fetching event {event_id}: {e}")
        return None

    attendees = event.get('attendees', [])
    if not any(a.get('email', '').lower() == me for a in attendees):
        print(f"{me} is not on the attendee list of '{event.get('summary')}'. "
              "Refusing to add an attendee: this account was never invited.")
        return None

    patched = []
    for a in attendees:
        entry = dict(a)
        if entry.get('email', '').lower() == me:
            entry['responseStatus'] = response
        patched.append(entry)

    try:
        result = service.events().patch(
            calendarId='primary',
            eventId=event_id,
            body={'attendees': patched},
            sendUpdates='none',
        ).execute()
    except Exception as e:
        print(f"Error answering invite: {e}")
        return None

    final = {a.get('email', ''): a.get('responseStatus', '') for a in result.get('attendees', [])}
    if final.get(me) != response:
        print(f"RSVP did not stick: Google still reports '{final.get(me)}' for {me}.")
        return None

    print(f"RSVP set to {response}: {result.get('summary')} "
          f"({result['start'].get('dateTime', result['start'].get('date'))})")
    print(f"Link: {result.get('htmlLink')}")
    print(f"Attendees now ({len(final)}): " + ", ".join(f"{e}={s}" for e, s in final.items()))
    return result

def main():
    parser = argparse.ArgumentParser(description='Google Calendar Manager')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # List command
    list_parser = subparsers.add_parser('list', help='List calendar events')
    list_parser.add_argument('--days-back', type=int, default=7, help='Days back from now')
    list_parser.add_argument('--days-forward', type=int, default=7, help='Days forward from now')
    list_parser.add_argument('--profile', default='default', choices=['default', 'work', 'secondary'], help='Authentication profile to use')
    list_parser.add_argument('--json', action='store_true', help='Output in JSON format')

    # Sweep command
    sweep_parser = subparsers.add_parser('sweep', help='Sweep calendar: Today / This Week / Last Week')
    sweep_parser.add_argument('--profile', default='default', choices=['default', 'work', 'secondary'], help='Authentication profile to use')
    sweep_parser.add_argument('--output', default='text', choices=['text', 'markdown'], help='Output format')

    # MCP server command
    mcp_parser = subparsers.add_parser('mcp', help='Start MCP stdio server for Claude Desktop')
    mcp_parser.add_argument('--profile', default='default', choices=['default', 'work', 'secondary'], help='Calendar profile to expose')

    # Create command
    create_parser = subparsers.add_parser('create', help='Create a new event')
    create_parser.add_argument('--summary', required=True, help='Event title')
    create_parser.add_argument('--start', required=True, help='Start time (ISO format: YYYY-MM-DDTHH:MM:SS)')
    create_parser.add_argument('--end', required=True, help='End time (ISO format: YYYY-MM-DDTHH:MM:SS)')
    create_parser.add_argument('--attendees', help='Comma-separated emails of attendees')
    create_parser.add_argument('--desc', help='Description')
    create_parser.add_argument('--no-meet', action='store_true', help='Do NOT attach a Google Meet link (default: attach)')
    create_parser.add_argument('--profile', default='default', choices=['default', 'work', 'secondary'], help='Authentication profile to use')

    # Update command
    update_parser = subparsers.add_parser('update', help='Patch an existing event in place')
    update_parser.add_argument('--event-id', required=True, help='Event ID (decode it from the calendar eid if needed)')
    update_parser.add_argument('--summary', help='New title')
    update_parser.add_argument('--start', help='New start (ISO: YYYY-MM-DDTHH:MM:SS)')
    update_parser.add_argument('--end', help='New end (ISO: YYYY-MM-DDTHH:MM:SS)')
    update_parser.add_argument('--attendees', help='Comma-separated emails, REPLACES the current list')
    update_parser.add_argument('--desc', help='New description')
    update_parser.add_argument('--no-notify', action='store_true', help='Do NOT email attendees about the change')
    update_parser.add_argument('--profile', default='default', choices=['default', 'work', 'secondary'], help='Authentication profile to use')

    # RSVP command: answer an invite. Never use `update --attendees` for this;
    # it replaces the guest list instead of changing one response.
    rsvp_parser = subparsers.add_parser('rsvp', help="Answer an invite (accept/decline/tentative)")
    rsvp_parser.add_argument('--event-id', help='Event ID (decode it from the calendar eid if needed)')
    rsvp_parser.add_argument('--find', help='Instead of --event-id: text in the title of an upcoming event')
    rsvp_parser.add_argument('--response', default='accepted',
                             choices=['accepted', 'declined', 'tentative'], help='Your answer')
    rsvp_parser.add_argument('--profile', default='default', choices=['default', 'work', 'secondary'], help='Authentication profile to use')

    # Auth command: two-step OAuth so a session with no TTY can re-authorize
    auth_parser = subparsers.add_parser('auth', help='Re-authorize a profile without an interactive prompt')
    auth_parser.add_argument('--profile', default='default', choices=['default', 'work', 'secondary'], help='Profile to authorize')
    auth_parser.add_argument('--start', action='store_true', help='Print the authorization URL')
    auth_parser.add_argument('--login-hint', help='Google account to sign in as, e.g. you@example.com. Also enforced at exchange time.')
    auth_parser.add_argument('--code', help='Authorization code, or the full localhost redirect URL')
    auth_parser.add_argument('--status', action='store_true', help='Report token health for every profile')

    args = parser.parse_args()

    if args.command == 'auth':
        if args.status:
            sys.exit(auth_status())
        if args.start:
            sys.exit(auth_start(args.profile, args.login_hint))
        if args.code:
            sys.exit(auth_finish(args.profile, args.code))
        auth_parser.print_help()
        sys.exit(1)
    elif args.command == 'list':
        # as_json: list_events emits a clean JSON array (text suppressed, status to stderr)
        list_events(args.days_back, args.days_forward, args.profile, as_json=args.json)
    elif args.command == 'sweep':
        sweep_events(args.profile, args.output)
    elif args.command == 'mcp':
        run_mcp_server(args.profile)
    elif args.command == 'create':
        create_event(args.summary, args.start, args.end, args.desc, args.profile, args.attendees, add_meet=not args.no_meet)
    elif args.command == 'update':
        update_event(args.event_id, args.profile, args.summary, args.start,
                     args.end, args.desc, args.attendees, notify=not args.no_notify)
    elif args.command == 'rsvp':
        rsvp_event(args.event_id, args.response, args.profile, args.find)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
