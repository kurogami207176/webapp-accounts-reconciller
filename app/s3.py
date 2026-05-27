"""
s3.py — S3 upload helpers for the reconciler bucket.

Path conventions:
  uploads/
    group=<group_id>/upload_type=<type>/payment_type=<ptype>/year=<Y>/month=<M>/<uuid>.<ext>
  info/
    group=<group_id>/upload_type=<type>/payment_type=<ptype>/year=<Y>/month=<M>/<uuid>.json
  transactions/
    group=<group_id>/year=<Y>/month=<M>/<statement_file_id>.json
  matches/
    year=<Y>/month=<M>/<run_id>.json

For bank statements (admin uploads), group_id is set to "_admin" and
payment_type maps to the statement_type.

Environment variables:
  S3_BUCKET_NAME       — required in production (injected by ECS task def)
  AWS_ENDPOINT_URL     — optional; set to http://localhost:4566 for LocalStack
  AWS_DEFAULT_REGION   — defaults to ap-southeast-2
"""

import json
import logging
import os
from datetime import date
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _s3_client():
    """Return a cached boto3 S3 client. Supports LocalStack via AWS_ENDPOINT_URL."""
    import boto3  # noqa: PLC0415
    kwargs: dict = {
        "region_name": os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2"),
    }
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint:
        logger.info("Using custom S3 endpoint: %s", endpoint)
        kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **kwargs)


def _bucket() -> str:
    name = os.environ.get("S3_BUCKET_NAME")
    if not name:
        raise RuntimeError("S3_BUCKET_NAME environment variable is not set")
    return name


def _partition_prefix(
    *,
    group_id: str,
    upload_type: str,
    payment_type: str,
    for_date: date,
) -> str:
    """
    Build the Hive-style partition path segment.
    e.g. group=branch-manila/upload_type=bank_deposit/payment_type=bdo/year=2026/month=05
    """
    return (
        f"group={group_id}"
        f"/upload_type={upload_type}"
        f"/payment_type={payment_type}"
        f"/year={for_date.year}"
        f"/month={for_date.month:02d}"
    )


def upload_file(
    file_bytes: bytes,
    *,
    group_id: str,
    upload_type: str,
    payment_type: str,
    for_date: date,
    file_id: str,
    extension: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload a raw file (photo or bank statement) to the uploads/ prefix.
    Returns the S3 key.
    """
    partition = _partition_prefix(
        group_id=group_id,
        upload_type=upload_type,
        payment_type=payment_type,
        for_date=for_date,
    )
    key = f"uploads/{partition}/{file_id}.{extension}"
    _s3_client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    logger.info("Uploaded file to s3://%s/%s", _bucket(), key)
    return key


def upload_info_json(
    info: dict,
    *,
    group_id: str,
    upload_type: str,
    payment_type: str,
    for_date: date,
    file_id: str,
) -> str:
    """
    Upload a metadata JSON to the info/ prefix.
    Returns the S3 key.
    """
    partition = _partition_prefix(
        group_id=group_id,
        upload_type=upload_type,
        payment_type=payment_type,
        for_date=for_date,
    )
    key = f"info/{partition}/{file_id}.json"
    _s3_client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=json.dumps(info, default=str),
        ContentType="application/json",
    )
    logger.info("Uploaded info JSON to s3://%s/%s", _bucket(), key)
    return key


def upload_transactions_json(
    transactions: list[dict],
    *,
    group_id: str,
    statement_file_id: str,
    for_date: date,
) -> str:
    """
    Upload extracted transactions from a bank statement to the transactions/ prefix.
    Returns the S3 key.
    """
    key = (
        f"transactions"
        f"/group={group_id}"
        f"/year={for_date.year}"
        f"/month={for_date.month:02d}"
        f"/{statement_file_id}.json"
    )
    _s3_client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=json.dumps(transactions, default=str),
        ContentType="application/json",
    )
    logger.info("Uploaded %d transactions to s3://%s/%s", len(transactions), _bucket(), key)
    return key


def upload_match_results_json(
    results: dict,
    *,
    run_id: str,
    for_date: date,
) -> str:
    """
    Upload match run results to the matches/ prefix.
    Returns the S3 key.
    """
    key = (
        f"matches"
        f"/year={for_date.year}"
        f"/month={for_date.month:02d}"
        f"/{run_id}.json"
    )
    _s3_client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=json.dumps(results, default=str),
        ContentType="application/json",
    )
    logger.info("Uploaded match results to s3://%s/%s", _bucket(), key)
    return key


def get_presigned_url(key: str, expiry_seconds: int = 3600) -> str:
    """Generate a pre-signed GET URL for a private S3 object."""
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": _bucket(), "Key": key},
        ExpiresIn=expiry_seconds,
    )


def download_file(key: str) -> bytes:
    """Download an S3 object and return its bytes."""
    response = _s3_client().get_object(Bucket=_bucket(), Key=key)
    return response["Body"].read()
