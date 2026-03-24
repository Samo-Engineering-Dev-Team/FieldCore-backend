#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Seacom API Test Runner
#  Logs in, captures the JWT, then runs all Bruno request folders.
#
#  Usage:
#    bash run_tests.sh                    # run everything
#    bash run_tests.sh 04_sites           # run one folder
#    bash run_tests.sh 06_incidents 07_reports   # run specific folders
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

BASE_URL="http://localhost:8000/api/v1"
EMAIL="admin@samotelecoms.co.za"
PASSWORD="Admin@1234"

# ── 1. Login and grab token ───────────────────────────────────────────────────
echo "Logging in as $EMAIL ..."
LOGIN_RESP=$(curl -s -X POST "$BASE_URL/auth/login" \
  --data-urlencode "username=$EMAIL" \
  --data-urlencode "password=$PASSWORD")

TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || \
        echo "$LOGIN_RESP" | python -c  "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "ERROR: Login failed. Response was:"
  echo "$LOGIN_RESP"
  exit 1
fi
echo "Token acquired."
echo ""

# ── 2. Determine which folders to run ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$#" -gt 0 ]; then
  FOLDERS=("$@")
else
  FOLDERS=(
    "01_auth"
    "02_users"
    "03_technicians"
    "04_sites"
    "05_tasks"
    "06_incidents"
    "07_reports"
    "08_incident-reports"
    "09_notifications"
    "10_access-requests"
    "11_clients"
    "13_routine-checks"
    "14_routine-issues"
    "15_routine-inspections"
    "16_route-patrols"
    "17_maintenance-schedules"
    "18_sessions"
    "19_system-settings"
    "20_dashboard"
    "21_webhooks"
  )
fi

# ── 3. Run each folder ────────────────────────────────────────────────────────
PASS=0
FAIL=0

for FOLDER in "${FOLDERS[@]}"; do
  echo "────────────────────────────────────────"
  echo "Running: $FOLDER"
  echo "────────────────────────────────────────"

  if npx @usebruno/cli run "$SCRIPT_DIR/$FOLDER" \
      --env local \
      --env-var "token=$TOKEN" \
      -r 2>&1; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
  fi
  echo ""
done

# ── 4. Summary ────────────────────────────────────────────────────────────────
echo "════════════════════════════════════════"
echo "  FOLDERS PASSED: $PASS"
echo "  FOLDERS FAILED: $FAIL"
echo "════════════════════════════════════════"

[ "$FAIL" -eq 0 ]
