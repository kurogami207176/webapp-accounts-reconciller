"""
blueprints/branch.py — Branch user routes.

Routes:
  GET  /branch/           → redirect to upload selection
  GET  /branch/upload/online   → online transaction upload form
  POST /branch/upload/online   → submit online transaction
  GET  /branch/upload/deposit  → bank deposit upload form
  POST /branch/upload/deposit  → submit bank deposit
  GET  /branch/history         → submission history for this branch
"""

import logging
import os
import uuid
from datetime import date, datetime, time

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from auth import require_auth
from db import get_pool
from models import ONLINE_PAYMENT_TYPES, BANK_DEPOSIT_TYPES
from roles import get_branch_group_id, require_branch
from s3 import upload_file, upload_info_json

logger = logging.getLogger(__name__)

branch_bp = Blueprint("branch", __name__, url_prefix="/branch")

ALLOWED_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "heic", "pdf"}
MAX_PHOTO_SIZE = 10 * 1024 * 1024  # 10 MB


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXTENSIONS


@branch_bp.get("/")
@require_auth
@require_branch
def index():
    return redirect(url_for("branch.upload_online"))


# ── Online Transaction ───────────────────────────────────────────────────────

@branch_bp.get("/upload/online")
@require_auth
@require_branch
def upload_online():
    return render_template(
        "branch/upload_online.html",
        payment_types=ONLINE_PAYMENT_TYPES,
    )


@branch_bp.post("/upload/online")
@require_auth
@require_branch
def upload_online_post():
    group_id = get_branch_group_id(g.claims)
    if not group_id:
        flash("Your account is not assigned to a branch group.", "error")
        return redirect(url_for("branch.upload_online"))

    # ── Validate form fields ──────────────────────────────────────────────
    errors = []
    txn_date_str = request.form.get("transaction_date", "").strip()
    txn_time_str = request.form.get("transaction_time", "").strip()
    amount_str = request.form.get("amount", "").strip()
    staff_name = request.form.get("staff_name", "").strip()
    payment_type = request.form.get("payment_type", "").strip()
    photo = request.files.get("photo")

    if not txn_date_str:
        errors.append("Transaction date is required.")
    if not txn_time_str:
        errors.append("Transaction time is required.")
    if not amount_str:
        errors.append("Amount is required.")
    if not staff_name:
        errors.append("Staff name is required.")
    if payment_type not in ONLINE_PAYMENT_TYPES:
        errors.append(f"Payment type must be one of: {', '.join(ONLINE_PAYMENT_TYPES)}")
    if not photo or not photo.filename:
        errors.append("Photo/receipt is required.")
    elif not _allowed_file(photo.filename):
        errors.append("Photo must be JPG, PNG, HEIC, or PDF.")

    try:
        txn_date = date.fromisoformat(txn_date_str)
    except ValueError:
        errors.append("Invalid date format.")
        txn_date = date.today()

    try:
        txn_time = time.fromisoformat(txn_time_str)
    except ValueError:
        errors.append("Invalid time format.")
        txn_time = time(0, 0)

    try:
        from decimal import Decimal
        amount = Decimal(amount_str)
        if amount <= 0:
            errors.append("Amount must be greater than zero.")
    except Exception:
        errors.append("Amount must be a valid number.")
        amount = Decimal("0")

    if errors:
        for e in errors:
            flash(e, "error")
        return render_template(
            "branch/upload_online.html",
            payment_types=ONLINE_PAYMENT_TYPES,
            form=request.form,
        ), 422

    # ── Upload to S3 ──────────────────────────────────────────────────────
    file_id = str(uuid.uuid4())
    photo_bytes = photo.read()
    if len(photo_bytes) > MAX_PHOTO_SIZE:
        flash("Photo file is too large (max 10 MB).", "error")
        return redirect(url_for("branch.upload_online"))

    ext = photo.filename.rsplit(".", 1)[1].lower()

    try:
        photo_key = upload_file(
            photo_bytes,
            group_id=group_id,
            upload_type="online_transaction",
            payment_type=payment_type,
            for_date=txn_date,
            file_id=file_id,
            extension=ext,
            content_type=photo.content_type or "image/jpeg",
        )

        info = {
            "id": file_id,
            "group_id": group_id,
            "upload_type": "online_transaction",
            "payment_type": payment_type,
            "transaction_date": txn_date.isoformat(),
            "transaction_time": txn_time.isoformat(),
            "amount": str(amount),
            "staff_name": staff_name,
            "photo_s3_key": photo_key,
            "submitted_by": g.user_id,
        }
        info_key = upload_info_json(
            info,
            group_id=group_id,
            upload_type="online_transaction",
            payment_type=payment_type,
            for_date=txn_date,
            file_id=file_id,
        )
    except Exception as exc:
        logger.exception("S3 upload failed")
        flash("Upload failed — please try again.", "error")
        return redirect(url_for("branch.upload_online"))

    # ── Insert into DB ────────────────────────────────────────────────────
    pool = get_pool()
    if pool:
        try:
            with pool.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO submissions (
                        id, group_id, upload_type, payment_type,
                        transaction_date, transaction_time, amount, staff_name,
                        photo_s3_key, info_s3_key, submitted_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        file_id, group_id, "online_transaction", payment_type,
                        txn_date, txn_time, amount, staff_name,
                        photo_key, info_key, g.user_id,
                    ),
                )
        except Exception:
            logger.exception("DB insert failed for submission %s", file_id)
            flash("Submission saved to S3 but database insert failed — contact admin.", "warning")

    flash("Receipt uploaded successfully!", "success")
    return redirect(url_for("branch.history"))


