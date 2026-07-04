#!/bin/bash
curl -s https://atharvcapricorn07-pathable.hf.space/stats | python3 -c "
import json, sys
d = json.load(sys.stdin)
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
curl -s https://atharvcapricorn07-pathable.hf.space/bugs | python3 -c "
import json, sys
bugs = json.load(sys.stdin)
if not bugs:
    print('  none yet')
else:
    for b in bugs:
        print(f\"  [{b['created_at']}] {b['message']}\")
        print(f\"    device: {b['device_id']}\")
        print(f\"    page: {b['page_url']}\")
        print(f\"    agent: {b['user_agent']}\")
        print()
"