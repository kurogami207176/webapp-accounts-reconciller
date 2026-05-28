"""
auth.py — Firebase ID token verification.

Every protected route uses the @require_auth decorator. It:
  1. Extracts the Bearer token from the Authorization header (or session cookie)
  2. Verifies the token with the Firebase Admin SDK (cached — one init per process)
  3. Stores the decoded claims on Flask's `g` for the duration of the request

Environment variables:
  FIREBASE_PROJECT_ID           — e.g. "gcashmatcher"
  FIREBASE_SERVICE_ACCOUNT_KEY  — full JSON string of the service account key
                                  (injected via Secrets Manager in production)

Usage:
    from auth import require_auth

    @app.get("/api/me")
    @require_auth
    def me():
        # g.user_id  — Firebase uid (stable)
        # g.email    — user's email address
        # g.claims   — full decoded token payload (includes custom claims)
        return jsonify(user_id=g.user_id, email=g.email)

Web session flow:
    /auth/login    → serve Firebase login page (email/password + Google sign-in)
    /auth/callback → receive ID token from client JS, verify + store in session
    /auth/logout   → clear session, redirect to /

Mobile flow:
    Mobile app performs Firebase auth independently and sends every request with:
        Authorization: Bearer <id_token>
"""

import json
import logging
import os
from functools import wraps

from flask import g, jsonify, redirect, render_template, request, session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — read once at import time
# ---------------------------------------------------------------------------

FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
_FIREBASE_SERVICE_ACCOUNT_KEY_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_KEY", "")

# Local dev auth bypass — set DEV_BYPASS_AUTH=admin or DEV_BYPASS_AUTH=branch-<name>
# NEVER enable in production (checked below — inactive when ENVIRONMENT != development)
_DEV_BYPASS_AUTH = (
    os.environ.get("DEV_BYPASS_AUTH", "")
    if os.environ.get("ENVIRONMENT", "development") == "development"
    else ""
)

# Admin email list — these users get `role: admin` custom claim set on first login
ADMIN_EMAILS: set[str] = {
    e.strip().lower()
    for e in os.environ.get("GASHFLOW_ADMIN_EMAILS", "").split(",")
    if e.strip()
}


# ---------------------------------------------------------------------------
# Firebase Admin SDK — initialised once per process
# ---------------------------------------------------------------------------

_firebase_app = None


def _get_firebase_app():
    """Return the cached Firebase Admin app, initialising it on first call."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError:
        raise RuntimeError(
            "firebase-admin is not installed. Add it to requirements.txt and rebuild."
        )

    if _FIREBASE_SERVICE_ACCOUNT_KEY_JSON:
        try:
            key_dict = json.loads(_FIREBASE_SERVICE_ACCOUNT_KEY_JSON)
            cred = credentials.Certificate(key_dict)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"FIREBASE_SERVICE_ACCOUNT_KEY is not valid JSON: {exc}") from exc
    elif FIREBASE_PROJECT_ID:
        # Application Default Credentials (useful in GCP / local with gcloud auth)
        cred = credentials.ApplicationDefault()
    else:
        raise RuntimeError(
            "Neither FIREBASE_SERVICE_ACCOUNT_KEY nor FIREBASE_PROJECT_ID is set."
        )

    _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


# ---------------------------------------------------------------------------
# Core token verification
# ---------------------------------------------------------------------------

def _verify_token(token: str) -> dict:
    """
    Verify a Firebase ID token and return its decoded claims.
    Raises ValueError / firebase_admin.auth.* on any failure.
    """
    _get_firebase_app()
    from firebase_admin import auth as firebase_auth
    return firebase_auth.verify_id_token(token, check_revoked=False)


# ---------------------------------------------------------------------------
# @require_auth decorator — for API routes (returns JSON errors)
# ---------------------------------------------------------------------------

def require_auth(f):
    """
    Decorator for routes that require a valid Firebase ID token.

    Reads the token from:
      1. Authorization: Bearer <token>  header  (mobile + web API calls)
      2. firebase_id_token session key           (web browser requests)

    On success, sets:
      g.user_id  — Firebase uid (stable)
      g.email    — user's email
      g.claims   — full decoded payload (includes custom claims: role, group_id)

    On failure:
      - Browser requests (Accept: text/html) → redirect to /auth/login
      - API requests → 401 JSON
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # ── Local dev bypass (never active in production) ──────────────────
        if _DEV_BYPASS_AUTH:
            role = "admin" if _DEV_BYPASS_AUTH == "admin" else "branch"
            group_id = _DEV_BYPASS_AUTH if role == "branch" else ""
            g.user_id = "dev-bypass-user"
            g.email   = "dev@localhost"
            g.claims  = {
                "uid":      "dev-bypass-user",
                "email":    "dev@localhost",
                "role":     role,
                "group_id": group_id,
            }
            return f(*args, **kwargs)

        token = _extract_token()
        if not token:
            if _wants_html():
                return redirect("/auth/login")
            return jsonify(error="Authentication required"), 401

        try:
            claims = _verify_token(token)
        except Exception as exc:
            logger.debug("Token verification failed: %s", exc)
            if _wants_html():
                session.clear()
                return redirect("/auth/login")
            return jsonify(error="Invalid or expired token"), 401

        g.user_id = claims.get("uid") or claims.get("user_id", "")
        g.email   = claims.get("email", "")
        g.claims  = claims
        return f(*args, **kwargs)

    return decorated


