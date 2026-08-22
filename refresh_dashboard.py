#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / 'index.html'

PROMPT = r'''
Use the configured Garmin MCP server and return JSON only.
Build a compact dataset for a daily dashboard with these exact top-level keys:
- today
- latestActivityDate
- todaySummary
- days
- activities

Shape:
{
  "today": "YYYY-MM-DD",
  "latestActivityDate": "YYYY-MM-DDTHH:MM:SS" or null,
  "todaySummary": {
    "steps": number,
    "sleep_hours": number,
    "sleep_score": number,
    "body_battery": number,
    "stress_avg": number
  },
  "days": [
    {
      "date": "YYYY-MM-DD",
      "steps": number,
      "sleep_hours": number,
      "sleep_score": number,
      "body_battery_high": number,
      "body_battery_low": number,
      "stress_avg": number,
      "activity": boolean
    }
  ],
  "activities": [
    {
      "date": "YYYY-MM-DD HH:MM:SS",
      "sport": "string",
      "distance_km": number,
      "duration_minutes": number,
      "title": "string"
    }
  ]
}

Requirements:
- Use the latest 7 days of data.
- Include up to 10 recent activities.
- If a metric is unavailable, use 0 for numeric fields and false for activity.
- Keep the response machine-readable and do not include markdown or commentary.
- Prefer the dashboard fields exactly as written above, even if you need to call multiple Garmin tools.
'''


def extract_json(text: str) -> dict:
    # Hermes emits the JSON inside a decorated transcript, and sometimes the
    # JSON string is escaped. Strip the wrapper, then try both raw and unescaped
    # variants before giving up.
    text = text.strip()
    marker = '╭─ ⚕ Hermes'
    marker_idx = text.find(marker)
    if marker_idx != -1:
        start = text.find('{', marker_idx)
    else:
        start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    candidates = [text]
    if '\\"' in text:
        candidates.append(text.replace('\\"', '"'))
    if '\\n' in text:
        candidates.append(text.replace('\\n', '\n').replace('\\"', '"'))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            pass
    raise ValueError('Could not parse JSON from Hermes output')


def clean_number(value, fallback=0):
    if value is None:
        return fallback
    try:
        n = float(value)
    except Exception:
        return fallback
    if n != n:  # NaN
        return fallback
    if abs(n - round(n)) < 1e-9:
        return int(round(n))
    return n


def _pick(mapping: dict, *names, default=0):
    if not isinstance(mapping, dict):
        return default
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return default


def _safe_dict(value):
    return value if isinstance(value, dict) else {}


def normalize(data: dict) -> dict:
    data = dict(data)
    data['today'] = str(data.get('today') or datetime.now(timezone.utc).date().isoformat())
    latest = data.get('latestActivityDate') or data.get('latest_activity_date')
    data['latestActivityDate'] = str(latest) if latest else None

    today_summary = data.get('todaySummary') or data.get('today_summary') or {}
    sleep = _safe_dict(today_summary.get('sleep'))
    stress = _safe_dict(today_summary.get('stress'))
    body = _safe_dict(today_summary.get('bodyBattery') or today_summary.get('body_battery'))
    data['todaySummary'] = {
        'steps': clean_number(_pick(today_summary, 'steps', 'stepCount', 'step_count')),
        'sleep_hours': clean_number(_pick(today_summary, 'sleep_hours', default=sleep.get('hours'))),
        'sleep_score': clean_number(_pick(today_summary, 'sleep_score', default=sleep.get('score'))),
        'body_battery': clean_number(_pick(today_summary, 'body_battery', default=body.get('current'))),
        'stress_avg': clean_number(_pick(today_summary, 'stress_avg', default=stress.get('avg'))),
    }

    activity_dates = set()
    for a in data.get('activities', []):
        d = str(a.get('date') or '')[:10]
        if d:
            activity_dates.add(d)

    days = []
    for d in data.get('days', []):
        d = dict(d)
        sleep_hours = _pick(d, 'sleep_hours', 'sleepHours', default=None)
        sleep_score = _pick(d, 'sleep_score', 'sleepScore', default=None)
        stress_avg = _pick(d, 'stress_avg', 'stressAvg', default=None)
        body_high = _pick(d, 'body_battery_high', 'bodyBatteryHigh', default=None)
        body_low = _pick(d, 'body_battery_low', 'bodyBatteryLow', default=None)
        days.append({
            'date': str(d.get('date')),
            'steps': clean_number(_pick(d, 'steps', 'stepCount', 'step_count')),
            'sleep_hours': clean_number(sleep_hours),
            'sleep_score': clean_number(sleep_score),
            'body_battery_high': clean_number(body_high),
            'body_battery_low': clean_number(body_low),
            'stress_avg': clean_number(stress_avg),
            'activity': bool(d.get('activity', str(d.get('date')) in activity_dates)),
        })
    data['days'] = days[:7]

    activities = []
    for a in data.get('activities', [])[:10]:
        a = dict(a)
        sport = a.get('sport') or a.get('type') or 'unknown'
        title = a.get('title') or a.get('name') or 'Activity'
        activities.append({
            'date': str(a.get('date')),
            'sport': str(sport),
            'distance_km': clean_number(_pick(a, 'distance_km', 'distanceKm', default=0)),
            'duration_minutes': clean_number(_pick(a, 'duration_minutes', 'durationMinutes', default=0)),
            'title': str(title),
        })
    data['activities'] = activities

    return data


def run_hermes() -> dict:
    proc = subprocess.run(
        ['hermes', 'chat', '-q', PROMPT],
        capture_output=True,
        text=True,
        check=False,
    )
    output = (proc.stdout or '') + '\n' + (proc.stderr or '')
    if proc.returncode != 0 and not output.strip():
        raise RuntimeError(f'Hermes command failed with exit code {proc.returncode}')
    return extract_json(output)


def replace_data_block(html: str, data: dict) -> str:
    new_block = 'const data = ' + json.dumps(data, indent=2, ensure_ascii=False) + ';'
    pattern = r'const data = \{.*?\n\};\n\nconst COLORS = '
    replacement = new_block + '\n\nconst COLORS = '
    updated, count = re.subn(pattern, replacement, html, flags=re.S)
    if count != 1:
        raise RuntimeError('Could not find the data block in index.html')
    return updated


def main() -> int:
    data = normalize(run_hermes())
    html = INDEX.read_text(encoding='utf-8')
    updated = replace_data_block(html, data)
    INDEX.write_text(updated, encoding='utf-8')
    print(f'Updated {INDEX}')
    print(json.dumps({
        'today': data['today'],
        'steps': data['todaySummary']['steps'],
        'activities': len(data['activities']),
        'days': len(data['days']),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
