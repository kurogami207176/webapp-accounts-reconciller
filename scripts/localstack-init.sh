#!/bin/bash
# LocalStack init script — runs when LocalStack is ready.
# Creates the S3 bucket that the app writes to.

set -e
BUCKET="local-reconciler"
REGION="ap-southeast-2"

echo "▶ Creating S3 bucket: $BUCKET"
awslocal s3 mb "s3://${BUCKET}" --region "$REGION" 2>/dev/null || true
echo "  ✓ Bucket ready: s3://${BUCKET}"
