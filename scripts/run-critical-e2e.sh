#!/usr/bin/env bash
# P7-02: a green gate requires every named case to pass once, without skip/retry.
# Exit: 0=complete pass, 1=test failure, 2=environment failure, 3=contract failure.
set -euo pipefail
E2E_DIR="$(cd "$(dirname "$0")/../e2e" && pwd)"
BASE_URL="${E2E_BASE_URL:-http://localhost:15666}"
API_URL="${E2E_API_BASE_URL:-http://localhost:17101/api/v1}"
ARTIFACT_DIR="${E2E_ARTIFACT_DIR:-}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --base-url) BASE_URL="${2:?--base-url requires URL}"; shift 2 ;;
    --api-url) API_URL="${2:?--api-url requires URL}"; shift 2 ;;
    --artifacts-dir) ARTIFACT_DIR="${2:?--artifacts-dir requires directory}"; shift 2 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done
API_URL="${API_URL%/}"
case "$API_URL" in */api/v1) ;; *) API_URL="$API_URL/api/v1" ;; esac
HEALTH_URL="${API_URL%/api/v1}/health"
if [ -z "$ARTIFACT_DIR" ]; then
  ARTIFACT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/clpm-critical-e2e.XXXXXX")"
fi
mkdir -p "$ARTIFACT_DIR"
ARTIFACT_DIR="$(cd "$ARTIFACT_DIR" && pwd)"
export E2E_BASE_URL="$BASE_URL" E2E_API_BASE_URL="$API_URL"
export PLAYWRIGHT_HTML_OPEN=never
echo "base_url=$BASE_URL"
echo "api_url=$API_URL"
echo "artifacts=$ARTIFACT_DIR"
if ! curl -sf -o /dev/null --max-time 5 "$BASE_URL"; then
  echo "ERROR: frontend unreachable: $BASE_URL" >&2
  exit 2
fi
if ! curl -sf -o /dev/null --max-time 5 "$HEALTH_URL"; then
  echo "ERROR: backend unreachable: $HEALTH_URL" >&2
  exit 2
fi
cd "$E2E_DIR"
if [ ! -x node_modules/.bin/playwright ]; then
  echo "ERROR: e2e dependencies missing; run npm ci in e2e" >&2
  exit 2
fi
# Structured collection checks identities, not output line counts.
set +e
PLAYWRIGHT_JSON_OUTPUT_FILE="$ARTIFACT_DIR/collected.json" \
  node_modules/.bin/playwright test --config=playwright.critical.config.ts \
  --list --reporter=json >"$ARTIFACT_DIR/collect.stdout.log" 2>"$ARTIFACT_DIR/collect.stderr.log"
collect_rc=$?
set -e
echo "PLAYWRIGHT_COLLECT_EXIT=$collect_rc"
if [ "$collect_rc" -ne 0 ]; then exit 3; fi
node scripts/validate-critical-results.mjs collect critical-cases.json \
  "$ARTIFACT_DIR/collected.json" "$ARTIFACT_DIR/collection-summary.json"
set +e
PLAYWRIGHT_JSON_OUTPUT_FILE="$ARTIFACT_DIR/executed.json" \
  node_modules/.bin/playwright test --config=playwright.critical.config.ts \
  --reporter=json --retries=0 --forbid-only \
  --output="$ARTIFACT_DIR/test-results" \
  >"$ARTIFACT_DIR/run.stdout.log" 2>"$ARTIFACT_DIR/run.stderr.log"
run_rc=$?
node scripts/validate-critical-results.mjs run critical-cases.json \
  "$ARTIFACT_DIR/executed.json" "$ARTIFACT_DIR/execution-summary.json"
contract_rc=$?
set -e
echo "PLAYWRIGHT_EXIT=$run_rc"
echo "CONTRACT_EXIT=$contract_rc"
if [ "$contract_rc" -ne 0 ]; then
  echo "CRITICAL_E2E_GATE=FAIL"
  exit "$contract_rc"
fi
if [ "$run_rc" -ne 0 ]; then
  echo "CRITICAL_E2E_GATE=FAIL"
  exit 1
fi
echo "CRITICAL_E2E_GATE=PASS"
