#!/usr/bin/env bash
# bin/run-local.sh
# ---------------------------------------------------------------------------
# Run the Flask app locally with a virtualenv (no Docker required for the app
# itself, but docker compose up -d db localstack must be running first).
#
# Usage:
#   ./bin/run-local.sh [--port 3000]
#
# Prerequisites:
#   docker compose up -d db localstack   # Postgres + S3
#   docker compose run --rm migrate      # run migrations (first time)
# ---------------------------------------------------------------------------
set -euo pipefail

PORT=3000
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${REPO_DIR}/app"

while [[ $# -gt 0 ]]; do
  case $1 in
    --port) PORT="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ── Load .env if present ─────────────────────────────────────────────────────
ENV_FILE="${REPO_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  echo "▶ Loading ${ENV_FILE}…"
  set -o allexport
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +o allexport
fi

VENV="${APP_DIR}/.venv"

# Create virtualenv if it doesn't exist
if [[ ! -d "${VENV}" ]]; then
  echo "▶ Creating virtualenv at app/.venv…"
  python3 -m venv "${VENV}"
fi

# Install/sync dependencies
echo "▶ Installing dependencies…"
"${VENV}/bin/pip" install -q -r "${APP_DIR}/requirements-dev.txt"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " webapp-accounts-reconciller → http://localhost:${PORT}"
echo ""
echo "  DATABASE_URL : ${DATABASE_URL:-⚠ not set}"
echo "  S3_BUCKET    : ${S3_BUCKET_NAME:-⚠ not set}"
echo "  AWS_ENDPOINT : ${AWS_ENDPOINT_URL:-AWS (no LocalStack)}"
echo "  DEV_BYPASS   : ${DEV_BYPASS_AUTH:-<none — real Cognito auth>}"
echo ""
echo " Press Ctrl+C to stop."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

ENVIRONMENT=development \
PORT="${PORT}" \
FLASK_APP="app:app" \
FLASK_DEBUG=1 \
  "${VENV}/bin/flask" --app app:app run --host 0.0.0.0 --port "${PORT}" --debug
