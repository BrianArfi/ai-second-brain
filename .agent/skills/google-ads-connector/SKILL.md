---
name: google-ads-connector
description: Reads Google Ads metrics for Search, Display, and YouTube. Not built yet: it is blocked on a developer token that needs approval. Read the credential checklist before starting.
---

# google-ads-connector (You): credential checklist + spec

Read Google Ads (Search/Display/YouTube) metrics for You. **Build priority #3** (most setup
friction: developer token needs approval). Status: **NOT built yet.**

## What the owner must obtain (one-time)
1. **Google Ads account** + the **Customer ID** (`123-456-7890`).
2. A **Developer Token** from the Google Ads API Center (starts in *test* access; **basic access
   needs an application + approval** (this has lead time, hence priority #3).
3. **OAuth2 client** (Google Cloud console) → client ID + secret + a **refresh token** with the
   `https://www.googleapis.com/auth/adwords` scope.

## Where it goes
`.agent/skills/google-ads-connector/token.env` (gitignored):
```
GOOGLE_ADS_DEVELOPER_TOKEN=...
GOOGLE_ADS_CLIENT_ID=...
GOOGLE_ADS_CLIENT_SECRET=...
GOOGLE_ADS_REFRESH_TOKEN=...
GOOGLE_ADS_CUSTOMER_ID=...
```

## API (for the script)
- Google Ads API (REST or the `google-ads` Python lib) `searchStream` with a GAQL query selecting
  `metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions` from `campaign`.
- Refresh-token OAuth → access token per call.

## Build note
Built once `token.env` is filled AND the developer token has at least basic access. Refresh-token
OAuth is headless-safe (good for scheduled runs). Until then `/growth-report` flags Google Ads
"not connected".
