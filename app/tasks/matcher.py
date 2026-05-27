"""
tasks/matcher.py — ECS one-off task: match bank transactions against branch submissions.

Triggered after a bank statement file reaches INSERTED status.
Runs as an ECS task with CMD override:
  python -m tasks.matcher --file-id <uuid>

Matching logic:
  For each unmatched bank_transaction from this file:
    Find a submission where ALL of the following match exactly:
      - transaction_date  == transaction_timestamp.date()
      - transaction_time  within ±5 minutes of transaction_timestamp.time()
      - amount            == amount
      - payment_type      == source (bank transaction source maps to submission payment_type)
      - status            == 'UNMATCHED'
    If found → mark both as matched (1:1 matching, first-match wins)

Results are written to S3 /matches/ and the match counts are logged.

Environment variables:
  DB_SECRET_ARN or DATABASE_URL  — database connection
  S3_BUCKET_NAME                 — S3 bucket
  AWS_DEFAULT_REGION             — AWS region
  AWS_ENDPOINT_URL               — optional, for LocalStack
"""

import argparse
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone

from db import get_pool
from s3 import upload_match_results_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
)
logger = logging.getLogger("tasks.matcher")

MATCH_TIME_TOLERANCE = timedelta(minutes=5)


def _get_unmatched_transactions(conn, file_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, transaction_id, transaction_timestamp, amount, source
        FROM bank_transactions
        WHERE bank_statement_file_id = %s
          AND matching_submission_id IS NULL
        ORDER BY transaction_timestamp
        """,
        (file_id,),
    ).fetchall()
    return [
        dict(zip(("id", "transaction_id", "transaction_timestamp", "amount", "source"), r))
        for r in rows
    ]


def _find_matching_submission(conn, txn: dict) -> str | None:
    """
    Find the first UNMATCHED submission that matches this transaction.
    Returns the submission UUID or None.
    """
    ts: datetime = txn["transaction_timestamp"]
    date_val = ts.date()
    time_min = (ts - MATCH_TIME_TOLERANCE).time()
    time_max = (ts + MATCH_TIME_TOLERANCE).time()

    row = conn.execute(
        """
        SELECT id FROM submissions
        WHERE transaction_date = %s
          AND transaction_time BETWEEN %s AND %s
          AND amount = %s
          AND payment_type = %s
          AND status = 'UNMATCHED'
        ORDER BY created_at
        LIMIT 1
        """,
        (date_val, time_min, time_max, txn["amount"], txn["source"]),
    ).fetchone()
    return row[0] if row else None


def _apply_match(conn, transaction_id: str, submission_id: str) -> None:
    conn.execute(
        "UPDATE bank_transactions SET matching_submission_id = %s WHERE id = %s",
        (submission_id, transaction_id),
    )
    conn.execute(
        "UPDATE submissions SET status = 'MATCHED' WHERE id = %s",
        (submission_id,),
    )


def run(file_id: str) -> None:
    pool = get_pool()
    if pool is None:
        raise RuntimeError("Database pool could not be initialised — check DB_SECRET_ARN")

    run_id = str(uuid.uuid4())
    matched = 0
    unmatched_txns = 0

    with pool.connection() as conn:
        transactions = _get_unmatched_transactions(conn, file_id)
        logger.info("Found %d unmatched transactions for file %s", len(transactions), file_id)

        match_details = []

        for txn in transactions:
            submission_id = _find_matching_submission(conn, txn)
            if submission_id:
                _apply_match(conn, txn["id"], submission_id)
                matched += 1
                match_details.append({
                    "transaction_id": txn["id"],
                    "bank_transaction_id": txn["transaction_id"],
                    "submission_id": submission_id,
                    "amount": str(txn["amount"]),
                    "timestamp": txn["transaction_timestamp"].isoformat(),
                    "matched": True,
                })
                logger.debug("Matched txn %s → submission %s", txn["id"], submission_id)
            else:
                unmatched_txns += 1
                match_details.append({
                    "transaction_id": txn["id"],
                    "bank_transaction_id": txn["transaction_id"],
                    "submission_id": None,
                    "amount": str(txn["amount"]),
                    "timestamp": txn["transaction_timestamp"].isoformat(),
                    "matched": False,
                })

        conn.commit()

    logger.info(
        "Matching complete — matched: %d, unmatched: %d (file: %s)",
        matched, unmatched_txns, file_id,
    )

    # ── Write match results to S3 ──────────────────────────────────────────
    try:
        results = {
            "run_id": run_id,
            "file_id": file_id,
            "run_at": datetime.now(timezone.utc).isoformat(),
            "matched_count": matched,
            "unmatched_count": unmatched_txns,
            "total": len(transactions),
            "details": match_details,
        }
        upload_match_results_json(
            results,
            run_id=run_id,
            for_date=datetime.now(timezone.utc).date(),
        )
    except Exception as exc:
        logger.warning("Failed to write match results to S3 (non-fatal): %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Match bank transactions against submissions")
    parser.add_argument("--file-id", required=True, help="bank_statement_files.id UUID")
    args = parser.parse_args()
    try:
        run(args.file_id)
    except Exception:
        logger.exception("Matcher task failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
