#!/bin/bash
BASE="https://atharvcapricorn07-pathable.hf.space"

STATS_JSON=$(curl -s "$BASE/stats")

echo "$STATS_JSON" | python3 -c "
import json, sys

raw = sys.stdin.read()
try:
    d = json.loads(raw)
except json.JSONDecodeError:
    print('Server did not return JSON (likely a cold start or the space is down). Raw response:')
    print(raw[:500])
    sys.exit(1)

required = ['unique_devices', 'returning_devices', 'total_markers_submitted', 'routes_generated', 'bug_reports', 'markers_by_label']
missing = [k for k in required if k not in d]
if missing:
    print(f'Response was JSON but missing expected fields: {missing}')
    print('Raw response:')
    print(json.dumps(d, indent=2)[:500])
    sys.exit(1)

print(f\"unique devices: {d['unique_devices']}\")
print(f\"returning devices: {d['returning_devices']}\")
print(f\"total markers submitted: {d['total_markers_submitted']}\")
print(f\"routes generated: {d['routes_generated']}\")
print(f\"bug reports: {d['bug_reports']}\")
print()
print('markers by label:')
for label, count in d['markers_by_label'].items():
    print(f'  {label}: {count}')
"

echo ""
echo "--- bug reports ---"

BUGS_JSON=$(curl -s "$BASE/bugs")

echo "$BUGS_JSON" | python3 -c "
import json, sys

raw = sys.stdin.read()
try:
    bugs = json.loads(raw)
except json.JSONDecodeError:
    print('Server did not return JSON (likely a cold start or the space is down). Raw response:')
    print(raw[:500])
    sys.exit(1)

if not bugs:
    print('  none yet')
else:
    for b in bugs:
        print(f\"  [{b.get('created_at', '?')}] {b.get('message', '?')}\")
        print(f\"    device: {b.get('device_id', '?')}\")
        print(f\"    page: {b.get('page_url', '?')}\")
        print(f\"    agent: {b.get('user_agent', '?')}\")
        print()
"