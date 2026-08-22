#!/usr/bin/env python3
import os
import json
import datetime
from urllib.request import Request, urlopen
from urllib.parse import urlencode

# Base settings
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TOKEN = os.path.join(BASE_DIR, "token_calendar.json")
WORK_TOKEN = os.path.join(BASE_DIR, ".agent/skills/work-drive-connector/token_calendar_work.json")

def refresh_token(token_file):
    if not os.path.exists(token_file):
        return None
    
    with open(token_file, 'r') as f:
        data = json.load(f)
    
    refresh_url = data.get('token_uri', 'https://oauth2.googleapis.com/token')
    payload = {
        'client_id': data.get('client_id'),
        'client_secret': data.get('client_secret'),
        'refresh_token': data.get('refresh_token'),
        'grant_type': 'refresh_token'
    }
    
    try:
        req = Request(refresh_url, data=urlencode(payload).encode(), method='POST')
        with urlopen(req, timeout=10) as resp:
            res = json.load(resp)
            new_token = res.get('access_token')
            if new_token:
                data['token'] = new_token
                # Add expiry if available
                expires_in = res.get('expires_in', 3600)
                data['expiry'] = (datetime.datetime.now(datetime.timezone.utc) + 
                                datetime.timedelta(seconds=expires_in)).isoformat()
                with open(token_file, 'w') as f:
                    json.dump(data, f)
                return new_token
    except Exception as e:
        print(f"Error refreshing {token_file}: {e}")
        return data.get('token') # Fallback to existing token
    return None

def fetch_events(token, days_back=1, days_forward=7):
    now = datetime.datetime.now(datetime.timezone.utc)
    time_min = (now - datetime.timedelta(days=days_back)).isoformat()
    time_max = (now + datetime.timedelta(days=days_forward)).isoformat()
    
    params = urlencode({
        'timeMin': time_min,
        'timeMax': time_max,
        'singleEvents': 'true',
        'orderBy': 'startTime',
        'maxResults': 50
    })
    
    url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?{params}"
    req = Request(url)
    req.add_header('Authorization', f'Bearer {token}')
    
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.load(resp)
            return data.get('items', [])
    except Exception as e:
        print(f"Error fetching events: {e}")
        return []

def format_markdown(events, profile_name):
    if not events:
        return f"\n### 📅 {profile_name} Calendar\nNo upcoming events found.\n"
    
    lines = [f"\n### 📅 {profile_name} Calendar"]
    
    # Group by date
    by_date = {}
    for e in events:
        start = e.get('start', {}).get('dateTime', e.get('start', {}).get('date', ''))
        date_str = start.split('T')[0] if 'T' in start else start
        if date_str not in by_date:
            by_date[date_str] = []
        by_date[date_str].append(e)
    
    for date_str in sorted(by_date.keys()):
        dt = datetime.datetime.fromisoformat(date_str) if '-' in date_str else None
        header = dt.strftime('%a, %b %d') if dt else date_str
        lines.append(f"\n**{header}**")
        
        for e in by_date[date_str]:
            summary = e.get('summary', '(No Title)')
            start_ts = e.get('start', {}).get('dateTime', '')
            time_str = ""
            if start_ts:
                st = datetime.datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
                time_str = st.strftime('%H:%M') + " "
            
            link = e.get('htmlLink', '')
            if link:
                lines.append(f"- {time_str}[{summary}]({link})")
            else:
                lines.append(f"- {time_str}{summary}")
                
    return "\n".join(lines)

def main():
    import sys
    profile = "default"
    if "--profile" in sys.argv:
        idx = sys.argv.index("--profile")
        if idx + 1 < len(sys.argv):
            profile = sys.argv[idx + 1]
    
    token_file = WORK_TOKEN if profile == "work" else DEFAULT_TOKEN
    token = refresh_token(token_file)
    
    if not token:
        print(f"Error: Could not authenticate for profile '{profile}'")
        return
    
    events = fetch_events(token)
    print(format_markdown(events, profile.capitalize()))

if __name__ == "__main__":
    main()
