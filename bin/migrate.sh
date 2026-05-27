#!/usr/bin/env bash
# bin/migrate.sh
# ---------------------------------------------------------------------------
# Run Alembic database migrations.
#
# Local:       Uses DATABASE_URL env var (or .env file)
# Production:  Runs as an ECS one-off task with DB_SECRET_ARN
#
# Usage:
#   ./bin/migrate.sh                      # local (reads DATABASE_URL or .env)
#   ./bin/migrate.sh --ecs --env staging  # trigger via ECS one-off task
#   ./bin/migrate.sh upgrade head         # pass alembic args (default: upgrade head)
# ---------------------------------------------------------------------------
set -euo pipefail

MODE="local"
ENV="production"
REGION="ap-southeast-2"
APP_NAME=""
ALEMBIC_ARGS="upgrade head"

while [[ $# -gt 0 ]]; do
  case $1 in
    --ecs)      MODE="ecs";    shift 1 ;;
    --env)      ENV="$2";      shift 2 ;;
    --region)   REGION="$2";   shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    *)          ALEMBIC_ARGS="$*"; break ;;
  esac
done

# ── Local mode ───────────────────────────────────────────────────────────────
if [[ "${MODE}" == "local" ]]; then
  APP_DIR="$(cd "$(dirname "$0")/../app" && pwd)"

  # Load .env if present
  if [[ -f "$(dirname "$0")/../.env" ]]; then
    set -a; source "$(dirname "$0")/../.env"; set +a
    echo "▶ Loaded .env"
  fi

  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL is not set. Add it to .env or export it." >&2
    exit 1
  fi

  cd "${APP_DIR}"
  if [[ -d ".venv" ]]; then
    source .venv/bin/activate
  fi

  echo "▶ Running: alembic ${ALEMBIC_ARGS}"
  alembic ${ALEMBIC_ARGS}
  echo "✅  Migration complete"
  exit 0
fi

# ── ECS mode — trigger one-off Fargate task ───────────────────────────────────
if [[ -z "${APP_NAME}" ]]; then
  APP_NAME=$(git remote get-url origin 2>/dev/null \
    | sed 's|.*[:/]\([^/]*\)\.git$|\1|; s|.*[:/]\([^/]*\)$|\1|')
fi

ECS_STACK="${APP_NAME}-ecs-${ENV}"

echo "▶ Resolving ECS cluster from stack: ${ECS_STACK}"
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

echo "▶ Running migration ECS task"
echo "  Cluster : ${CLUSTER}"
echo "  Task def: ${TASK_DEF}"

TASK_ARN=$(aws ecs run-task \
  --cluster "${CLUSTER}" \
  --task-definition "${TASK_DEF}" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS// /,}],securityGroups=[${SG}],assignPublicIp=DISABLED}" \
  --overrides "{\"containerOverrides\":[{\"name\":\"app\",\"command\":[\"python\",\"-m\",\"alembic\",\"upgrade\",\"head\"]}]}" \
  --region "${REGION}" \
  --query 'tasks[0].taskArn' \
  --output text)

echo "  Task ARN: ${TASK_ARN}"
echo "  Waiting for task to complete…"

aws ecs wait tasks-stopped \
  --cluster "${CLUSTER}" \
  --tasks "${TASK_ARN}" \
  --region "${REGION}"

EXIT_CODE=$(aws ecs describe-tasks \
  --cluster "${CLUSTER}" \
  --tasks "${TASK_ARN}" \
  --region "${REGION}" \
  --query 'tasks[0].containers[0].exitCode' \
  --output text)

if [[ "${EXIT_CODE}" == "0" ]]; then
  echo "✅  Migration task completed successfully"
else
  echo "❌  Migration task failed with exit code: ${EXIT_CODE}" >&2
  exit 1
fi