# ── Bank Deposit ─────────────────────────────────────────────────────────────

@branch_bp.get("/upload/deposit")
@require_auth
@require_branch
def upload_deposit():
    return render_template(
        "branch/upload_deposit.html",
        bank_types=BANK_DEPOSIT_TYPES,
    )


@branch_bp.post("/upload/deposit")
@require_auth
@require_branch
def upload_deposit_post():
    group_id = get_branch_group_id(g.claims)
    if not group_id:
        flash("Your account is not assigned to a branch group.", "error")
        return redirect(url_for("branch.upload_deposit"))

    errors = []
    txn_date_str = request.form.get("transaction_date", "").strip()
    txn_time_str = request.form.get("transaction_time", "").strip()
    amount_str = request.form.get("amount", "").strip()
    staff_name = request.form.get("staff_name", "").strip()
    bank_type = request.form.get("bank_type", "").strip()
    reference_number = request.form.get("reference_number", "").strip() or None
    for_day_str = request.form.get("for_day", "").strip()
    photo = request.files.get("photo")

    if not txn_date_str:
        errors.append("Transaction date is required.")
    if not txn_time_str:
        errors.append("Transaction time is required.")
    if not amount_str:
        errors.append("Amount is required.")
    if not staff_name:
        errors.append("Staff name is required.")
    if bank_type not in BANK_DEPOSIT_TYPES:
        errors.append(f"Bank type must be one of: {', '.join(BANK_DEPOSIT_TYPES)}")
    if not photo or not photo.filename:
        errors.append("Deposit slip photo is required.")
    elif not _allowed_file(photo.filename):
        errors.append("Photo must be JPG, PNG, HEIC, or PDF.")
    if not for_day_str:
        errors.append("Clearing date (for day) is required.")

    try:
        txn_date = date.fromisoformat(txn_date_str)
    except ValueError:
        errors.append("Invalid date format.")
        txn_date = date.today()

    try:
        txn_time = time.fromisoformat(txn_time_str)
    except ValueError:
        errors.append("Invalid time format.")
        txn_time = time(0, 0)

    try:
        from decimal import Decimal
        amount = Decimal(amount_str)
        if amount <= 0:
            errors.append("Amount must be greater than zero.")
    except Exception:
        errors.append("Amount must be a valid number.")
        amount = Decimal("0")

    try:
        for_day = date.fromisoformat(for_day_str) if for_day_str else None
    except ValueError:
        errors.append("Invalid clearing date format.")
        for_day = None

    if errors:
        for e in errors:
            flash(e, "error")
        return render_template(
            "branch/upload_deposit.html",
            bank_types=BANK_DEPOSIT_TYPES,
            form=request.form,
        ), 422

    file_id = str(uuid.uuid4())
    photo_bytes = photo.read()
    if len(photo_bytes) > MAX_PHOTO_SIZE:
        flash("Photo file is too large (max 10 MB).", "error")
        return redirect(url_for("branch.upload_deposit"))

    ext = photo.filename.rsplit(".", 1)[1].lower()

    try:
        photo_key = upload_file(
            photo_bytes,
            group_id=group_id,
            upload_type="bank_deposit",
            payment_type=bank_type,
            for_date=txn_date,
            file_id=file_id,
            extension=ext,
            content_type=photo.content_type or "image/jpeg",
        )

        info = {
            "id": file_id,
            "group_id": group_id,
            "upload_type": "bank_deposit",
            "payment_type": bank_type,
            "transaction_date": txn_date.isoformat(),
            "transaction_time": txn_time.isoformat(),
            "amount": str(amount),
            "staff_name": staff_name,
            "reference_number": reference_number,
            "for_day": for_day.isoformat() if for_day else None,
            "photo_s3_key": photo_key,
            "submitted_by": g.user_id,
        }
        info_key = upload_info_json(
            info,
            group_id=group_id,
            upload_type="bank_deposit",
            payment_type=bank_type,
            for_date=txn_date,
            file_id=file_id,
        )
    except Exception:
        logger.exception("S3 upload failed")
        flash("Upload failed — please try again.", "error")
        return redirect(url_for("branch.upload_deposit"))

    pool = get_pool()
    if pool:
        try:
            with pool.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO submissions (
                        id, group_id, upload_type, payment_type,
                        transaction_date, transaction_time, amount, staff_name,
                        reference_number, for_day,
                        photo_s3_key, info_s3_key, submitted_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        file_id, group_id, "bank_deposit", bank_type,
                        txn_date, txn_time, amount, staff_name,
                        reference_number, for_day,
                        photo_key, info_key, g.user_id,
                    ),
                )
        except Exception:
            logger.exception("DB insert failed for submission %s", file_id)
            flash("Submission saved to S3 but database insert failed — contact admin.", "warning")

    flash("Bank deposit slip uploaded successfully!", "success")
    return redirect(url_for("branch.history"))


# ── History ───────────────────────────────────────────────────────────────────

@branch_bp.get("/history")
@require_auth
@require_branch
def history():
    group_id = get_branch_group_id(g.claims)
    submissions = []
    pool = get_pool()
    if pool and group_id:
        with pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT id, upload_type, payment_type, transaction_date,
                       transaction_time, amount, staff_name, status, created_at
                FROM submissions
                WHERE group_id = %s
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (group_id,),
            ).fetchall()
            submissions = [
                dict(zip(
                    ("id", "upload_type", "payment_type", "transaction_date",
                     "transaction_time", "amount", "staff_name", "status", "created_at"),
                    r,
                ))
                for r in rows
            ]
    return render_template("branch/history.html", submissions=submissions, group_id=group_id)
