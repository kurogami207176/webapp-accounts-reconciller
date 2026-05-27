#!/usr/bin/env bash
# bin/deploy-infra.sh
# ---------------------------------------------------------------------------
# Bootstrap all infrastructure stacks (ECR → ECS Express → DNS).
# Run this once to provision; afterwards use bin/deploy-app.sh to release.
#
# ECS Express Mode auto-provisions the ALB, target group, security groups,
# auto-scaling, and CloudWatch logs — no separate network stack required.
#
# Usage:
#   ./bin/deploy-infra.sh [--env staging|production] [--region ap-southeast-2]
# ---------------------------------------------------------------------------
set -euo pipefail

# ---- defaults ---------------------------------------------------------------
ENV="production"
REGION="ap-southeast-2"
APP_NAME=""
SKIP_DNS="false"
SKIP_GLUE="false"
DB_STACK=""       # e.g. webapp-production (from webapp-environment-setup)
COGNITO_STACK=""  # e.g. webapp-accounts-reconciller-cognito-production
SECRET_KEY_ARN="" # ARN of the Flask secret key in Secrets Manager

# ---- parse args -------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case $1 in
    --env)       ENV="$2";       shift 2 ;;
    --region)    REGION="$2";    shift 2 ;;
    --app-name)  APP_NAME="$2";  shift 2 ;;
    --skip-dns)       SKIP_DNS="true";       shift 1 ;;
    --skip-glue)      SKIP_GLUE="true";     shift 1 ;;
    --db-stack)       DB_STACK="$2";        shift 2 ;;
    --cognito-stack)  COGNITO_STACK="$2";   shift 2 ;;
    --secret-key-arn) SECRET_KEY_ARN="$2";  shift 2 ;;
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

# ---- derived names ----------------------------------------------------------
ECR_STACK="${APP_NAME}-ecr"
S3_STACK="${APP_NAME}-s3-${ENV}"
GLUE_STACK="${APP_NAME}-glue-${ENV}"
ECS_STACK="${APP_NAME}-ecs-${ENV}"
DNS_STACK="${APP_NAME}-dns-${ENV}"
CF_DIR="$(cd "$(dirname "$0")/../cf" && pwd)"

# ---- load tags from cf/tags.json --------------------------------------------
TAGS_FILE="${CF_DIR}/tags.json"
if [[ ! -f "${TAGS_FILE}" ]]; then
  echo "ERROR: ${TAGS_FILE} not found." >&2; exit 1
fi
# Convert [{"Key":"k","Value":"v"},...] → "k=v k2=v2 ..." for --tags
CF_TAGS=$(jq -r '.[] | "\(.Key)=\(.Value)"' "${TAGS_FILE}" | tr '\n' ' ')

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Deploying infrastructure — env: ${ENV}  region: ${REGION}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ---- validate templates first -----------------------------------------------
echo ""
echo "▶ Validating CloudFormation templates…"
TEMPLATES_TO_VALIDATE="ecr.yml s3.yml ecs.yml"
[[ "${SKIP_GLUE}" == "false" ]] && TEMPLATES_TO_VALIDATE="${TEMPLATES_TO_VALIDATE} glue.yml"
[[ "${SKIP_DNS}" == "false" ]] && TEMPLATES_TO_VALIDATE="${TEMPLATES_TO_VALIDATE} dns.yml"
for tpl in ${TEMPLATES_TO_VALIDATE}; do
  echo "  → ${tpl}"
  aws cloudformation validate-template \
    --template-body "file://${CF_DIR}/${tpl}" \
    --region "${REGION}" \
    --output text > /dev/null
done
echo "  ✓ All templates valid"

# ---- 1. ECR -----------------------------------------------------------------
echo ""
echo "▶ Stack 1/4 — ECR (${ECR_STACK})"
aws cloudformation deploy \
  --template-file "${CF_DIR}/ecr.yml" \
  --stack-name "${ECR_STACK}" \
  --parameter-overrides \
      "AppName=${APP_NAME}" \
      "Environment=${ENV}" \
  --tags ${CF_TAGS} \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${REGION}" \
  --no-fail-on-empty-changeset

