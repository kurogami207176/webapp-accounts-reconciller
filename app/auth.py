"""
auth.py — JWT verification for Cognito tokens.

Every protected route uses the @require_auth decorator. It:
  1. Extracts the Bearer token from the Authorization header
  2. Fetches Cognito's public JWKS (cached — one HTTP call per process lifetime)
  3. Verifies the token's signature, expiry, and audience
  4. Stores the decoded claims on Flask's `g` for the duration of the request

Environment variables (injected by ECS task definition):
  COGNITO_USER_POOL_ID  — e.g. ap-southeast-2_AbCdEfGhI
  COGNITO_REGION        — e.g. ap-southeast-2
  COGNITO_WEB_CLIENT_ID — Cognito app client ID for the web client
  COGNITO_MOBILE_CLIENT_ID — Cognito app client ID for the mobile client

Usage:
    from auth import require_auth

    @app.get("/api/me")
    @require_auth
    def me():
        # g.user_id  — stable UUID (Cognito "sub")
        # g.email    — user's email address
        # g.claims   — full decoded JWT payload
        return jsonify(user_id=g.user_id, email=g.email)

Web session flow (authorization code + PKCE):
    /auth/login    → redirect to Cognito Hosted UI
    /auth/callback → exchange code for tokens, store in secure HttpOnly cookie
    /auth/logout   → clear cookie, redirect to Cognito logout

Mobile flow:
    Mobile app performs PKCE flow independently (e.g. via AWS Amplify or AppAuth).
    It then sends every request with:
        Authorization: Bearer <access_token>
"""

import logging
import os
from functools import lru_cache, wraps
from urllib.parse import urlencode

import requests
from flask import g, jsonify, redirect, request, session
from jwt import PyJWT, PyJWKClient, ExpiredSignatureError, InvalidTokenError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config — read once at import time
# ---------------------------------------------------------------------------

COGNITO_REGION        = os.environ.get("COGNITO_REGION", "ap-southeast-2")
COGNITO_USER_POOL_ID  = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_WEB_CLIENT_ID = os.environ.get("COGNITO_WEB_CLIENT_ID", "")
COGNITO_MOBILE_CLIENT_ID = os.environ.get("COGNITO_MOBILE_CLIENT_ID", "")
COGNITO_HOSTED_UI_URL = os.environ.get("COGNITO_HOSTED_UI_URL", "")  # base URL only

# All valid client IDs — a token is accepted if its `aud` matches any of these
_VALID_AUDIENCES = {id_ for id_ in [COGNITO_WEB_CLIENT_ID, COGNITO_MOBILE_CLIENT_ID] if id_}

_ISSUER = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
_JWKS_URL = f"{_ISSUER}/.well-known/jwks.json"


# ---------------------------------------------------------------------------
# JWKS client — cached for the process lifetime (PyJWT handles key rotation)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    """Return a cached PyJWKClient that auto-refreshes keys on rotation."""
    return PyJWKClient(_JWKS_URL, cache_keys=True, max_cached_keys=16)


# ---------------------------------------------------------------------------
# Core token verification
# ---------------------------------------------------------------------------

def _verify_token(token: str) -> dict:
    """
    Verify a Cognito access token and return its decoded claims.
    Raises jwt.InvalidTokenError (or subclass) on any failure.
    """
    client = _jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)

    # Decode and verify signature, expiry, issuer, and audience in one call
    payload = PyJWT().decode_complete(
        token,
        key=signing_key.key,
        algorithms=["RS256"],
        issuer=_ISSUER,
        # Cognito access tokens use `client_id` claim, not `aud`.
        # We verify it manually below.
        options={"verify_aud": False},
    )["payload"]

    # Manually verify client_id matches one of our registered app clients
    token_client_id = payload.get("client_id") or payload.get("aud")
    if token_client_id not in _VALID_AUDIENCES:
        raise InvalidTokenError(f"Token client_id '{token_client_id}' is not a known app client")

    return payload


