import logging
import os
from datetime import datetime, timezone

from flask import Flask, g, jsonify

from auth import register_auth_routes, require_auth
from db import get_pool

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Flask signs session cookies with this key — must be set in production.
# Injected via SECRET_KEY env var in the ECS task definition.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Register /auth/* routes (login, callback, logout, me)
register_auth_routes(app)


# ---------------------------------------------------------------------------
# Infrastructure / health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """
    Health check — called by ECS Express / ALB every 15 s.

    Returns 200 as long as the process is alive. Database connectivity is
    checked separately (/health/db) so a DB blip never takes the service down.
    """
    return jsonify(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
        environment=os.getenv("ENVIRONMENT", "development"),
    )


@app.get("/health/db")
def health_db():
    """
    Deep health check — verifies the database is reachable.
    Returns 503 if the pool is not configured or the query fails.
    """
    pool = get_pool()
    if pool is None:
        return jsonify(status="unconfigured", detail="DB_SECRET_ARN not set"), 503

    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return jsonify(status="ok")
    except Exception as exc:
        logger.exception("DB health check failed")
        return jsonify(status="error", detail=str(exc)), 503


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return jsonify(message="webapp-accounts-reconciller API", version="1.0.0")


@app.get("/hello")
def hello():
    return jsonify(message="Hello, world!")


# ---------------------------------------------------------------------------
# Protected API routes — require a valid Cognito token
# ---------------------------------------------------------------------------

@app.get("/api/me")
@require_auth
def api_me():
    """Return the authenticated user's identity. Useful as a mobile 'whoami'."""
    return jsonify(user_id=g.user_id, email=g.email)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(_e):
    return jsonify(error="Not found"), 404


@app.errorhandler(500)
def internal_error(_e):
    return jsonify(error="Internal server error"), 500
