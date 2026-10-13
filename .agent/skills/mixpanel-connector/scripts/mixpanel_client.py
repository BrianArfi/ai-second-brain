import argparse
import base64
import hashlib
import json
import os
import signal
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

# Global timeout: 180 seconds
DEFAULT_TIMEOUT = 60
TOKEN_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "token.env")

def timeout_handler(_signum, _frame):
    print("[ERROR] Mixpanel Connector timed out after 180 seconds", file=sys.stderr)
    sys.exit(1)

if os.name != 'nt':
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)

def load_config():
    """Load Mixpanel credentials from token.env."""
    config = {}
    if os.path.exists(TOKEN_ENV_PATH):
        with open(TOKEN_ENV_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    key, val = line.split('=', 1)
                    config[key.strip()] = val.strip()
    # Override with env vars if set
    for key in ['MIXPANEL_PROJECT_TOKEN', 'MIXPANEL_API_SECRET',
                'MIXPANEL_PROJECT_ID', 'MIXPANEL_SERVICE_ACCOUNT_USERNAME',
                'MIXPANEL_SERVICE_ACCOUNT_SECRET']:
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config

def basic_auth_header(username, password):
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {creds}"

def make_query_request(endpoint, config, params=None):
    """
    Makes a request to the Mixpanel Query API.
    Uses Service Account auth if available, else falls back to API Secret.
    Base: https://mixpanel.com/api/2.0/
    """
    base_url = "https://eu.mixpanel.com/api/2.0"
    url = f"{base_url}{endpoint}"

    # Add project_id param
    all_params = {}
    if config.get('MIXPANEL_PROJECT_ID'):
        all_params['project_id'] = config['MIXPANEL_PROJECT_ID']
    if params:
        all_params.update(params)

    if all_params:
        url += '?' + urllib.parse.urlencode(all_params)

    # Auth: prefer service account, else api_secret
    if config.get('MIXPANEL_SERVICE_ACCOUNT_USERNAME') and config.get('MIXPANEL_SERVICE_ACCOUNT_SECRET'):
        auth = basic_auth_header(
            config['MIXPANEL_SERVICE_ACCOUNT_USERNAME'],
            config['MIXPANEL_SERVICE_ACCOUNT_SECRET']
        )
    elif config.get('MIXPANEL_API_SECRET'):
        auth = basic_auth_header(config['MIXPANEL_API_SECRET'], '')
    else:
        print("[ERROR] No auth credentials found. Set service account or API secret.", file=sys.stderr)
        sys.exit(1)

    headers = {
        'Authorization': auth,
        'Accept': 'application/json',
    }

    print(f"[DEBUG] GET {endpoint} params={all_params}", file=sys.stderr)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f"[ERROR] {e.code}: {body}", file=sys.stderr)
        return {"error": e.code, "message": body}
    except Exception as e:
        print(f"[ERROR] {str(e)}", file=sys.stderr)
        return {"error": "connection_error", "message": str(e)}

def make_export_request(config, params=None):
    """
    Raw data export from data.mixpanel.com/api/2.0/export
    Returns newline-delimited JSON.
    """
    base_url = "https://data-eu.mixpanel.com/api/2.0/export"
    all_params = {}
    if config.get('MIXPANEL_PROJECT_ID'):
        all_params['project_id'] = config['MIXPANEL_PROJECT_ID']
    if params:
        all_params.update(params)

    url = base_url + '?' + urllib.parse.urlencode(all_params)

    if config.get('MIXPANEL_SERVICE_ACCOUNT_USERNAME') and config.get('MIXPANEL_SERVICE_ACCOUNT_SECRET'):
        auth = basic_auth_header(
            config['MIXPANEL_SERVICE_ACCOUNT_USERNAME'],
            config['MIXPANEL_SERVICE_ACCOUNT_SECRET']
        )
    elif config.get('MIXPANEL_API_SECRET'):
        auth = basic_auth_header(config['MIXPANEL_API_SECRET'], '')
    else:
        print("[ERROR] No auth credentials found.", file=sys.stderr)
        sys.exit(1)

    headers = {'Authorization': auth}
    print(f"[DEBUG] EXPORT params={all_params}", file=sys.stderr)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            lines = resp.read().decode('utf-8').strip().split('\n')
            events = []
            for line in lines:
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return events
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f"[ERROR] {e.code}: {body}", file=sys.stderr)
        return {"error": e.code, "message": body}
    except Exception as e:
        print(f"[ERROR] {str(e)}", file=sys.stderr)
        return {"error": "connection_error", "message": str(e)}

