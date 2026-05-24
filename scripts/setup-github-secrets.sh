#!/usr/bin/env bash
# Pulls AWS credentials from SSM Parameter Store and sets them as GitHub Actions secrets
# on the current repo (or a specified one).
#
# Usage:
#   ./scripts/setup-github-secrets.sh              # uses current git repo
#   ./scripts/setup-github-secrets.sh owner/repo   # targets a specific repo
#
# Prerequisites:
#   - AWS CLI configured (ap-southeast-2)
#   - gh CLI installed and authenticated: brew install gh && gh auth login

set -euo pipefail


# --- Resolve target repo ---
if [[ $# -ge 1 ]]; then
  REPO="$1"
else
  # Infer from the current git remote
  REPO=$(git remote get-url origin 2>/dev/null | sed 's|.*github.com[:/]\(.*\)\.git|\1|' | sed 's|.*github.com[:/]\(.*\)|\1|')
  if [[ -z "$REPO" ]]; then
    echo "❌  Could not detect repo from git remote. Pass it explicitly:"
    echo "    $0 owner/repo"
    exit 1
  fi
  echo "📍  Detected repo: $REPO"
fi

# --- Prerequisites check ---
if ! command -v gh &>/dev/null; then
  echo "❌  gh CLI not found. Install it:"
  echo "    brew install gh && gh auth login"
  exit 1
fi

if ! command -v aws &>/dev/null; then
  echo "❌  aws CLI not found."
  exit 1
fi

# --- Pull all values from SSM ---
# Bootstrap: region is the one thing we need to know upfront to query SSM
SSM_REGION="ap-southeast-2"
echo "🔐  Fetching values from SSM Parameter Store (${SSM_REGION})..."

ACCESS_KEY=$(aws ssm get-parameter \
  --name "/github-actions/aws-access-key-id" \
  --with-decryption \
  --query "Parameter.Value" \
  --output text \
  --region "$SSM_REGION")

SECRET_KEY=$(aws ssm get-parameter \
  --name "/github-actions/aws-secret-access-key" \
  --with-decryption \
  --query "Parameter.Value" \
  --output text \
  --region "$SSM_REGION")

ACCOUNT_ID=$(aws ssm get-parameter \
  --name "/github-actions/aws-account-id" \
  --with-decryption \
  --query "Parameter.Value" \
  --output text \
  --region "$SSM_REGION")

REGION=$(aws ssm get-parameter \
  --name "/github-actions/aws-region" \
  --query "Parameter.Value" \
  --output text \
  --region "$SSM_REGION")

ANTHROPIC_API_KEY=$(aws ssm get-parameter \
  --name "/github-actions/anthropic-api-key" \
  --with-decryption \
  --query "Parameter.Value" \
  --output text \
  --region "$SSM_REGION")

# --- Push to GitHub ---
echo "📦  Setting GitHub Actions secrets on: $REPO"

gh secret set AWS_ACCESS_KEY_ID     --repo "$REPO" --body "$ACCESS_KEY"
gh secret set AWS_SECRET_ACCESS_KEY --repo "$REPO" --body "$SECRET_KEY"
gh secret set AWS_REGION            --repo "$REPO" --body "$REGION"
gh secret set AWS_ACCOUNT_ID        --repo "$REPO" --body "$ACCOUNT_ID"
gh secret set ANTHROPIC_API_KEY     --repo "$REPO" --body "$ANTHROPIC_API_KEY"

echo
echo "✅  Done! Secrets set on $REPO:"
echo "    AWS_ACCESS_KEY_ID"
echo "    AWS_SECRET_ACCESS_KEY"
echo "    AWS_REGION"
echo "    AWS_ACCOUNT_ID"
echo "    ANTHROPIC_API_KEY"
