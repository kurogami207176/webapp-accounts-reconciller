#!/usr/bin/env bash
# bin/deploy-app.sh
# ---------------------------------------------------------------------------
# Deploy a new image to the ECS Express service by updating the CloudFormation
# stack. Express Mode handles rolling updates and rollback automatically.
#
# Usage:
#   ./bin/deploy-app.sh [--env staging|production] [--region ap-southeast-2] [--tag v1.2.3]
# ---------------------------------------------------------------------------
set -euo pipefail

ENV="production"
REGION="ap-southeast-2"
APP_NAME=""
TAG="latest"
DB_STACK=""
COGNITO_STACK=""
SECRET_KEY_ARN=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --env)      ENV="$2";      shift 2 ;;
    --region)   REGION="$2";   shift 2 ;;
    --tag)      TAG="$2";      shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    --db-stack)       DB_STACK="$2";       shift 2 ;;
    --cognito-stack)  COGNITO_STACK="$2";  shift 2 ;;
    --secret-key-arn) SECRET_KEY_ARN="$2"; shift 2 ;;
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
  echo "▶ Derived APP_NAME from git remote: ${APP_NAME}"
fi

ECS_STACK="${APP_NAME}-ecs-${ENV}"
ECR_STACK="${APP_NAME}-ecr"
CF_DIR="$(cd "$(dirname "$0")/../cf" && pwd)"

# ---- load tags from cf/tags.json --------------------------------------------
TAGS_FILE="${CF_DIR}/tags.json"
if [[ ! -f "${TAGS_FILE}" ]]; then
  echo "ERROR: ${TAGS_FILE} not found." >&2; exit 1
fi
CF_TAGS=$(jq -r '.[] | "\(.Key)=\(.Value)"' "${TAGS_FILE}" | tr '\n' ' ')

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Deploying app — env: ${ENV}  region: ${REGION}  tag: ${TAG}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Update the ECS Express stack — updating ImageTag triggers a canary rollout
echo ""
echo "▶ Updating ECS Express stack with ImageTag=${TAG}…"
aws cloudformation deploy \
  --template-file "${CF_DIR}/ecs.yml" \
  --stack-name "${ECS_STACK}" \
  --parameter-overrides \
      "AppName=${APP_NAME}" \
      "Environment=${ENV}" \
      "EcrStackName=${ECR_STACK}" \
      "ImageTag=${TAG}" \
      "DatabaseStackName=${DB_STACK}" \
      "CognitoStackName=${COGNITO_STACK}" \
      "SecretKeyArn=${SECRET_KEY_ARN}" \
  --tags ${CF_TAGS} \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --region "${REGION}" \
  --no-fail-on-empty-changeset

ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name "${ECS_STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' \
  --output text)

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✅  Deployment complete"
echo "    App URL: ${ALB_DNS}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