ECR_URI=$(aws cloudformation describe-stacks \
  --stack-name "${ECR_STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`RepositoryUri`].OutputValue' \
  --output text)
echo "  ✓ ECR URI: ${ECR_URI}"

# ---- 2. S3 ------------------------------------------------------------------
echo ""
echo "▶ Stack 2/4 — S3 Reconciler Bucket (${S3_STACK})"
aws cloudformation deploy \
  --template-file "${CF_DIR}/s3.yml" \
  --stack-name "${S3_STACK}" \
  --parameter-overrides \
      "AppName=${APP_NAME}" \
      "Environment=${ENV}" \
  --tags ${CF_TAGS} \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${REGION}" \
  --no-fail-on-empty-changeset
S3_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "${S3_STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
  --output text)
echo "  ✓ S3 Bucket: ${S3_BUCKET}"

# ---- 3. ECS (Express Mode) --------------------------------------------------
echo ""
echo "▶ Stack 3/4 — ECS Express (${ECS_STACK})"
echo "  ECS Express Mode will auto-provision the ALB, security groups,"
echo "  auto-scaling, and CloudWatch logs. This may take a few minutes…"
aws cloudformation deploy \
  --template-file "${CF_DIR}/ecs.yml" \
  --stack-name "${ECS_STACK}" \
  --parameter-overrides \
      "AppName=${APP_NAME}" \
      "Environment=${ENV}" \
      "EcrStackName=${ECR_STACK}" \
      "ImageTag=latest" \
      "DatabaseStackName=${DB_STACK}" \
      "S3StackName=${S3_STACK}" \
      "CognitoStackName=${COGNITO_STACK}" \
      "SecretKeyArn=${SECRET_KEY_ARN}" \
  --tags ${CF_TAGS} \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --region "${REGION}" \
  --no-fail-on-empty-changeset
echo "  ✓ ECS Express stack deployed"

ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name "${ECS_STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' \
  --output text)

# ---- 4a. Glue (optional) ────────────────────────────────────────────────────
if [[ "${SKIP_GLUE}" == "false" ]]; then
  echo ""
  echo "▶ Stack 4a — Glue crawlers (${GLUE_STACK})"
  aws cloudformation deploy \
    --template-file "${CF_DIR}/glue.yml" \
    --stack-name "${GLUE_STACK}" \
    --parameter-overrides \
        "AppName=${APP_NAME}" \
        "Environment=${ENV}" \
        "S3StackName=${S3_STACK}" \
    --tags ${CF_TAGS} \
    --region "${REGION}" \
    --no-fail-on-empty-changeset
  echo "  ✓ Glue stack deployed"
fi

# ---- 4b. DNS (optional) ─────────────────────────────────────────────────────
APP_URL="http://${ALB_DNS}"
if [[ "${SKIP_DNS}" == "false" ]]; then
  echo ""
  echo "▶ Stack 4b — DNS (${DNS_STACK})"
  aws cloudformation deploy \
    --template-file "${CF_DIR}/dns.yml" \
    --stack-name "${DNS_STACK}" \
    --parameter-overrides \
        "AppName=${APP_NAME}" \
        "Environment=${ENV}" \
        "EcsStackName=${ECS_STACK}" \
    --tags ${CF_TAGS} \
    --region "${REGION}" \
    --no-fail-on-empty-changeset

  APP_URL=$(aws cloudformation describe-stacks \
    --stack-name "${DNS_STACK}" \
    --region "${REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`AppUrl`].OutputValue' \
    --output text)
  echo "  ✓ DNS stack deployed"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✅  Infrastructure ready"
echo "    ECR URI : ${ECR_URI}"
echo "    App URL : ${APP_URL}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next steps:"
echo "  1. Push a Docker image:  ./bin/push-image.sh --tag latest"
echo "  2. Force a new deploy:   ./bin/deploy-app.sh --env ${ENV}"
