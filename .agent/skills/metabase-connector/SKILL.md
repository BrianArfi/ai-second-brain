---
name: Metabase Connector
description: A skill to interact with the Metabase API, allowing you to fetch dashboard details, query questions (cards), and run raw SQL queries directly from the chat.
---

# Metabase Connector Skill

This skill allows the agent to interact with a Metabase instance using either Email/Password credentials or a Google OAuth Session Token.

## Capabilities

1. **Verify Connection**: Test authentication status against Metabase.
2. **Explore Dashboard**: Fetch all questions/cards on a dashboard, listing their IDs, dashcard IDs, and parameter options.
3. **Query Card (Question)**: Run a query for a specific card with parameters.
4. **Query Dashcard**: Run a query for a specific card on a dashboard layout using active dashboard filters.
5. **List Databases**: Get a list of database connections configured in Metabase.
6. **Execute Direct SQL**: Execute native SQL queries directly on a connected database via Metabase.

## Prerequisites

- **Configuration**: Set up the following environment variables in your `.env` file:
  ```env
  METABASE_BASE_URL=https://metabase.yourcompany.me
  
  # Option A: Email/Password login
  METABASE_USERNAME=yourteammate@yourcompany.com
  METABASE_PASSWORD=your-password
  
  # Option B: Direct Session Token (Mandatory for Google OAuth users)
  METABASE_SESSION_TOKEN=your-session-cookie-value
  ```
- **Session Token Extraction**: If using Google OAuth, log into Metabase in your browser, open DevTools (`F12`), go to **Application > Cookies**, copy the value of `metabase.SESSION`, and paste it as `METABASE_SESSION_TOKEN` in `.env`.

## Usage

The skill runs using Node.js. It does not require any external npm dependencies (contains a self-loading `.env` file parser).

### Verify Connection
```bash
node .agent/skills/metabase-connector/scripts/metabase.js login
```

### Fetch Dashboard Cards
```bash
node .agent/skills/metabase-connector/scripts/metabase.js dashboard 27
```

### Query Card in Dashboard Context with Filter
Format: `node .agent/skills/metabase-connector/scripts/metabase.js query-dashcard <dash_id> <dashcard_id> <card_id> "<parameter_id>=<value>"`
```bash
node .agent/skills/metabase-connector/scripts/metabase.js query-dashcard 27 476 366 "d9a6252e=Last 30 Days"
```

### Run Custom SQL Query
Format: `node .agent/skills/metabase-connector/scripts/metabase.js sql <db_id> "<query>"`
```bash
node .agent/skills/metabase-connector/scripts/metabase.js sql 2 "SELECT * FROM my_table LIMIT 10"
```