def track_event(config, event_name, distinct_id, properties=None):
    """
    Track a single event via Mixpanel Ingestion API (import endpoint).
    Uses project token for auth.
    """
    token = config.get('MIXPANEL_PROJECT_TOKEN')
    if not token:
        print("[ERROR] MIXPANEL_PROJECT_TOKEN not set.", file=sys.stderr)
        sys.exit(1)

    ts = int(time.time())
    insert_id = hashlib.md5(f"{event_name}{distinct_id}{ts}".encode()).hexdigest()

    event_props = properties or {}
    event_props['token'] = token
    event_props['distinct_id'] = distinct_id
    event_props['time'] = ts
    event_props['$insert_id'] = insert_id

    payload = json.dumps([{
        "event": event_name,
        "properties": event_props
    }]).encode('utf-8')

    # Use /import for server-side tracking (requires service account or api_secret)
    if config.get('MIXPANEL_SERVICE_ACCOUNT_USERNAME') and config.get('MIXPANEL_SERVICE_ACCOUNT_SECRET'):
        url = f"https://api.mixpanel.com/import?strict=1&project_id={config.get('MIXPANEL_PROJECT_ID','')}"
        auth = basic_auth_header(
            config['MIXPANEL_SERVICE_ACCOUNT_USERNAME'],
            config['MIXPANEL_SERVICE_ACCOUNT_SECRET']
        )
    elif config.get('MIXPANEL_API_SECRET'):
        url = f"https://api.mixpanel.com/import?strict=1"
        auth = basic_auth_header(config['MIXPANEL_API_SECRET'], '')
    else:
        # Fallback: use /track (client-side, no auth needed but less reliable)
        url = "https://api.mixpanel.com/track"
        auth = None

    headers = {'Content-Type': 'application/json'}
    if auth:
        headers['Authorization'] = auth

    print(f"[DEBUG] Tracking event '{event_name}' for '{distinct_id}'", file=sys.stderr)
    req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f"[ERROR] {e.code}: {body}", file=sys.stderr)
        return {"error": e.code, "message": body}
    except Exception as e:
        print(f"[ERROR] {str(e)}", file=sys.stderr)
        return {"error": "connection_error", "message": str(e)}

