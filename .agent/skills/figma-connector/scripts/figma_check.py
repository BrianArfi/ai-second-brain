#!/usr/bin/env python3
"""Verify the Figma PAT works and can actually reach a file.

Written after the repo token silently expired and every call came back as a bare
403 "Token expired", which is easy to mistake for a permissions problem on the
file. Run this first whenever Figma stops working.

    python3 .agent/skills/figma-connector/scripts/figma_check.py
    python3 .agent/skills/figma-connector/scripts/figma_check.py --file <FILE_KEY>
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(SKILL_DIR, "token.json")

# Seller Portal, Work. The file this was written for.
DEFAULT_FILE = "HSmQTNkLKrVoPSuz2WCEyI"

def load_token(explicit=None):
    if explicit:
        return explicit, "--token"
    if os.environ.get("FIGMA_ACCESS_TOKEN"):
        return os.environ["FIGMA_ACCESS_TOKEN"], "FIGMA_ACCESS_TOKEN"
    if os.path.exists(TOKEN_FILE):
        d = json.load(open(TOKEN_FILE))
        tok = d.get("access_token") or d.get("token")
        if tok:
            return tok, TOKEN_FILE
    return None, None

def call(path, token):
    req = urllib.request.Request(
        "https://api.figma.com/v1" + path, headers={"X-Figma-Token": token}
    )
    try:
        return 200, json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            body = json.loads(body)
        except Exception:
            body = body[:300]
        return e.code, body
    except Exception as e:  # network, DNS, timeout
        return 0, str(e)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token")
    ap.add_argument("--file", default=DEFAULT_FILE)
    args = ap.parse_args()

    token, source = load_token(args.token)
    if not token:
        print("FAIL: no token found.")
        print(f"  Put one in {TOKEN_FILE} as: " '{"access_token": "figd_..."}')
        print("  or export FIGMA_ACCESS_TOKEN")
        return 2

    print(f"token source : {source}")
    print(f"token prefix : {token[:9]}... ({len(token)} chars)")
    if not token.startswith("figd_"):
        print("  WARNING: Figma personal access tokens normally start with 'figd_'.")

    code, body = call("/me", token)
    if code != 200:
        print(f"\nFAIL /v1/me -> HTTP {code}: {body}")
        if isinstance(body, dict) and "expired" in str(body).lower():
            print("\n  The token is expired. Generate a new one:")
            print("  Figma > your avatar > Settings > Security > Personal access tokens")
            print("  Scope needed: 'File content' = Read")
        return 1

    print(f"account      : {body.get('email')} ({body.get('handle')})")

    print(f"\nchecking file {args.file} ...")
    code, body = call(f"/files/{args.file}?depth=1", token)
    if code != 200:
        print(f"FAIL -> HTTP {code}: {body}")
        if code == 403:
            print("\n  Token is valid but cannot read this file. Either the scope is")
            print("  missing ('File content' = Read) or the account lacks access to it.")
        return 1

    print(f"OK           : '{body.get('name')}'  last modified {body.get('lastModified')}")
    pages = body.get("document", {}).get("children", [])
    print(f"pages        : {len(pages)}")
    for p in pages:
        print(f"   {p['id']:>8}  {p.get('name')}")

    print("\nPASS. Figma REST access is working.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
