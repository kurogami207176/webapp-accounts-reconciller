"""
Alembic env.py — wires migrations to the database.

Connection URL resolution (in order):
  1. DATABASE_URL env var — used for local development / CI
     e.g. postgresql+psycopg://postgres:postgres@localhost:5432/appdb
  2. DB_SECRET_ARN env var — used in production (ECS tasks)
     Fetches credentials from AWS Secrets Manager using the same helper as db.py.

Usage:
  Local:       DATABASE_URL=postgresql+psycopg://... alembic upgrade head
  Production:  DB_SECRET_ARN=arn:aws:... alembic upgrade head
               (run as ECS one-off task via bin/migrate.sh)
"""

import json
import logging
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Ensure app/ is on the path so db.py helpers are importable
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

config = context.config

# Alembic logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")

# We don't use SQLAlchemy models/metadata for autogenerate — pure SQL migrations.
target_metadata = None


# ---------------------------------------------------------------------------
# Build the database URL
# ---------------------------------------------------------------------------

def _get_url() -> str:
    """
    Resolve the database connection URL.

    Priority:
      1. DATABASE_URL env var (local dev / CI)
      2. DB_SECRET_ARN env var → fetch from AWS Secrets Manager (production)
    """
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        logger.info("Using DATABASE_URL from environment")
        return database_url

    secret_arn = os.environ.get("DB_SECRET_ARN")
    if secret_arn:
        logger.info("Fetching DB credentials from Secrets Manager: %s", secret_arn)
        import boto3  # noqa: PLC0415
        client = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2"),
        )
        secret = json.loads(
            client.get_secret_value(SecretId=secret_arn)["SecretString"]
        )
        return (
            f"postgresql+psycopg://{secret['username']}:{secret['password']}"
            f"@{secret['host']}:{secret.get('port', 5432)}/{secret['dbname']}"
            f"?sslmode=require"
        )

    raise RuntimeError(
        "No database URL configured. Set DATABASE_URL (local) "
        "or DB_SECRET_ARN (production) environment variable."
    )


# ---------------------------------------------------------------------------
# Alembic migration runners
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL without a live connection)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database connection."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No connection pooling for migrations
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
