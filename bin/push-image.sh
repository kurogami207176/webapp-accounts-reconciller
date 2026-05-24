#!/usr/bin/env bash
# bin/push-image.sh
# ---------------------------------------------------------------------------
# Build the Docker image from app/ and push it to ECR.
#
# Usage:
#   ./bin/push-image.sh [--tag v1.2.3] [--region ap-southeast-2] [--platform linux/amd64]
# ---------------------------------------------------------------------------
set -euo pipefail

TAG="latest"
REGION="ap-southeast-2"
APP_NAME=""
PLATFORM="linux/amd64"
APP_DIR="$(cd "$(dirname "$0")/../app" && pwd)"

while [[ $# -gt 0 ]]; do
  case $1 in
    --tag)      TAG="$2";      shift 2 ;;
    --region)   REGION="$2";   shift 2 ;;
    --platform) PLATFORM="$2"; shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
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

ECR_STACK="${APP_NAME}-ecr"
CF_DIR="$(cd "$(dirname "$0")/../cf" && pwd)"

# ---- load tags from cf/tags.json and convert to --label flags ---------------
TAGS_FILE="${CF_DIR}/tags.json"
if [[ ! -f "${TAGS_FILE}" ]]; then
  echo "ERROR: ${TAGS_FILE} not found." >&2; exit 1
fi
# Build array of --label key=value args
DOCKER_LABELS=()
while IFS= read -r pair; do
  DOCKER_LABELS+=(--label "${pair}")
done < <(jq -r '.[] | "\(.Key)=\(.Value)"' "${TAGS_FILE}")

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Building & pushing image — tag: ${TAG}  region: ${REGION}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Resolve ECR URI from stack output
ECR_URI=$(aws cloudformation describe-stacks \
  --stack-name "${ECR_STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`RepositoryUri`].OutputValue' \
  --output text)

if [[ -z "${ECR_URI}" ]]; then
  echo "ERROR: Could not resolve ECR URI from stack ${ECR_STACK}" >&2
  echo "       Run ./bin/deploy-infra.sh first." >&2
  exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo ""
echo "▶ Logging in to ECR…"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

echo ""
echo "▶ Building image (platform=${PLATFORM})…"
docker build \
  --platform "${PLATFORM}" \
  "${DOCKER_LABELS[@]}" \
  -t "${ECR_URI}:${TAG}" \
  -t "${ECR_URI}:latest" \
  "${APP_DIR}"

echo ""
echo "▶ Pushing ${ECR_URI}:${TAG}…"
docker push "${ECR_URI}:${TAG}"

if [[ "${TAG}" != "latest" ]]; then
  echo "▶ Pushing ${ECR_URI}:latest…"
  docker push "${ECR_URI}:latest"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✅  Image pushed: ${ECR_URI}:${TAG}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
