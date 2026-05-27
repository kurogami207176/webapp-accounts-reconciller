"""
tasks/parser.py — ECS one-off task: parse a bank statement file.

Triggered by the admin API after a bank statement file is uploaded.
Runs as an ECS task with CMD override:
  python -m tasks.parser --file-id <uuid>

Flow:
  1. Load bank_statement_files row (must be PENDING or FAILED)
  2. Download the file from S3
  3. Call the appropriate parser (based on statement_type)
  4. Update status → PARSED
  5. Insert bank_transactions rows
  6. Write transactions JSON to S3 /transactions/...
  7. Update status → INSERTED
  8. On any error: set status → FAILED with parse_error message

Environment variables:
  DB_SECRET_ARN or DATABASE_URL  — database connection
  S3_BUCKET_NAME                 — S3 bucket
  AWS_DEFAULT_REGION             — AWS region (default: ap-southeast-2)
  AWS_ENDPOINT_URL               — optional, for LocalStack
"""

import argparse
import logging
import sys
import uuid
from datetime import datetime, timezone

from db import get_pool
from s3 import download_file, upload_transactions_json
from tasks.parsers.base import get_parser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
)
logger = logging.getLogger("tasks.parser")


def _load_file_record(conn, file_id: str) -> dict:
    row = conn.execute(
        """
        SELECT id, statement_type, file_s3_key, file_password, status, uploaded_by
        FROM bank_statement_files
        WHERE id = %s
        """,
        (file_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"bank_statement_files row not found: {file_id}")
    return dict(zip(
        ("id", "statement_type", "file_s3_key", "file_password", "status", "uploaded_by"),
        row,
    ))


def _set_status(conn, file_id: str, status: str, **extra_fields) -> None:
    set_clauses = ["status = %s"]
    values: list = [status]
    for col, val in extra_fields.items():
        set_clauses.append(f"{col} = %s")
        values.append(val)
    values.append(file_id)
    conn.execute(
        f"UPDATE bank_statement_files SET {', '.join(set_clauses)} WHERE id = %s",
        values,
    )


def _insert_transactions(conn, transactions, file_id: str, source: str) -> None:
    for txn in transactions:
        txn_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO bank_transactions (
                id, transaction_id, transaction_timestamp, transaction_type,
                amount, currency, description, balance,
                record_created_timestamp_utc, group_id, source,
                bank_statement_file_id, created_at
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, now()
            )
            ON CONFLICT (id) DO NOTHING
            """,
            (
                txn_id,
                txn.transaction_id,
                txn.transaction_timestamp,
                txn.transaction_type,
                txn.amount,
                txn.currency,
                txn.description,
                txn.balance,
                txn.record_created_timestamp_utc,
                txn.group_id,
                source,
                file_id,
            ),
        )


def run(file_id: str) -> None:
    pool = get_pool()
    if pool is None:
        raise RuntimeError("Database pool could not be initialised — check DB_SECRET_ARN")

    with pool.connection() as conn:
        record = _load_file_record(conn, file_id)
        logger.info(
            "Processing file %s (type=%s, status=%s)",
            file_id, record["statement_type"], record["status"],
        )

        if record["status"] not in ("PENDING", "FAILED"):
            logger.warning("Skipping — file is already in status: %s", record["status"])
            return

        # ── Download file from S3 ──────────────────────────────────────────
        try:
            file_bytes = download_file(record["file_s3_key"])
            logger.info("Downloaded %d bytes from S3", len(file_bytes))
        except Exception as exc:
            logger.exception("Failed to download file from S3")
            _set_status(conn, file_id, "FAILED", parse_error=f"S3 download error: {exc}")
            conn.commit()
            raise

        # ── Parse ──────────────────────────────────────────────────────────
        try:
            parser = get_parser(record["statement_type"])
            transactions = parser.parse(file_bytes, password=record.get("file_password"))
            logger.info("Parsed %d transactions", len(transactions))
        except Exception as exc:
            logger.exception("Parsing failed")
            _set_status(conn, file_id, "FAILED", parse_error=str(exc))
            conn.commit()
            raise

        _set_status(conn, file_id, "PARSED", parsed_at=datetime.now(timezone.utc))
        conn.commit()

        # ── Insert transactions ────────────────────────────────────────────
        try:
            _insert_transactions(conn, transactions, file_id, record["statement_type"])
            _set_status(conn, file_id, "INSERTED", inserted_at=datetime.now(timezone.utc))
            conn.commit()
            logger.info("Inserted %d transactions into DB", len(transactions))
        except Exception as exc:
            logger.exception("DB insertion failed")
            _set_status(conn, file_id, "FAILED", parse_error=f"DB insert error: {exc}")
            conn.commit()
            raise

        # ── Write transactions to S3 ───────────────────────────────────────
        try:
            txn_dicts = [
                {
                    "transaction_id": t.transaction_id,
                    "transaction_timestamp": t.transaction_timestamp.isoformat(),
                    "transaction_type": t.transaction_type,
                    "amount": str(t.amount),
                    "currency": t.currency,
                    "description": t.description,
                    "balance": str(t.balance),
                    "source": record["statement_type"],
                    "bank_statement_file_id": file_id,
                }
                for t in transactions
            ]
            upload_transactions_json(
                txn_dicts,
                group_id="_admin",
                statement_file_id=file_id,
                for_date=datetime.now(timezone.utc).date(),
            )
        except Exception as exc:
            # S3 write failure is non-fatal — DB is the source of truth
            logger.warning("Failed to write transactions to S3 (non-fatal): %s", exc)

    logger.info("Parser task complete for file %s", file_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a bank statement file")
    parser.add_argument("--file-id", required=True, help="bank_statement_files.id UUID")
    args = parser.parse_args()
    try:
        run(args.file_id)
    except Exception:
        logger.exception("Parser task failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
