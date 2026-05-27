"""
roles.py — Cognito Group-based role helpers.

Cognito groups convention:
  - "admins"          → admin role (full access)
  - "branch-<name>"   → branch role; group name doubles as group_id
    e.g. "branch-manila", "branch-cebu"

The groups list is embedded in the JWT access token under the
"cognito:groups" claim by Cognito when group membership is configured.

Usage:
    from roles import is_admin, require_admin, require_branch, get_branch_group_id

    @app.get("/admin/dashboard")
    @require_auth
    @require_admin
    def dashboard():
        ...

    @app.post("/branch/upload")
    @require_auth
    @require_branch
    def upload():
        group_id = get_branch_group_id(g.claims)  # e.g. "branch-manila"
        ...
"""

import logging
from functools import wraps

from flask import g, jsonify

logger = logging.getLogger(__name__)

ADMIN_GROUP = "admins"
BRANCH_GROUP_PREFIX = "branch-"


def get_user_groups(claims: dict) -> list[str]:
    """Extract Cognito group memberships from JWT claims."""
    return claims.get("cognito:groups") or []


def is_admin(claims: dict) -> bool:
    """Return True if the user belongs to the admins group."""
    return ADMIN_GROUP in get_user_groups(claims)


def is_branch_user(claims: dict) -> bool:
    """Return True if the user belongs to at least one branch group."""
    return any(g.startswith(BRANCH_GROUP_PREFIX) for g in get_user_groups(claims))


def get_branch_group_id(claims: dict) -> str | None:
    """
    Return the first branch group name the user belongs to, or None.
    The group name IS the group_id (e.g. "branch-manila").
    """
    for group in get_user_groups(claims):
        if group.startswith(BRANCH_GROUP_PREFIX):
            return group
    return None


# ---------------------------------------------------------------------------
# Decorators — use after @require_auth
# ---------------------------------------------------------------------------

def require_admin(f):
    """
    Decorator that requires the authenticated user to be in the 'admins' group.
    Must be applied AFTER @require_auth (which sets g.claims).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin(g.claims):
            return jsonify(error="Admin access required"), 403
        return f(*args, **kwargs)
    return decorated


def require_branch(f):
    """
    Decorator that requires the authenticated user to be in a branch group.
    Must be applied AFTER @require_auth (which sets g.claims).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_branch_user(g.claims):
            return jsonify(error="Branch user access required"), 403
        return f(*args, **kwargs)
    return decorated