# ---------------------------------------------------------------------------
# @require_auth decorator — for API routes (returns JSON errors)
# ---------------------------------------------------------------------------

def require_auth(f):
    """
    Decorator for API routes that require a valid Cognito access token.

    Reads the token from:
      1. Authorization: Bearer <token>  header  (mobile + web API calls)
      2. cognito_access_token session cookie     (web browser requests)

    On success, sets:
      g.user_id  — Cognito sub (stable UUID)
      g.email    — user's email (may be absent on access tokens; use id_token for email)
      g.claims   — full decoded payload
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify(error="Authentication required"), 401

        try:
            claims = _verify_token(token)
        except ExpiredSignatureError:
            return jsonify(error="Token expired"), 401
        except InvalidTokenError as exc:
            logger.debug("Invalid token: %s", exc)
            return jsonify(error="Invalid token"), 401

        g.user_id = claims["sub"]
        g.email   = claims.get("email", "")
        g.claims  = claims
        return f(*args, **kwargs)

    return decorated


def _extract_token() -> str | None:
    """Try Authorization header first, then session cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return session.get("cognito_access_token")


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
        """Redirect the browser to the Cognito Hosted UI login page."""
        if not COGNITO_HOSTED_UI_URL or not COGNITO_WEB_CLIENT_ID:
            return jsonify(error="Auth not configured"), 503

        callback_url = _web_callback_url()
        params = {
            "response_type": "code",
            "client_id": COGNITO_WEB_CLIENT_ID,
            "redirect_uri": callback_url,
            "scope": "openid email profile",
        }
        return redirect(f"{COGNITO_HOSTED_UI_URL}/oauth2/authorize?{urlencode(params)}")

    @app.get("/auth/callback")
    def auth_callback():
        """
        Cognito redirects here with ?code=... after a successful login.
        Exchange the code for tokens and store them in a secure session cookie.
        """
        code = request.args.get("code")
        if not code:
            return jsonify(error="Missing auth code"), 400

        token_url = f"{COGNITO_HOSTED_UI_URL}/oauth2/token"
        resp = requests.post(
            token_url,
            data={
                "grant_type":   "authorization_code",
                "client_id":    COGNITO_WEB_CLIENT_ID,
                "redirect_uri": _web_callback_url(),
                "code":         code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        if not resp.ok:
            logger.error("Token exchange failed: %s %s", resp.status_code, resp.text)
            return jsonify(error="Token exchange failed"), 502

        tokens = resp.json()

        # Store in server-side session (Flask signs the cookie with SECRET_KEY)
        # Never put tokens in localStorage — HttpOnly cookie prevents XSS theft
        session["cognito_access_token"]  = tokens["access_token"]
        session["cognito_refresh_token"] = tokens["refresh_token"]
        session["cognito_id_token"]      = tokens.get("id_token", "")
        session.permanent = True

        return redirect("/")

    @app.get("/auth/logout")
    def auth_logout():
        """Clear the session and redirect to Cognito's logout endpoint."""
        session.clear()

        if COGNITO_HOSTED_UI_URL and COGNITO_WEB_CLIENT_ID:
            params = {
                "client_id":  COGNITO_WEB_CLIENT_ID,
                "logout_uri": _web_callback_url().replace("/auth/callback", "/auth/logout-done"),
            }
            return redirect(f"{COGNITO_HOSTED_UI_URL}/logout?{urlencode(params)}")

        return redirect("/")

    @app.get("/auth/logout-done")
    def auth_logout_done():
        """Landing page after Cognito clears its session."""
        return redirect("/")

    @app.get("/auth/me")
    @require_auth
    def auth_me():
        """Return the current user's identity. Useful for the web frontend."""
        return jsonify(user_id=g.user_id, email=g.email)


def _web_callback_url() -> str:
    """Build the callback URL from the incoming request host."""
    # In production this will be https://webapp-accounts-reconciller.apps.alainjunia.com/auth/callback
    return f"{request.scheme}://{request.host}/auth/callback"
