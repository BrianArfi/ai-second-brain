#!/usr/bin/env python3
"""Re-sync an OAuth client secret into every token minted by that client.

Google user tokens carry a copy of client_id + client_secret. When the secret
is reset in the Cloud console, every token still holds the old secret and fails
with `invalid_client: The provided client secret is invalid.`, even though its
refresh token is still good. Re-consenting every connector is not needed:
copy the new secret into each token and refresh.

Usage:
  python3 .agent/scripts/oauth_client_resync.py check  [--credentials PATH]
  python3 .agent/scripts/oauth_client_resync.py apply  [--credentials PATH]
  python3 .agent/scripts/oauth_client_resync.py reauth-url
  python3 .agent/scripts/oauth_client_resync.py reauth-save --url '<redirected localhost URL>'

If `apply` still fails with `invalid_grant`, the secret reset also revoked the
refresh tokens. `reauth-url` builds ONE consent URL covering the union of every
token's scopes; `reauth-save` writes the new grant into all of them.

Default credentials: .agent/skills/work-drive-connector/credentials.json
(Work client, project gdrive-mcp-486112). `apply` backs up each token it
changes to <token>.bak-<timestamp> before writing, then test-refreshes it.
Never prints a secret.
"""
import argparse
import json
import os
import shutil
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DEFAULT_CREDS = os.path.join(REPO, '.agent', 'skills', 'work-drive-connector', 'credentials.json')
SKIP_DIRS = {'node_modules', '.git', 'venv', '.venv', '__pycache__'}

def load_client(path):
    with open(path) as f:
        d = json.load(f)
    c = d.get('installed') or d.get('web')
    if not c or 'client_id' not in c or 'client_secret' not in c:
        sys.exit(f'{path}: not an OAuth client JSON (no installed/web block)')
    return c['client_id'], c['client_secret']

def find_tokens(client_id):
    # Tokens live under .agent/skills/ plus a few at the repo root.
    candidates = [os.path.join(REPO, n) for n in os.listdir(REPO)]
    for dp, dn, fn in os.walk(os.path.join(REPO, '.agent', 'skills')):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        candidates += [os.path.join(dp, n) for n in fn]
    for p in candidates:
        n = os.path.basename(p)
        if not os.path.isfile(p) or not n.endswith('.json') or '.bak-' in n:
            continue
        try:
            with open(p) as f:
                d = json.load(f)
        except Exception:
            continue
        if isinstance(d, dict) and d.get('client_id') == client_id and d.get('refresh_token'):
            yield p, d

def refresh(d):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    c = Credentials.from_authorized_user_info(d)
    c.refresh(Request())
    return c

STATE_FILE = os.path.join(REPO, '.agent', 'skills', 'work-drive-connector', '.reauth_all_state.json')
REDIRECT = 'http://localhost:8080/'

def _flow(creds_path, scopes, verifier=None):
    from google_auth_oauthlib.flow import Flow
    f = Flow.from_client_secrets_file(creds_path, scopes=scopes, code_verifier=verifier)
    f.redirect_uri = REDIRECT
    return f

def reauth_url(creds_path, client_id):
    tokens = sorted(set(p for p, _ in find_tokens(client_id)))
    scopes = sorted({sc for p in tokens for sc in (json.load(open(p)).get('scopes') or [])})
    f = _flow(creds_path, scopes)
    f.autogenerate_code_verifier = True
    url, _ = f.authorization_url(prompt='consent', access_type='offline')
    with open(STATE_FILE, 'w') as s:
        json.dump({'code_verifier': f.code_verifier, 'scopes': scopes, 'tokens': tokens}, s)
    print(f'{len(tokens)} tokens, {len(scopes)} scopes')
    print(url)

def reauth_save(creds_path, url):
    from urllib.parse import urlparse, parse_qs
    st = json.load(open(STATE_FILE))
    code = parse_qs(urlparse(url).query).get('code', [url])[0]
    f = _flow(creds_path, st['scopes'], st['code_verifier'])
    os.environ.setdefault('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')
    f.fetch_token(code=code)
    data = json.loads(f.credentials.to_json())
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for p in st['tokens']:
        shutil.copy2(p, f'{p}.bak-{stamp}')
        with open(p, 'w') as t:
            json.dump(data, t)
        print(f'written {os.path.relpath(p, REPO)}')
    os.remove(STATE_FILE)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['check', 'apply', 'reauth-url', 'reauth-save'])
    ap.add_argument('--credentials', default=DEFAULT_CREDS)
    ap.add_argument('--url', help='redirected localhost URL (or bare code) for reauth-save')
    a = ap.parse_args()

    client_id, secret = load_client(a.credentials)
    if a.action == 'reauth-url':
        return reauth_url(a.credentials, client_id)
    if a.action == 'reauth-save':
        reauth_save(a.credentials, a.url)
        a.action = 'check'
    print(f'client {client_id.split("-")[0]} from {os.path.relpath(a.credentials, REPO)}')
    tokens = sorted(set((p for p, _ in find_tokens(client_id))))
    if not tokens:
        print('no tokens found for this client')
        return 1
    bad = 0
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for p in tokens:
        rel = os.path.relpath(p, REPO)
        with open(p) as f:
            d = json.load(f)
        stale = d.get('client_secret') != secret
        if a.action == 'apply' and stale:
            shutil.copy2(p, f'{p}.bak-{stamp}')
            d['client_secret'] = secret
        try:
            c = refresh(d)
            if a.action == 'apply':
                d['token'] = c.token
                if c.expiry:
                    d['expiry'] = c.expiry.isoformat() + 'Z'
                with open(p, 'w') as f:
                    json.dump(d, f)
            print(f'OK    {rel}{"  (secret updated)" if stale and a.action == "apply" else ""}')
        except Exception as e:
            bad += 1
            msg = str(e).split(',')[0][:110]
            hint = '  -> secret in this token differs from credentials' if stale and a.action == 'check' else ''
            print(f'FAIL  {rel}: {msg}{hint}')
    print(f'{len(tokens) - bad}/{len(tokens)} tokens refresh')
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
