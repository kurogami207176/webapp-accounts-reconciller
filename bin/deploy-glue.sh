#!/usr/bin/env bash
# bin/deploy-glue.sh
# ---------------------------------------------------------------------------
# Deploy the Glue database + crawlers stack (cf/glue.yml).
# Requires cf/s3.yml to be deployed first.
#
# Usage:
#   ./bin/deploy-glue.sh [--env staging|production] [--region ap-southeast-2]
# ---------------------------------------------------------------------------
set -euo pipefail

ENV="production"
REGION="ap-southeast-2"
APP_NAME=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --env)      ENV="$2";      shift 2 ;;
    --region)   REGION="$2";   shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${APP_NAME}" ]]; then
  APP_NAME=$(git remote get-url origin 2>/dev/null \
    | sed 's|.*[:/]\([^/]*\)\.git$|\1|; s|.*[:/]\([^/]*\)$|\1|')
  echo "▶ Derived APP_NAME: ${APP_NAME}"
fi

S3_STACK="${APP_NAME}-s3-${ENV}"
STACK_NAME="${APP_NAME}-glue-${ENV}"
CF_DIR="$(cd "$(dirname "$0")/../cf" && pwd)"
TAGS_FILE="${CF_DIR}/tags.json"
CF_TAGS=$(jq -r '.[] | "\(.Key)=\(.Value)"' "${TAGS_FILE}" | tr '\n' ' ')

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Deploying Glue stack — env: ${ENV}  region: ${REGION}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

aws cloudformation validate-template \
  --template-body "file://${CF_DIR}/glue.yml" \
  --region "${REGION}" --output text > /dev/null
echo "  ✓ Template valid"

aws cloudformation deploy \
  --template-file "${CF_DIR}/glue.yml" \
  --stack-name "${STACK_NAME}" \
  --parameter-overrides \
      "AppName=${APP_NAME}" \
      "Environment=${ENV}" \
      "S3StackName=${S3_STACK}" \
  --tags ${CF_TAGS} \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${REGION}" \
  --no-fail-on-empty-changeset

echo ""
echo "✅  Glue stack deployed: ${STACK_NAME}"
