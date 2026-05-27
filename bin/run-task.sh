#!/usr/bin/env bash
# bin/run-task.sh
# ---------------------------------------------------------------------------
# Trigger an ECS one-off Fargate task (parser or matcher) for a given file ID.
#
# Usage:
#   ./bin/run-task.sh --task parser  --file-id <uuid> [--env staging|production]
#   ./bin/run-task.sh --task matcher --file-id <uuid> [--env staging|production]
#
# For local development (no ECS), run directly:
#   cd app && DATABASE_URL=... S3_BUCKET_NAME=... python -m tasks.parser --file-id <uuid>
# ---------------------------------------------------------------------------
set -euo pipefail

TASK=""
FILE_ID=""
ENV="production"
REGION="ap-southeast-2"
APP_NAME=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --task)     TASK="$2";     shift 2 ;;
    --file-id)  FILE_ID="$2";  shift 2 ;;
    --env)      ENV="$2";      shift 2 ;;
    --region)   REGION="$2";   shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${TASK}" || -z "${FILE_ID}" ]]; then
  echo "Usage: $0 --task parser|matcher --file-id <uuid> [--env staging|production]"
  exit 1
fi

if [[ "${TASK}" != "parser" && "${TASK}" != "matcher" ]]; then
  echo "ERROR: --task must be 'parser' or 'matcher'" >&2; exit 1
fi

if [[ -z "${APP_NAME}" ]]; then
  APP_NAME=$(git remote get-url origin 2>/dev/null \
    | sed 's|.*[:/]\([^/]*\)\.git$|\1|; s|.*[:/]\([^/]*\)$|\1|')
fi

ECS_STACK="${APP_NAME}-ecs-${ENV}"

CLUSTER=$(aws cloudformation describe-stacks \
  --stack-name "${ECS_STACK}" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`ServiceArn`].OutputValue' \
  --output text | sed 's|arn:aws:ecs:[^:]*:[^:]*:service/\([^/]*\)/.*|\1|')

TASK_DEF=$(aws ecs list-task-definitions \
  --family-prefix "${APP_NAME}-${ENV}" \
  --status ACTIVE \
  --region "${REGION}" \
  --query 'taskDefinitionArns[-1]' \
  --output text)

SUBNETS=$(aws cloudformation describe-stacks \
  --stack-name "webapp-${ENV}-network" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`PrivateSubnetIds`].OutputValue' \
  --output text | tr ',' ' ')

SG=$(aws cloudformation describe-stacks \
  --stack-name "webapp-${ENV}-network" \
  --region "${REGION}" \
  --query 'Stacks[0].Outputs[?OutputKey==`AppSecurityGroupId`].OutputValue' \
  --output text)

MODULE="tasks.${TASK}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Triggering ECS task: ${MODULE}"
echo " File ID : ${FILE_ID}"
echo " Cluster : ${CLUSTER}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

TASK_ARN=$(aws ecs run-task \
  --cluster "${CLUSTER}" \
  --task-definition "${TASK_DEF}" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS// /,}],securityGroups=[${SG}],assignPublicIp=DISABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"app\",\"command\":[\"python\",\"-m\",\"${MODULE}\",\"--file-id\",\"${FILE_ID}\"]}]}" \
  --region "${REGION}" \
  --query 'tasks[0].taskArn' \
  --output text)

echo "  Task ARN: ${TASK_ARN}"
echo ""
echo "Monitor: https://console.aws.amazon.com/ecs/home?region=${REGION}#/clusters/${CLUSTER}/tasks/${TASK_ARN##*/}/details"
