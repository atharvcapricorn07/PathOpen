#!/bin/bash
# Manually run the same sweep the 3am cron job runs (POST /verify-pending).
# Each call only processes up to 25 markers, so this loops until the backlog is clear.

BASE="https://atharvcapricorn07-pathable.hf.space"
ADMIN_KEY="pathable_admin_x7f2k9d1"

echo "Running verification sweep..."
echo ""

while true; do
  RESULT_JSON=$(curl -s -X POST -H "X-Admin-Key: $ADMIN_KEY" "$BASE/verify-pending")

  DONE=$(echo "$RESULT_JSON" | python3 -c "
import json, sys

raw = sys.stdin.read()
try:
    d = json.loads(raw)
except json.JSONDecodeError:
    print('Server did not return JSON (likely a cold start or the space is down). Raw response:')
    print(raw[:500])
    print('STOP')
    sys.exit(0)

if isinstance(d, dict) and d.get('error') and 'checked' not in d:
    print(f\"error: {d['error']}\")
    print('STOP')
    sys.exit(0)

checked = d.get('checked', 0)
flagged = d.get('newly_flagged', 0)
failed = d.get('failed_will_retry', 0)
failures = d.get('failures', [])

print(f'checked: {checked}, newly_flagged: {flagged}, failed_will_retry: {failed}')
for f in failures:
    print(f\"  failed marker {f.get('marker_id', '?')}: {f.get('detail', '?')}\")

if checked == 0 and failed == 0:
    print('STOP')
else:
    print('CONTINUE')
")

  echo "$DONE" | grep -v -E '^(STOP|CONTINUE)$'

  if echo "$DONE" | grep -q '^STOP$'; then
    echo ""
    echo "Nothing left pending. Done."
    break
  fi

  echo "---"
  sleep 1
done