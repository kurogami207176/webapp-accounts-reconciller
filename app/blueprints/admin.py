"""
blueprints/admin.py — Admin user routes.

Routes:
  GET  /admin/                    → redirect to dashboard
  GET  /admin/dashboard           → statement uploads list + status
  GET  /admin/upload/statement    → bank statement upload form
  POST /admin/upload/statement    → submit bank statement
  GET  /admin/statement/<id>      → statement detail (transactions, parse errors)
  GET  /admin/reports             → audit reports
"""

import logging
import os
import uuid

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from auth import require_auth
from db import get_pool
from models import STATEMENT_TYPES
from roles import require_admin
from s3 import upload_file, upload_info_json, get_presigned_url

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ALLOWED_STATEMENT_EXTENSIONS = {"pdf", "csv", "xlsx", "xls", "zip"}
MAX_STATEMENT_SIZE = 50 * 1024 * 1024  # 50 MB


def _trigger_parser_task(file_id: str) -> None:
    """
    Trigger an ECS one-off task to parse the bank statement file.
    Uses the same ECS cluster/task definition as the web service,
    with a CMD override: python -m tasks.parser --file-id <id>
    """
    import boto3  # noqa: PLC0415

    cluster = os.environ.get("ECS_CLUSTER_NAME", "")
    task_def = os.environ.get("ECS_TASK_DEFINITION", "")
    subnets = os.environ.get("ECS_TASK_SUBNETS", "").split(",")
    security_groups = os.environ.get("ECS_TASK_SECURITY_GROUPS", "").split(",")
    container_name = os.environ.get("ECS_CONTAINER_NAME", "app")

    if not cluster or not task_def:
        logger.warning(
            "ECS_CLUSTER_NAME or ECS_TASK_DEFINITION not set — "
            "parser task not triggered. Trigger manually via bin/run-task.sh"
        )
        return

    ecs = boto3.client("ecs", region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2"))
    resp = ecs.run_task(
        cluster=cluster,
        taskDefinition=task_def,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": [s.strip() for s in subnets if s.strip()],
                "securityGroups": [sg.strip() for sg in security_groups if sg.strip()],
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": container_name,
                    "command": ["python", "-m", "tasks.parser", "--file-id", file_id],
                }
            ]
        },
    )
    tasks = resp.get("tasks", [])
    if tasks:
        logger.info("Started parser ECS task: %s", tasks[0].get("taskArn"))
    else:
        failures = resp.get("failures", [])
        logger.error("Failed to start parser ECS task: %s", failures)


@admin_bp.get("/")
@require_auth
@require_admin
def index():
    return redirect(url_for("admin.dashboard"))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@admin_bp.get("/dashboard")