def set_user_profile(config, distinct_id, properties):
    """
    Set/update a user profile (People analytics).
    """
    token = config.get('MIXPANEL_PROJECT_TOKEN')
    if not token:
        print("[ERROR] MIXPANEL_PROJECT_TOKEN not set.", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps([{
        "$token": token,
        "$distinct_id": distinct_id,
        "$set": properties
    }]).encode('utf-8')

    url = "https://api.mixpanel.com/engage#profile-set"
    headers = {'Content-Type': 'application/json'}
    print(f"[DEBUG] Setting profile for '{distinct_id}'", file=sys.stderr)
    req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        print(f"[ERROR] {e.code}: {body}", file=sys.stderr)
        return {"error": e.code, "message": body}
    except Exception as e:
        print(f"[ERROR] {str(e)}", file=sys.stderr)
        return {"error": "connection_error", "message": str(e)}

def query_events(config, event_names, from_date, to_date, unit='day', interval=1):
    """
    Query event counts over time.
    endpoint: /events/
    """
    params = {
        'event': json.dumps(event_names) if isinstance(event_names, list) else f'["{event_names}"]',
        'type': 'general',
        'unit': unit,
        'interval': interval,
        'from_date': from_date,
        'to_date': to_date,
    }
    return make_query_request('/events/', config, params)

def query_funnel(config, funnel_id, from_date, to_date, unit='day'):
    """
    Query funnel conversion data.
    endpoint: /funnels/
    """
    params = {
        'funnel_id': funnel_id,
        'from_date': from_date,
        'to_date': to_date,
        'unit': unit,
    }
    return make_query_request('/funnels/', config, params)

def list_funnels(config):
    """List all funnels in the project."""
    return make_query_request('/funnels/list/', config)

def query_retention(config, from_date, to_date, born_event=None, retention_type='birth', unit='day', interval=1):
    """
    Query retention data.
    endpoint: /retention/
    """
    params = {
        'from_date': from_date,
        'to_date': to_date,
        'retention_type': retention_type,
        'unit': unit,
        'interval': interval,
    }
    if born_event:
        params['born_event'] = born_event
    return make_query_request('/retention/', config, params)

def query_top_events(config, type='general', limit=10):
    """List top events by volume."""
    params = {'type': type, 'limit': limit}
    return make_query_request('/events/top/', config, params)

def export_events(config, from_date, to_date, event_names=None, where=None, limit=None):
    """
    Export raw event data (newline-delimited JSON).
    """
    params = {
        'from_date': from_date,
        'to_date': to_date,
    }
    if event_names:
        params['event'] = json.dumps(event_names) if isinstance(event_names, list) else f'["{event_names}"]'
    if where:
        params['where'] = where
    if limit:
        params['limit'] = limit
    return make_export_request(config, params)

def main():
    parser = argparse.ArgumentParser(description="Mixpanel API Connector")
    subparsers = parser.add_subparsers(dest='action', required=True)

    # track
    p_track = subparsers.add_parser('track', help='Track an event')
    p_track.add_argument('--event', required=True, help='Event name')
    p_track.add_argument('--id', required=True, help='distinct_id (user identifier)')
    p_track.add_argument('--props', default='{}', help='JSON string of additional properties')

    # people
    p_people = subparsers.add_parser('people', help='Set user profile properties')
    p_people.add_argument('--id', required=True, help='distinct_id')
    p_people.add_argument('--props', required=True, help='JSON string of properties to set')

    # query-events
    p_qe = subparsers.add_parser('query-events', help='Query event counts over time')
    p_qe.add_argument('--events', required=True, help='Comma-separated event names')
    p_qe.add_argument('--from', dest='from_date', required=True, help='Start date (YYYY-MM-DD)')
    p_qe.add_argument('--to', dest='to_date', required=True, help='End date (YYYY-MM-DD)')
    p_qe.add_argument('--unit', default='day', choices=['minute','hour','day','week','month'])
    p_qe.add_argument('--interval', type=int, default=1)

    # top-events
    p_top = subparsers.add_parser('top-events', help='List top events by volume')
    p_top.add_argument('--limit', type=int, default=20)

    # list-funnels
    subparsers.add_parser('list-funnels', help='List all funnels')

    # query-funnel
    p_qf = subparsers.add_parser('query-funnel', help='Query funnel conversion data')
    p_qf.add_argument('--id', required=True, help='Funnel ID')
    p_qf.add_argument('--from', dest='from_date', required=True)
    p_qf.add_argument('--to', dest='to_date', required=True)
    p_qf.add_argument('--unit', default='day', choices=['day','week','month'])

    # retention
    p_ret = subparsers.add_parser('retention', help='Query user retention')
    p_ret.add_argument('--from', dest='from_date', required=True)
    p_ret.add_argument('--to', dest='to_date', required=True)
    p_ret.add_argument('--born-event', help='Event that marks user birth')
    p_ret.add_argument('--unit', default='day', choices=['day','week','month'])

    # export
    p_exp = subparsers.add_parser('export', help='Export raw event data')
    p_exp.add_argument('--from', dest='from_date', required=True)
    p_exp.add_argument('--to', dest='to_date', required=True)
    p_exp.add_argument('--events', help='Comma-separated event names to filter')
    p_exp.add_argument('--where', help='Filter expression (Mixpanel JQL-style)')
    p_exp.add_argument('--limit', type=int, help='Max events to return')

    args = parser.parse_args()
    config = load_config()

    if args.action == 'track':
        props = json.loads(args.props)
        result = track_event(config, args.event, args.id, props)
        print(json.dumps(result, indent=2))

    elif args.action == 'people':
        props = json.loads(args.props)
        result = set_user_profile(config, args.id, props)
        print(json.dumps(result, indent=2))

    elif args.action == 'query-events':
        event_list = [e.strip() for e in args.events.split(',')]
        result = query_events(config, event_list, args.from_date, args.to_date, args.unit, args.interval)
        print(json.dumps(result, indent=2))

    elif args.action == 'top-events':
        result = query_top_events(config, limit=args.limit)
        print(json.dumps(result, indent=2))

    elif args.action == 'list-funnels':
        result = list_funnels(config)
        print(json.dumps(result, indent=2))

    elif args.action == 'query-funnel':
        result = query_funnel(config, args.id, args.from_date, args.to_date, args.unit)
        print(json.dumps(result, indent=2))

    elif args.action == 'retention':
        result = query_retention(config, args.from_date, args.to_date,
                                  born_event=args.born_event, unit=args.unit)
        print(json.dumps(result, indent=2))

    elif args.action == 'export':
        event_list = [e.strip() for e in args.events.split(',')] if args.events else None
        result = export_events(config, args.from_date, args.to_date,
                               event_names=event_list, where=args.where, limit=args.limit)
        print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
