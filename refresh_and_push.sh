#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python refresh_dashboard.py
if git diff --cached --quiet --exit-code >/dev/null 2>&1; then
  :
fi

git add index.html
if git diff --cached --quiet; then
  echo "No changes to commit"
  exit 0
fi

git commit -m "Update Garmin dashboard"
git push origin main