@require_auth
@require_admin
def dashboard():
    files = []
    pool = get_pool()
    if pool:
        with pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, statement_type, status, comment, uploaded_by,
                       created_at, parsed_at, inserted_at, parse_error
                FROM bank_statement_files
                ORDER BY created_at DESC
                LIMIT 200
                """
            ).fetchall()
            files = [
                dict(zip(
                    ("id", "statement_type", "status", "comment", "uploaded_by",
                     "created_at", "parsed_at", "inserted_at", "parse_error"),
                    r,
                ))
                for r in rows
            ]
    return render_template("admin/dashboard.html", files=files)


# ── Upload Statement ──────────────────────────────────────────────────────────

@admin_bp.get("/upload/statement")
@require_auth
@require_admin
def upload_statement():
    return render_template(
        "admin/upload_statement.html",
        statement_types=STATEMENT_TYPES,
    )


@admin_bp.post("/upload/statement")
@require_auth
@require_admin
def upload_statement_post():
    errors = []
    statement_type = request.form.get("statement_type", "").strip()
    file_password = request.form.get("file_password", "").strip() or None
    comment = request.form.get("comment", "").strip() or None
    statement_file = request.files.get("statement_file")

    if statement_type not in STATEMENT_TYPES:
        errors.append(f"Statement type must be one of: {', '.join(STATEMENT_TYPES)}")
    if not statement_file or not statement_file.filename:
        errors.append("Statement file is required.")
    else:
        ext = statement_file.filename.rsplit(".", 1)[-1].lower() if "." in statement_file.filename else ""
        if ext not in ALLOWED_STATEMENT_EXTENSIONS:
            errors.append(f"File must be one of: {', '.join(ALLOWED_STATEMENT_EXTENSIONS)}")

    if errors:
        for e in errors:
            flash(e, "error")
        return render_template(
            "admin/upload_statement.html",
            statement_types=STATEMENT_TYPES,
            form=request.form,
        ), 422

    file_id = str(uuid.uuid4())
    file_bytes = statement_file.read()
    if len(file_bytes) > MAX_STATEMENT_SIZE:
        flash("File too large (max 50 MB).", "error")
        return redirect(url_for("admin.upload_statement"))

    ext = statement_file.filename.rsplit(".", 1)[-1].lower()
    from datetime import date, timezone, datetime
    today = date.today()

    try:
        file_key = upload_file(
            file_bytes,
            group_id="_admin",
            upload_type="bank_statement",
            payment_type=statement_type,
            for_date=today,
            file_id=file_id,
            extension=ext,
            content_type=statement_file.content_type or "application/octet-stream",
        )
        info = {
            "id": file_id,
            "statement_type": statement_type,
            "file_s3_key": file_key,
            "comment": comment,
            "uploaded_by": g.user_id,
        }
        info_key = upload_info_json(
            info,
            group_id="_admin",
            upload_type="bank_statement",
            payment_type=statement_type,
            for_date=today,
            file_id=file_id,
        )
    except Exception:
        logger.exception("S3 upload failed for statement %s", file_id)
        flash("Upload failed — please try again.", "error")
        return redirect(url_for("admin.upload_statement"))

    pool = get_pool()
    if pool:
        try:
            with pool.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO bank_statement_files (
                        id, statement_type, file_s3_key, info_s3_key,
                        file_password, comment, uploaded_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (file_id, statement_type, file_key, info_key,
                     file_password, comment, g.user_id),
                )
        except Exception:
            logger.exception("DB insert failed for statement file %s", file_id)
            flash("File saved but DB insert failed — contact admin.", "warning")
            return redirect(url_for("admin.dashboard"))

    # Trigger parser ECS task
    try:
        _trigger_parser_task(file_id)
        flash("Statement uploaded. Parser task triggered.", "success")
    except Exception:
        logger.exception("Failed to trigger parser task")
        flash("Statement uploaded. Trigger parsing manually from the dashboard.", "warning")

    return redirect(url_for("admin.dashboard"))


# ── Statement Detail ──────────────────────────────────────────────────────────

@admin_bp.get("/statement/<file_id>")
@require_auth
@require_admin
def statement_detail(file_id: str):
    file_record = None
    transactions = []
    presigned_url = None
    pool = get_pool()
    if pool:
        with pool.connection() as conn:
            row = conn.execute(
                """
                SELECT id, statement_type, file_s3_key, info_s3_key, comment,
                       status, uploaded_by, created_at, parsed_at, inserted_at, parse_error
                FROM bank_statement_files WHERE id = %s
                """,
                (file_id,),
            ).fetchone()
            if row:
                file_record = dict(zip(
                    ("id", "statement_type", "file_s3_key", "info_s3_key", "comment",
                     "status", "uploaded_by", "created_at", "parsed_at", "inserted_at", "parse_error"),
                    row,
                ))
                try:
                    presigned_url = get_presigned_url(file_record["file_s3_key"])
                except Exception:
                    pass

            if file_record:
                txn_rows = conn.execute(
                    """
                    SELECT id, transaction_id, transaction_timestamp, transaction_type,
                           amount, currency, description, balance, matching_submission_id
                    FROM bank_transactions
                    WHERE bank_statement_file_id = %s
                    ORDER BY transaction_timestamp
                    """,
                    (file_id,),
                ).fetchall()
                transactions = [
                    dict(zip(
                        ("id", "transaction_id", "transaction_timestamp", "transaction_type",
                         "amount", "currency", "description", "balance", "matching_submission_id"),
                        r,
                    ))
                    for r in txn_rows
                ]

    if not file_record:
        flash("Statement file not found.", "error")
        return redirect(url_for("admin.dashboard"))

    return render_template(
        "admin/statement_detail.html",
        file=file_record,
        transactions=transactions,
        presigned_url=presigned_url,
    )


# ── Reports ───────────────────────────────────────────────────────────────────

@admin_bp.get("/reports")
@require_auth
@require_admin
def reports():
    pool = get_pool()
    report_data = {}

    if pool:
        with pool.connection() as conn:
            # 1. Statement upload status summary
            rows = conn.execute(
                """
                SELECT status, COUNT(*) as count
                FROM bank_statement_files
                GROUP BY status ORDER BY status
                """
            ).fetchall()
            report_data["statement_status_summary"] = [
                {"status": r[0], "count": r[1]} for r in rows
            ]

            # 2. Matched transactions per branch
            rows = conn.execute(
                """
                SELECT group_id,
                       COUNT(*) AS matched_count,
                       SUM(amount) AS matched_amount
                FROM submissions
                WHERE status = 'MATCHED'
                GROUP BY group_id ORDER BY group_id
                """
            ).fetchall()
            report_data["matched_per_branch"] = [
                {"group_id": r[0], "count": r[1], "amount": r[2]} for r in rows
            ]

            # 3. Unmatched transactions per branch
            rows = conn.execute(
                """
                SELECT group_id,
                       COUNT(*) AS unmatched_count,
                       SUM(amount) AS unmatched_amount
                FROM submissions
                WHERE status = 'UNMATCHED'
                GROUP BY group_id ORDER BY group_id
                """
            ).fetchall()
            report_data["unmatched_per_branch"] = [
                {"group_id": r[0], "count": r[1], "amount": r[2]} for r in rows
            ]

            # 4. Parse failures
            rows = conn.execute(
                """
                SELECT id, statement_type, created_at, parse_error, file_s3_key
                FROM bank_statement_files
                WHERE status = 'FAILED'
                ORDER BY created_at DESC
                """
            ).fetchall()
            report_data["parse_failures"] = [
                {"id": r[0], "statement_type": r[1], "created_at": r[2],
                 "parse_error": r[3], "file_s3_key": r[4]}
                for r in rows
            ]

            # 5. Duplicate submission audit (same branch, date, amount, type)
            rows = conn.execute(
                """
                SELECT group_id, transaction_date, amount, payment_type,
                       COUNT(*) AS duplicate_count
                FROM submissions
                GROUP BY group_id, transaction_date, amount, payment_type
                HAVING COUNT(*) > 1
                ORDER BY duplicate_count DESC, transaction_date DESC
                LIMIT 50
                """
            ).fetchall()
            report_data["duplicates"] = [
                {"group_id": r[0], "date": r[1], "amount": r[2],
                 "payment_type": r[3], "count": r[4]}
                for r in rows
            ]

            # 6. Late submissions (submitted > 2 days after transaction date)
            rows = conn.execute(
                """
                SELECT id, group_id, transaction_date, created_at, amount, payment_type,
                       (created_at::date - transaction_date) AS days_late
                FROM submissions
                WHERE (created_at::date - transaction_date) > 2
                ORDER BY days_late DESC
                LIMIT 50
                """
            ).fetchall()
            report_data["late_submissions"] = [
                {"id": r[0], "group_id": r[1], "transaction_date": r[2],
                 "created_at": r[3], "amount": r[4], "payment_type": r[5], "days_late": r[6]}
                for r in rows
            ]

            # 7. Statement coverage: date range of transactions per statement
            rows = conn.execute(
                """
                SELECT bsf.id, bsf.statement_type, bsf.created_at,
                       MIN(bt.transaction_timestamp) AS earliest,
                       MAX(bt.transaction_timestamp) AS latest,
                       COUNT(bt.id) AS txn_count
                FROM bank_statement_files bsf
                LEFT JOIN bank_transactions bt ON bt.bank_statement_file_id = bsf.id
                WHERE bsf.status = 'INSERTED'
                GROUP BY bsf.id, bsf.statement_type, bsf.created_at
                ORDER BY bsf.created_at DESC
                LIMIT 50
                """
            ).fetchall()
            report_data["statement_coverage"] = [
                {"id": r[0], "statement_type": r[1], "uploaded_at": r[2],
                 "earliest_txn": r[3], "latest_txn": r[4], "txn_count": r[5]}
                for r in rows
            ]

    return render_template("admin/reports.html", data=report_data)
