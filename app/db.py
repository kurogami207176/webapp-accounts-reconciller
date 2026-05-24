"""
db.py — Database connection pool.

Reads connection credentials from AWS Secrets Manager at startup.
The secret ARN is provided via the DB_SECRET_ARN environment variable,
which is injected by the ECS task definition from the database stack output.

Secret format (standard RDS JSON written by database.yml):
  {
    "username": "dbadmin",
    "password": "...",
    "host":     "...",
    "port":     "5432",
    "dbname":   "appdb",
    "engine":   "postgres"
  }

Usage:
    from db import get_pool

    pool = get_pool()          # returns None if DB_SECRET_ARN is not set
    if pool:
        with pool.connection() as conn:
            row = conn.execute("SELECT 1").fetchone()
"""

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

# Lazily imported so the module loads cleanly in environments without these packages
_psycopg = None
_boto3 = None


def _import_deps():
    global _psycopg, _boto3
    if _psycopg is None:
        import psycopg          # noqa: PLC0415
        import psycopg.pool     # noqa: PLC0415
        import boto3            # noqa: PLC0415
        _psycopg = psycopg
        _boto3 = boto3


@lru_cache(maxsize=1)
def _fetch_secret(secret_arn: str) -> dict:
    """Fetch and parse the Secrets Manager secret. Cached for the process lifetime."""
    _import_deps()
    client = _boto3.client("secretsmanager", region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2"))
    response = client.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


@lru_cache(maxsize=1)
def get_pool():
    """
    Return a psycopg ConnectionPool, or None if DB_SECRET_ARN is not configured.

    The pool is created once and reused across requests. SSL is always required
    (enforced both here and by rds.force_ssl=1 in the cluster parameter group).
    """
    secret_arn = os.environ.get("DB_SECRET_ARN")
    if not secret_arn:
        logger.info("DB_SECRET_ARN not set — database pool not initialised")
        return None

    try:
        _import_deps()
        secret = _fetch_secret(secret_arn)

        conninfo = (
            f"host={secret['host']} "
            f"port={secret.get('port', 5432)} "
            f"dbname={secret['dbname']} "
            f"user={secret['username']} "
            f"password={secret['password']} "
            f"sslmode=require"
        )

        pool = _psycopg.pool.ConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=10,
            # Open connections lazily — don't block startup if DB is unreachable
            open=False,
        )
        pool.open(wait=True, timeout=10)
        logger.info("Database connection pool initialised (host=%s dbname=%s)", secret["host"], secret["dbname"])
        return pool

    except Exception:
        logger.exception("Failed to initialise database connection pool")
        raise