def _wants_html() -> bool:
    """Return True if the client prefers HTML (browser navigation)."""
    accept = request.headers.get("Accept", "")
    if not accept or accept == "*/*":
        return False
    best = request.accept_mimetypes.best_match(["text/html", "application/json"])
    return best == "text/html"


def _extract_token() -> str | None:
    """Try Authorization header first, then session cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return session.get("firebase_id_token")


# ---------------------------------------------------------------------------
# Web auth routes — register these on the Flask app via register_auth_routes()
# ---------------------------------------------------------------------------

def register_auth_routes(app):
    """
    Register /auth/* routes on the Flask app.

    Call this once in app.py:
        from auth import register_auth_routes
        register_auth_routes(app)
    """

    @app.get("/auth/login")
    def auth_login():
        """Serve the Firebase login page."""
        return render_template(
            "auth/login.html",
            firebase_api_key=os.environ.get("FIREBASE_API_KEY", ""),
            firebase_app_id=os.environ.get("FIREBASE_APP_ID", ""),
            firebase_project_id=FIREBASE_PROJECT_ID,
        )

    @app.post("/auth/callback")
    def auth_callback():
        """
        Receive a Firebase ID token from the client-side JS login flow.
        Verify it, optionally set role custom claims on first login, then
        store in the server-side session.
        """
        data = request.get_json(silent=True) or {}
        token = data.get("idToken") or request.form.get("idToken", "")
        if not token:
            return jsonify(error="Missing idToken"), 400

        try:
            claims = _verify_token(token)
        except Exception as exc:
            logger.warning("auth/callback token verification failed: %s", exc)
            return jsonify(error="Invalid token"), 401

        # Ensure custom claims are set (role + group_id) — first-login seeding
        _ensure_custom_claims(claims)

        session["firebase_id_token"] = token
        session.permanent = True
        return jsonify(ok=True)

    @app.get("/auth/logout")
    def auth_logout():
        """Clear the session and redirect to the login page."""
        session.clear()
        return redirect("/auth/login")

    @app.get("/auth/me")
    @require_auth
    def auth_me():
        """Return the current user's identity. Useful for the web frontend."""
        return jsonify(user_id=g.user_id, email=g.email, role=g.claims.get("role", ""))


def _ensure_custom_claims(claims: dict) -> None:
    """
    Set Firebase custom claims (role, group_id) if they are not already present.

    Admin seeding: any email in ADMIN_EMAILS → role=admin.
    All others default to role=branch, group_id="" (admin must assign group later).
    This runs once per login; Firebase caches claims in the token for 1 hour.
    """
    if claims.get("role"):
        return  # already set — nothing to do

    email = (claims.get("email") or "").lower()
    uid   = claims.get("uid") or claims.get("user_id", "")
    if not uid:
        return

    if email in ADMIN_EMAILS:
        new_claims = {"role": "admin", "group_id": ""}
    else:
        new_claims = {"role": "branch", "group_id": ""}

    try:
        from firebase_admin import auth as firebase_auth
        firebase_auth.set_custom_user_claims(uid, new_claims)
        logger.info("Set custom claims for %s: %s", email, new_claims)
    except Exception as exc:
        logger.warning("Could not set custom claims for %s: %s", uid, exc)
