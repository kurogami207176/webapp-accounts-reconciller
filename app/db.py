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
_psycopg_pool = None
_boto3 = None


def _import_deps():
    global _psycopg, _psycopg_pool, _boto3
    if _psycopg is None:
        import psycopg          # noqa: PLC0415
        import psycopg_pool     # noqa: PLC0415  (psycopg[pool] extra — separate top-level package)
        import boto3            # noqa: PLC0415
        _psycopg = psycopg
        _psycopg_pool = psycopg_pool
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
    Return a psycopg ConnectionPool, or None if no database is configured.

    Connection source priority:
      1. DATABASE_URL env var  — used in local dev / CI
      2. DB_SECRET_ARN env var — used in ECS (fetches credentials from Secrets Manager)

    SSL is required when connecting via DB_SECRET_ARN (Aurora). It is not
    enforced when DATABASE_URL is set so local dev works without certificates.
    """
    _import_deps()

    # ── 1. Direct connection string (local dev / CI) ──────────────────────
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        try:
            # psycopg_pool expects a libpq-style DSN, not a SQLAlchemy URL.
            # Strip the SQLAlchemy driver prefix if present
            # e.g. "postgresql+psycopg://..." → "postgresql://..."
            conninfo = database_url
            if "+psycopg" in conninfo:
                conninfo = conninfo.replace("postgresql+psycopg://", "postgresql://", 1)

            pool = _psycopg_pool.ConnectionPool(
                conninfo=conninfo,
                min_size=1,
                max_size=10,
                open=False,
            )
            pool.open(wait=True, timeout=10)
            logger.info("Database connection pool initialised from DATABASE_URL")
            return pool
        except Exception:
            logger.exception("Failed to initialise database connection pool from DATABASE_URL")
            raise

    # ── 2. Secrets Manager ARN (ECS / production) ─────────────────────────
    secret_arn = os.environ.get("DB_SECRET_ARN")
    if not secret_arn:
        logger.info("Neither DATABASE_URL nor DB_SECRET_ARN set — database pool not initialised")
        return None

    try:
        secret = _fetch_secret(secret_arn)

        conninfo = (
            f"host={secret['host']} "
            f"port={secret.get('port', 5432)} "
            f"dbname={secret['dbname']} "
            f"user={secret['username']} "
            f"password={secret['password']} "
            f"sslmode=require"
        )

        pool = _psycopg_pool.ConnectionPool(
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
