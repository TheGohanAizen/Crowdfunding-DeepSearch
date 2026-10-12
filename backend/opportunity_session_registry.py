"""Offline server-side session revocation registry.

Not registered as HTTP routes. Call require_active_session for every private
request after issuing a signed token and registering it here.
"""
import hashlib
import hmac
import time

from opportunity_sessions import verify_session, MAX_AGE_SECONDS


def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS opportunity_active_sessions (
        token_hash TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        revoked_at INTEGER
    )""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_opportunity_sessions_owner ON opportunity_active_sessions(owner_id)"
    )
    connection.commit()


def _digest(token):
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise ValueError("invalid session token")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register_session(connection, token, secret, now=None):
    current = int(time.time() if now is None else now)
    owner = verify_session(token, secret, now=current)
    digest = _digest(token)
    with connection:
        connection.execute(
            """INSERT INTO opportunity_active_sessions
            (token_hash,owner_id,expires_at,revoked_at)
            VALUES (?,?,?,NULL)""",
            (digest, owner, current + MAX_AGE_SECONDS),
        )
    return owner


def require_active_session(connection, token, secret, now=None):
    current = int(time.time() if now is None else now)
    owner = verify_session(token, secret, now=current)
    row = connection.execute(
        """SELECT owner_id,expires_at,revoked_at
        FROM opportunity_active_sessions WHERE token_hash=?""",
        (_digest(token),),
    ).fetchone()
    if row is None or row[2] is not None or current > row[1] or not hmac.compare_digest(row[0], owner):
        raise PermissionError("session not active")
    return owner


def revoke_session(connection, token):
    digest = _digest(token)
    with connection:
        result = connection.execute(
            "UPDATE opportunity_active_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
            (int(time.time()), digest),
        )
    return result.rowcount > 0


def revoke_owner_sessions(connection, owner_id):
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("owner required")
    with connection:
        result = connection.execute(
            """UPDATE opportunity_active_sessions SET revoked_at=?
            WHERE owner_id=? AND revoked_at IS NULL""",
            (int(time.time()), owner_id),
        )
    return result.rowcount
