"""Offline-only account storage prototype; no HTTP registration or login routes.

Email addresses are normalized for lookup; verification and abuse protection
must be implemented before accounts are enabled in production.
"""
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from opportunity_passwords import hash_password, verify_password

_EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}$")


def normalize_email(value):
    if not isinstance(value, str):
        raise ValueError("invalid email")
    email = value.strip().lower()
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        raise ValueError("invalid email")
    local, domain = email.rsplit("@", 1)
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("invalid email")
    return email


def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS opportunity_accounts (
        owner_id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        email_verified INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        disabled_at TEXT
    )""")
    connection.commit()


def register_unverified(connection, email, password):
    """Create an unverified account; caller MUST rate-limit registration."""
    email = normalize_email(email)
    password_hash = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()
    owner_id = uuid.uuid4().hex
    try:
        with connection:
            connection.execute(
                """INSERT INTO opportunity_accounts
                (owner_id,email,password_hash,email_verified,created_at)
                VALUES (?,?,?,0,?)""",
                (owner_id, email, password_hash, now),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("account cannot be registered") from exc
    return owner_id


def verify_credentials(connection, email, password):
    """Return owner only for enabled, email-verified accounts.

    Caller MUST apply rate limiting, generic login errors and TLS.
    """
    try:
        email = normalize_email(email)
    except ValueError:
        return None
    row = connection.execute(
        """SELECT owner_id,password_hash,email_verified,disabled_at
        FROM opportunity_accounts WHERE email=?""", (email,)
    ).fetchone()
    if row is None:
        return None
    owner_id, password_hash, verified, disabled = row
    if disabled is not None or verified != 1:
        return None
    if not verify_password(password, password_hash):
        return None
    return owner_id


def disable_account(connection, owner_id):
    """Disable an account and revoke all its sessions atomically.

    Requires a trusted administrative caller and initialized session registry.
    Never expose this function as an unauthenticated public route.
    """
    if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
        raise ValueError("invalid account")
    timestamp = datetime.now(timezone.utc).isoformat()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("SELECT 1 FROM opportunity_active_sessions LIMIT 1")
        result = connection.execute(
            "UPDATE opportunity_accounts SET disabled_at=? WHERE owner_id=? AND disabled_at IS NULL",
            (timestamp, owner_id),
        )
        if result.rowcount:
            connection.execute(
                """UPDATE opportunity_active_sessions SET revoked_at=?
                WHERE owner_id=? AND revoked_at IS NULL""",
                (int(datetime.now(timezone.utc).timestamp()), owner_id),
            )
        connection.commit()
        return result.rowcount == 1
    except Exception:
        connection.rollback()
        raise
