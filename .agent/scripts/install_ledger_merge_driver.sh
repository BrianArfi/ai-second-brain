#!/usr/bin/env bash
# Register the Slack mention ledger merge driver in THIS clone.
#
# A merge driver lives in .git/config, which is per clone and never committed, so
# every machine has to run this once. The .gitattributes line that points at the
# driver IS committed, so a clone that skips this step falls back to an ordinary
# conflict rather than to a wrong merge.
set -euo pipefail
cd "$(dirname "$0")/../.."

git config merge.slackledger.name \
  "union merge for the Slack mention ledger, never discards a side"
git config merge.slackledger.driver \
  "python3 .agent/scripts/slack_ledger_merge_driver.py %O %A %B"

echo "registered merge.slackledger in $(pwd)/.git/config"
git check-attr merge -- journal/state/slack_mention_ledger.json
