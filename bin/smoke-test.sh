#!/usr/bin/env bash
# bin/smoke-test.sh
# ---------------------------------------------------------------------------
# Smoke-test the deployed app by hitting /hello and asserting the response.
# Resolves the ALB URL from the ECS CloudFormation stack automatically,
# or accepts an explicit --url if you already have one.
#
# Usage:
#   ./bin/smoke-test.sh [--env staging|production] [--region ap-southeast-2]
#   ./bin/smoke-test.sh --url http://my-alb.elb.amazonaws.com
# ---------------------------------------------------------------------------
set -euo pipefail

ENV="production"
REGION="ap-southeast-2"
APP_NAME=""
BASE_URL=""
RETRIES=18
RETRY_INTERVAL=10

while [[ $# -gt 0 ]]; do
  case $1 in
    --env)      ENV="$2";      shift 2 ;;
    --region)   REGION="$2";   shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    --url)      BASE_URL="$2"; shift 2 ;;
    --retries)  RETRIES="$2";  shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ---- derive APP_NAME from git remote if not supplied ------------------------
if [[ -z "${APP_NAME}" ]]; then
  APP_NAME=$(git remote get-url origin 2>/dev/null \
    | sed 's|.*[:/]\([^/]*\)\.git$|\1|; s|.*[:/]\([^/]*\)$|\1|')
  if [[ -z "${APP_NAME}" ]]; then
    echo "ERROR: could not derive app name from git remote. Pass --app-name <name>." >&2
    exit 1
  fi
fi

# ---- resolve base URL from CloudFormation if not supplied -------------------
if [[ -z "${BASE_URL}" ]]; then
  ECS_STACK="${APP_NAME}-ecs-${ENV}"
  echo "▶ Resolving ALB URL from stack ${ECS_STACK}…"
  ALB=$(aws cloudformation describe-stacks \
    --stack-name "${ECS_STACK}" \
    --region "${REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' \
    --output text 2>&1)
  if [[ -z "${ALB}" || "${ALB}" == "None" ]]; then
    echo "ERROR: could not resolve ALB from stack ${ECS_STACK}." >&2
    echo "       Run ./bin/deploy-infra.sh first, or pass --url <base-url>." >&2
    exit 1
  fi
  # Express Mode Endpoint is already a full URL (https://...); plain ALB DNS is not.
  if [[ "${ALB}" == http* ]]; then
    BASE_URL="${ALB}"
  else
    BASE_URL="https://${ALB}"
  fi
fi

# ---- test cases -------------------------------------------------------------
PASS=0
FAIL=0

run_test() {
  local description="$1"
  local path="$2"
  local expected_status="$3"
  local expected_key="$4"
  local expected_value="$5"

  local url="${BASE_URL}${path}"
  local tmp
  tmp=$(mktemp)

  echo ""
  echo "  ┌─ ${description}"
  echo "  │  GET ${url}"

  local status=""
  for i in $(seq 1 "${RETRIES}"); do
    status=$(curl -s -o "${tmp}" -w "%{http_code}" --connect-timeout 10 --max-time 30 \
      -H "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" \
      "${url}" 2>/dev/null || echo "000")
    if [[ "${status}" == "${expected_status}" ]]; then
      break
    fi
    if [[ "${i}" -lt "${RETRIES}" ]]; then
      echo "  │  attempt ${i}/${RETRIES} — HTTP ${status}, retrying in ${RETRY_INTERVAL}s…"
      sleep "${RETRY_INTERVAL}"
    fi
  done

  local body
  body=$(cat "${tmp}")
  rm -f "${tmp}"

  # Check status code
  if [[ "${status}" != "${expected_status}" ]]; then
    echo "  │  FAIL — expected HTTP ${expected_status}, got ${status}"
    echo "  └─ ✗"
    FAIL=$((FAIL + 1))
    return
  fi

  # Check body field if specified
  if [[ -n "${expected_key}" ]]; then
    local actual
    actual=$(echo "${body}" | jq -r ".${expected_key}" 2>/dev/null || echo "")
    if [[ "${actual}" != "${expected_value}" ]]; then
      echo "  │  FAIL — expected .${expected_key}==\"${expected_value}\", got \"${actual}\""
      echo "  │  Body: ${body}"
      echo "  └─ ✗"
      FAIL=$((FAIL + 1))
      return
    fi
    echo "  │  HTTP ${status}  .${expected_key} == \"${actual}\""
  else
    echo "  │  HTTP ${status}"
  fi

  echo "  └─ ✓"
  PASS=$((PASS + 1))
}

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Smoke test — ${BASE_URL}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

run_test "Health check"          "/health"      "200" "status"  "ok"
run_test "Root → login redirect" "/"            "302" ""        ""
run_test "Hello endpoint"        "/hello"       "200" "message" "Hello, world!"
run_test "404 handling"          "/nonexistent" "404" "error"   "Not found"

# ---- results ----------------------------------------------------------------
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
TOTAL=$((PASS + FAIL))
if [[ "${FAIL}" -eq 0 ]]; then
  echo " ✅  ${PASS}/${TOTAL} tests passed"
else
  echo " ❌  ${FAIL}/${TOTAL} tests failed"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[[ "${FAIL}" -eq 0 ]]
