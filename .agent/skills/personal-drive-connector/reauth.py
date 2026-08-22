"""Two-step Google Drive OAuth for the personal connector.

Port 8080 is occupied by the whatsapp-bridge sidecar, so the automatic
loopback flow cannot bind. This does the same thing by hand, persisting the
PKCE verifier between the two steps so they can run as separate processes.

  python3 asb_drive_auth.py url            -> prints the consent URL
  python3 asb_drive_auth.py code <CODE>    -> exchanges it, writes token.json
"""

import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

from google_auth_oauthlib.flow import InstalledAppFlow

BASE = os.path.expanduser(
    "~/product-second-brain/.agent/skills/personal-drive-connector"
)
SCOPES = ["https://www.googleapis.com/auth/drive"]
REDIRECT = "http://localhost:8080/"
STASH = "/tmp/asb_drive_auth_stash.json"

def build_flow():
    flow = InstalledAppFlow.from_client_secrets_file(
        os.path.join(BASE, "credentials.json"), SCOPES
    )
    flow.redirect_uri = REDIRECT
    return flow

def cmd_url():
    flow = build_flow()
    url, state = flow.authorization_url(prompt="consent", access_type="offline")
    with open(STASH, "w") as fh:
        json.dump({"state": state, "code_verifier": flow.code_verifier}, fh)
    os.chmod(STASH, 0o600)
    print(url)

def cmd_code(code):
    with open(STASH) as fh:
        stash = json.load(fh)
    flow = build_flow()
    flow.code_verifier = stash["code_verifier"]
    flow.fetch_token(code=code)
    creds = flow.credentials

    token_path = os.path.join(BASE, "token.json")
    with open(token_path, "w") as fh:
        fh.write(creds.to_json())
    os.chmod(token_path, 0o600)
    os.remove(STASH)

    print("TOKEN_WRITTEN", token_path)
    print("has_refresh_token:", bool(creds.refresh_token))

if __name__ == "__main__":
    if sys.argv[1:2] == ["url"]:
        cmd_url()
    elif sys.argv[1:2] == ["code"]:
        cmd_code(sys.argv[2])
    else:
        sys.exit(__doc__)
