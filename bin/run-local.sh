#!/usr/bin/env bash
# bin/run-local.sh
# ---------------------------------------------------------------------------
# Run the Flask app locally with a virtualenv.
# Creates/reuses app/.venv and installs dependencies automatically.
#
# Usage:
#   ./bin/run-local.sh [--port 3000]
# ---------------------------------------------------------------------------
set -euo pipefail

PORT=3000
APP_DIR="$(cd "$(dirname "$0")/../app" && pwd)"

while [[ $# -gt 0 ]]; do
  case $1 in
    --port) PORT="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

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
echo " Starting webapp-accounts-reconciller locally on http://localhost:${PORT}"
echo " Endpoints:"
echo "   GET http://localhost:${PORT}/"
echo "   GET http://localhost:${PORT}/hello"
echo "   GET http://localhost:${PORT}/health"
echo " Press Ctrl+C to stop."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

ENVIRONMENT=development \
PORT="${PORT}" \
FLASK_APP="${APP_DIR}/app.py" \
  "${VENV}/bin/flask" run --host 0.0.0.0 --port "${PORT}" --reload
