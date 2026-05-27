"""
blueprints/api.py — JSON API endpoints for task management and status polling.

Routes:
  POST /api/admin/statement/<id>/parse    → trigger parser ECS task
  POST /api/admin/statement/<id>/match    → trigger matcher ECS task
  GET  /api/admin/statement/<id>/status   → poll status (for UI polling)
  GET  /api/admin/submissions             → list submissions (with filters)
"""

import logging
import os

from flask import Blueprint, g, jsonify, request

from auth import require_auth
from db import get_pool
from roles import is_admin, require_admin

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _trigger_ecs_task(file_id: str, task_module: str) -> dict:
    """
    Trigger an ECS Fargate one-off task for parser or matcher.
    Returns {"task_arn": "..."} or raises on failure.
    """
    import boto3  # noqa: PLC0415

    cluster = os.environ.get("ECS_CLUSTER_NAME", "")
    task_def = os.environ.get("ECS_TASK_DEFINITION", "")
    subnets_raw = os.environ.get("ECS_TASK_SUBNETS", "")
    sgs_raw = os.environ.get("ECS_TASK_SECURITY_GROUPS", "")
    container_name = os.environ.get("ECS_CONTAINER_NAME", "app")

    if not cluster or not task_def:
        raise RuntimeError(
            "ECS_CLUSTER_NAME and ECS_TASK_DEFINITION env vars are required. "
            "Use bin/run-task.sh to trigger locally."
        )

    subnets = [s.strip() for s in subnets_raw.split(",") if s.strip()]
    sgs = [s.strip() for s in sgs_raw.split(",") if s.strip()]

    ecs = boto3.client("ecs", region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2"))
    resp = ecs.run_task(
        cluster=cluster,
        taskDefinition=task_def,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnets,
                "securityGroups": sgs,
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": container_name,
                    "command": ["python", "-m", task_module, "--file-id", file_id],
                }
            ]
        },
    )
    tasks = resp.get("tasks", [])
    failures = resp.get("failures", [])
    if not tasks:
        raise RuntimeError(f"ECS RunTask returned no tasks. Failures: {failures}")
    return {"task_arn": tasks[0].get("taskArn")}


# ── Statement status poll ─────────────────────────────────────────────────────

@api_bp.get("/admin/statement/<file_id>/status")
@require_auth
@require_admin
def statement_status(file_id: str):
    pool = get_pool()
    if not pool:
        return jsonify(error="Database not configured"), 503

    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, status, parse_error, parsed_at, inserted_at,
                   (SELECT COUNT(*) FROM bank_transactions WHERE bank_statement_file_id = %s) AS txn_count
            FROM bank_statement_files WHERE id = %s
            """,
            (file_id, file_id),
        ).fetchone()

    if not row:
        return jsonify(error="Not found"), 404

    return jsonify(
        id=row[0],
        status=row[1],
        parse_error=row[2],
        parsed_at=row[3].isoformat() if row[3] else None,
        inserted_at=row[4].isoformat() if row[4] else None,
        transaction_count=row[5],
    )


# ── Trigger parser ────────────────────────────────────────────────────────────

@api_bp.post("/admin/statement/<file_id>/parse")
@require_auth
@require_admin
def trigger_parse(file_id: str):
    pool = get_pool()
    if not pool:
        return jsonify(error="Database not configured"), 503

    with pool.connection() as conn:
        row = conn.execute(
            "SELECT status FROM bank_statement_files WHERE id = %s",
            (file_id,),
        ).fetchone()

    if not row:
        return jsonify(error="Not found"), 404
    if row[0] not in ("PENDING", "FAILED"):
        return jsonify(error=f"Cannot parse — current status is '{row[0]}'"), 409

    try:
        result = _trigger_ecs_task(file_id, "tasks.parser")
        return jsonify(message="Parser task triggered", **result)
    except RuntimeError as exc:
        logger.warning("Could not trigger ECS task: %s", exc)
        # Fall back: run synchronously in a thread (local dev / degraded mode)
        _run_task_in_thread(file_id, "tasks.parser")
        return jsonify(message="Parser running in-process (ECS not configured)")
    except Exception as exc:
        logger.exception("Failed to trigger parser")
        return jsonify(error=str(exc)), 500


# ── Trigger matcher ───────────────────────────────────────────────────────────

@api_bp.post("/admin/statement/<file_id>/match")
@require_auth
@require_admin
def trigger_match(file_id: str):
    pool = get_pool()
    if not pool:
        return jsonify(error="Database not configured"), 503

    with pool.connection() as conn:
        row = conn.execute(
            "SELECT status FROM bank_statement_files WHERE id = %s",
            (file_id,),
        ).fetchone()

    if not row:
        return jsonify(error="Not found"), 404
    if row[0] != "INSERTED":
        return jsonify(error=f"Cannot match — current status is '{row[0]}' (must be INSERTED)"), 409

    try:
        result = _trigger_ecs_task(file_id, "tasks.matcher")
        return jsonify(message="Matcher task triggered", **result)
    except RuntimeError as exc:
        logger.warning("Could not trigger ECS task: %s", exc)
        _run_task_in_thread(file_id, "tasks.matcher")
        return jsonify(message="Matcher running in-process (ECS not configured)")
    except Exception as exc:
        logger.exception("Failed to trigger matcher")
        return jsonify(error=str(exc)), 500


# ── Admin: list submissions ───────────────────────────────────────────────────

@api_bp.get("/admin/submissions")
@require_auth
@require_admin
def list_submissions():
    pool = get_pool()
    if not pool:
        return jsonify(error="Database not configured"), 503

    group_id = request.args.get("group_id")
    status = request.args.get("status")
    limit = min(int(request.args.get("limit", 100)), 500)

    filters = []
    params: list = []
    if group_id:
        filters.append("group_id = %s")
        params.append(group_id)
    if status:
        filters.append("status = %s")
        params.append(status)

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)

    with pool.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, group_id, upload_type, payment_type, transaction_date,
                   amount, staff_name, status, created_at
            FROM submissions
            {where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        ).fetchall()

    return jsonify(submissions=[
        dict(zip(
            ("id", "group_id", "upload_type", "payment_type", "transaction_date",
             "amount", "staff_name", "status", "created_at"),
            r,
        ))
        for r in rows
    ])


# ── Local fallback: run task in background thread ─────────────────────────────

def _run_task_in_thread(file_id: str, module: str) -> None:
    """
    Run a task module in a background thread (local dev / no-ECS fallback).
    NOT suitable for production — tasks may be killed if the process restarts.
    """
    import threading  # noqa: PLC0415
    import importlib  # noqa: PLC0415

    def _run():
        try:
            mod = importlib.import_module(module)
            mod.run(file_id)
        except Exception:
            logger.exception("Background task failed: %s (file_id=%s)", module, file_id)

    thread = threading.Thread(target=_run, daemon=True, name=f"{module}-{file_id[:8]}")
    thread.start()
    logger.info("Started background thread for %s (file_id=%s)", module, file_id)
