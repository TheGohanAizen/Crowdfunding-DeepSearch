"""Offline authenticated opportunity operations.

Not registered as HTTP routes. Only accepts a cryptographically verified
session and requires a session-bound CSRF token for mutations. The caller must
provide the secret from protected server configuration and authenticate the
user before issuing the session. No browser-provided owner ID is trusted.
"""
from opportunity_sessions import verify_csrf
from opportunity_session_registry import require_active_session
from opportunity_store import (
    add, list_for_owner, update_for_owner, delete_for_owner,
)


def _owner(connection, session_token, secret, now=None):
    return require_active_session(connection, session_token, secret, now=now)


def list_opportunities(connection, session_token, secret, limit=100, now=None):
    owner_id = _owner(connection, session_token, secret, now=now)
    return list_for_owner(connection, owner_id, limit=limit)


def create_opportunity(connection, session_token, csrf_token, secret, item, now=None):
    owner_id = _owner(connection, session_token, secret, now=now)
    if not verify_csrf(session_token, csrf_token, secret):
        raise PermissionError("invalid CSRF token")
    return add(connection, owner_id, item)


def update_opportunity(connection, session_token, csrf_token, secret, record_id, changes, now=None):
    owner_id = _owner(connection, session_token, secret, now=now)
    if not verify_csrf(session_token, csrf_token, secret):
        raise PermissionError("invalid CSRF token")
    return update_for_owner(connection, owner_id, record_id, changes)


def delete_opportunity(connection, session_token, csrf_token, secret, record_id, now=None):
    owner_id = _owner(connection, session_token, secret, now=now)
    if not verify_csrf(session_token, csrf_token, secret):
        raise PermissionError("invalid CSRF token")
    return delete_for_owner(connection, owner_id, record_id)
