#!/usr/bin/env bash
# Sets GitHub Actions secrets and variables on the correct environments
# (staging / production) so the CD workflows can authenticate to AWS via OIDC.
#
# What gets set:
#   Repo-level secret : ANTHROPIC_API_KEY
#   Per-environment   : AWS_DEPLOY_ROLE_ARN  (secret)
#                       AWS_REGION           (variable, not secret — it's not sensitive)
#
# Usage:
#   ./scripts/setup-github-secrets.sh              # uses current git repo
#   ./scripts/setup-github-secrets.sh owner/repo   # targets a specific repo
#
# Prerequisites:
#   - AWS CLI configured (ap-southeast-2)
#   - gh CLI installed and authenticated: brew install gh && gh auth login
#   - The iam-github-oidc CloudFormation stack must already be deployed for
#     each environment so the deploy role ARNs exist.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve target repo
# ---------------------------------------------------------------------------
if [[ $# -ge 1 ]]; then
  REPO="$1"
else
  REPO=$(git remote get-url origin 2>/dev/null \
    | sed 's|.*github.com[:/]\(.*\)\.git|\1|' \
    | sed 's|.*github.com[:/]\(.*\)|\1|')
  if [[ -z "$REPO" ]]; then
    echo "❌  Could not detect repo from git remote. Pass it explicitly:"
    echo "    $0 owner/repo"
    exit 1
  fi
  echo "📍  Detected repo: $REPO"
fi

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------
for cmd in gh aws; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "❌  $cmd CLI not found."
    exit 1
  fi
done

SSM_REGION="ap-southeast-2"

# ---------------------------------------------------------------------------
# Helper: fetch a secret from SSM
# ---------------------------------------------------------------------------
ssm_get() {
  aws ssm get-parameter \
    --name "$1" \
    --with-decryption \
    --query "Parameter.Value" \
    --output text \
    --region "$SSM_REGION"
}

# ---------------------------------------------------------------------------
# Pull values from SSM
# ---------------------------------------------------------------------------
echo "🔐  Fetching values from SSM Parameter Store (${SSM_REGION})..."

ANTHROPIC_API_KEY=$(ssm_get "/github-actions/anthropic-api-key")

STAGING_ROLE_ARN=$(ssm_get "/github-actions/staging/deploy-role-arn")
STAGING_REGION=$(ssm_get "/github-actions/staging/aws-region")

PRODUCTION_ROLE_ARN=$(ssm_get "/github-actions/production/deploy-role-arn")
PRODUCTION_REGION=$(ssm_get "/github-actions/production/aws-region")

# ---------------------------------------------------------------------------
# Repo-level secrets (not environment-scoped)
# ---------------------------------------------------------------------------
echo
echo "📦  Setting repo-level secrets on: $REPO"
gh secret set ANTHROPIC_API_KEY --repo "$REPO" --body "$ANTHROPIC_API_KEY"
echo "    ✅  ANTHROPIC_API_KEY"

# ---------------------------------------------------------------------------
# Staging environment
# ---------------------------------------------------------------------------
echo
echo "🚧  Setting staging environment secrets/vars on: $REPO"

gh secret set AWS_DEPLOY_ROLE_ARN \
  --repo "$REPO" \
  --env staging \
  --body "$STAGING_ROLE_ARN"
echo "    ✅  AWS_DEPLOY_ROLE_ARN (staging)"

gh variable set AWS_REGION \
  --repo "$REPO" \
  --env staging \
  --body "$STAGING_REGION"
echo "    ✅  AWS_REGION (staging)"

# ---------------------------------------------------------------------------
# Production environment
# ---------------------------------------------------------------------------
echo
echo "🚀  Setting production environment secrets/vars on: $REPO"

gh secret set AWS_DEPLOY_ROLE_ARN \
  --repo "$REPO" \
  --env production \
  --body "$PRODUCTION_ROLE_ARN"
echo "    ✅  AWS_DEPLOY_ROLE_ARN (production)"

gh variable set AWS_REGION \
  --repo "$REPO" \
  --env production \
  --body "$PRODUCTION_REGION"
echo "    ✅  AWS_REGION (production)"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "✅  Done! Summary:"
echo
echo "   Repo-level secrets:"
echo "     ANTHROPIC_API_KEY"
echo
echo "   staging environment:"
echo "     secret: AWS_DEPLOY_ROLE_ARN"
echo "     var:    AWS_REGION"
echo
echo "   production environment:"
echo "     secret: AWS_DEPLOY_ROLE_ARN"
echo "     var:    AWS_REGION"
echo
echo "   ⚠️  Make sure these SSM paths exist before running:"
echo "     /github-actions/anthropic-api-key"
echo "     /github-actions/staging/deploy-role-arn"
echo "     /github-actions/staging/aws-region"
echo "     /github-actions/production/deploy-role-arn"
echo "     /github-actions/production/aws-region"
