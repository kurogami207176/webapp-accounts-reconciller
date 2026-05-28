"""
roles.py — Firebase Custom Claim-based role helpers.

Firebase custom claims convention (set by auth.py on first login):
  role: "admin"   → full admin access
  role: "branch"  → branch user; group_id holds the branch identifier
                    e.g. role="branch", group_id="branch-manila"

Custom claims are embedded in the Firebase ID token payload and are
available on g.claims after @require_auth runs.

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

ADMIN_ROLE = "admin"
BRANCH_ROLE = "branch"
BRANCH_GROUP_PREFIX = "branch-"


def is_admin(claims: dict) -> bool:
    """Return True if the user has the admin role."""
    return claims.get("role") == ADMIN_ROLE


def is_branch_user(claims: dict) -> bool:
    """Return True if the user has the branch role."""
    return claims.get("role") == BRANCH_ROLE


def get_branch_group_id(claims: dict) -> str | None:
    """
    Return the branch group_id if the user is a branch user, else None.
    e.g. "branch-manila"
    """
    if claims.get("role") == BRANCH_ROLE:
        return claims.get("group_id") or None
    return None


def get_user_groups(claims: dict) -> list[str]:
    """
    Return a list of group identifiers for the user.
    Admins get ["admins"]; branch users get their group_id (if set).
    Provided for compatibility with any code that iterates groups.
    """
    role = claims.get("role", "")
    if role == ADMIN_ROLE:
        return ["admins"]
    if role == BRANCH_ROLE:
        gid = claims.get("group_id", "")
        return [gid] if gid else []
    return []


# ---------------------------------------------------------------------------
# Decorators — use after @require_auth
# ---------------------------------------------------------------------------

def require_admin(f):
    """
    Decorator that requires the authenticated user to have the admin role.
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
    Decorator that requires the authenticated user to have the branch role.
    Must be applied AFTER @require_auth (which sets g.claims).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_branch_user(g.claims):
            return jsonify(error="Branch user access required"), 403
        return f(*args, **kwargs)
    return decorated
