#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python refresh_dashboard.py

git add index.html
if git diff --cached --quiet; then
  exit 0
fi

git commit -m "Update Garmin dashboard"
git push origin main
printf 'Garmin dashboard refreshed and pushed\n'
