#!/usr/bin/env bash
# bin/setup-github-oidc.sh
# ---------------------------------------------------------------------------
# Creates the GitHub OIDC provider and deploy role (run once per account).
#
# Usage:
#   ./bin/setup-github-oidc.sh --github-org <org> --github-repo <repo> [--region ap-southeast-2]
# ---------------------------------------------------------------------------
set -euo pipefail

REGION="ap-southeast-2"
APP_NAME=""
GITHUB_ORG=""
GITHUB_REPO=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --github-org)  GITHUB_ORG="$2";  shift 2 ;;
    --github-repo) GITHUB_REPO="$2"; shift 2 ;;
    --app-name)    APP_NAME="$2";    shift 2 ;;
    --region)      REGION="$2";      shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${GITHUB_ORG}" || -z "${GITHUB_REPO}" ]]; then
  echo "Usage: $0 --github-org <org> --github-repo <repo> [--region <region>] [--app-name <name>]" >&2
  exit 1
fi

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

ECR_STACK="${APP_NAME}-ecr"
OIDC_STACK="${APP_NAME}-github-oidc"
CF_DIR="$(cd "$(dirname "$0")/../cf" && pwd)"

# ---- load tags from cf/tags.json --------------------------------------------
TAGS_FILE="${CF_DIR}/tags.json"
if [[ ! -f "${TAGS_FILE}" ]]; then
  echo "ERROR: ${TAGS_FILE} not found." >&2; exit 1
fi
CF_TAGS=$(jq -r '.[] | "\(.Key)=\(.Value)"' "${TAGS_FILE}" | tr '\n' ' ')

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Setting up GitHub OIDC for ${GITHUB_ORG}/${GITHUB_REPO}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "▶ Validating template…"
aws cloudformation validate-template \
  --template-body "file://${CF_DIR}/iam-github-oidc.yml" \
  --region "${REGION}" \
  --output text > /dev/null
echo "  ✓ Template valid"

echo ""
echo "▶ Deploying OIDC stack (${OIDC_STACK})…"
aws cloudformation deploy \
  --template-file "${CF_DIR}/iam-github-oidc.yml" \
  --stack-name "${OIDC_STACK}" \
  --parameter-overrides \
      "GitHubOrg=${GITHUB_ORG}" \
      "GitHubRepo=${GITHUB_REPO}" \
      "AppName=${APP_NAME}" \
      "EcrStackName=${ECR_STACK}" \
  --tags ${CF_TAGS} \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${REGION}" \
  --no-fail-on-empty-changeset

ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${OIDC_STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`DeployRoleArn`].OutputValue' \
  --output text)

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✅  OIDC setup complete"
echo ""
echo " Add the following to your GitHub repository secrets:"
echo ""
echo "   AWS_DEPLOY_ROLE_ARN = ${ROLE_ARN}"
echo ""
echo " And these repository variables:"
echo "   AWS_REGION          = ${REGION}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
