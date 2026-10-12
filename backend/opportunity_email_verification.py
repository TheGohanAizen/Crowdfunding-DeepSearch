"""Offline email ownership challenge primitives.

No email is sent and no HTTP routes are registered. A trusted caller must
deliver the raw token only to the intended mailbox and enforce resend limits.
"""
import hashlib
import hmac
import secrets
import time

TOKEN_LIFETIME = 30 * 60


def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS opportunity_email_challenges (
        owner_id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        consumed_at INTEGER
    )""")
    connection.commit()


def issue_challenge(connection, owner_id, now=None):
    if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
        raise ValueError("invalid account")
    current = int(time.time() if now is None else now)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT email_verified,disabled_at FROM opportunity_accounts WHERE owner_id=?",
            (owner_id,),
        ).fetchone()
        if row is None or row[0] != 0 or row[1] is not None:
            raise ValueError("account not eligible for verification")
        connection.execute(
            """INSERT INTO opportunity_email_challenges
            (owner_id,token_hash,expires_at,consumed_at) VALUES (?,?,?,NULL)
            ON CONFLICT(owner_id) DO UPDATE SET
            token_hash=excluded.token_hash,expires_at=excluded.expires_at,consumed_at=NULL""",
            (owner_id, token_hash, current + TOKEN_LIFETIME),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return token


def consume_challenge(connection, owner_id, token, now=None):
    if not isinstance(owner_id, str) or not owner_id.strip():
        return False
    if not isinstance(token, str) or not 20 <= len(token) <= 256:
        return False
    current = int(time.time() if now is None else now)
    supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            """SELECT c.token_hash,c.expires_at,c.consumed_at,a.email_verified,a.disabled_at
            FROM opportunity_email_challenges c JOIN opportunity_accounts a
            ON a.owner_id=c.owner_id WHERE c.owner_id=?""",
            (owner_id,),
        ).fetchone()
        valid = (
            row is not None and row[2] is None and current < row[1]
            and row[3] == 0 and row[4] is None
            and hmac.compare_digest(row[0], supplied)
        )
        if valid:
            connection.execute(
                "UPDATE opportunity_accounts SET email_verified=1 WHERE owner_id=?",
                (owner_id,),
            )
            connection.execute(
                "UPDATE opportunity_email_challenges SET consumed_at=? WHERE owner_id=?",
                (current, owner_id),
            )
        connection.commit()
        return valid
    except Exception:
        connection.rollback()
        raise
