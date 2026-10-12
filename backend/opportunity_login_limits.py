"""Offline, persistent account login attempt throttling.

No HTTP routes are registered. Use a server-controlled normalized identity
and IP-derived bucket; never trust a client-provided identity as authorization.
"""
import hashlib
import time

WINDOW_SECONDS = 15 * 60
MAX_ATTEMPTS = 5


def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS opportunity_login_limits (
        bucket TEXT PRIMARY KEY,
        attempts INTEGER NOT NULL,
        window_start INTEGER NOT NULL
    )""")
    connection.commit()


def bucket_key(identity, ip_address):
    if not isinstance(identity, str) or not isinstance(ip_address, str):
        raise ValueError("invalid login bucket")
    if not identity.strip() or not ip_address.strip() or len(identity) > 254 or len(ip_address) > 64:
        raise ValueError("invalid login bucket")
    return hashlib.sha256((identity.strip().lower() + "|" + ip_address.strip()).encode()).hexdigest()


def record_attempt(connection, bucket, now=None):
    """Atomically reserve one attempt; False means temporarily throttled."""
    if not isinstance(bucket, str) or len(bucket) != 64 or any(c not in "0123456789abcdef" for c in bucket):
        raise ValueError("invalid login bucket")
    timestamp = int(time.time() if now is None else now)
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT attempts,window_start FROM opportunity_login_limits WHERE bucket=?", (bucket,)
        ).fetchone()
        if row is None or timestamp - row[1] >= WINDOW_SECONDS:
            connection.execute(
                """INSERT INTO opportunity_login_limits(bucket,attempts,window_start)
                VALUES (?,1,?) ON CONFLICT(bucket) DO UPDATE SET attempts=1,window_start=excluded.window_start""",
                (bucket, timestamp),
            )
            allowed = True
        elif row[0] >= MAX_ATTEMPTS:
            allowed = False
        else:
            connection.execute(
                "UPDATE opportunity_login_limits SET attempts=attempts+1 WHERE bucket=?",
                (bucket,),
            )
            allowed = True
        connection.commit()
        return allowed
    except Exception:
        connection.rollback()
        raise


def record_attempts(connection, buckets, now=None):
    """Atomically enforce all buckets (e.g. per-IP and per-account/IP).

    No bucket is charged when any bucket is already blocked.
    """
    if not isinstance(buckets, (tuple, list)) or not buckets:
        raise ValueError("login buckets required")
    unique = list(dict.fromkeys(buckets))
    for bucket in unique:
        if not isinstance(bucket, str) or len(bucket) != 64 or any(
            c not in "0123456789abcdef" for c in bucket
        ):
            raise ValueError("invalid login bucket")
    timestamp = int(time.time() if now is None else now)
    connection.execute("BEGIN IMMEDIATE")
    try:
        states = []
        for bucket in unique:
            row = connection.execute(
                "SELECT attempts,window_start FROM opportunity_login_limits WHERE bucket=?",
                (bucket,),
            ).fetchone()
            fresh = row is None or timestamp - row[1] >= WINDOW_SECONDS
            if not fresh and row[0] >= MAX_ATTEMPTS:
                connection.commit()
                return False
            states.append((bucket, fresh))
        for bucket, fresh in states:
            if fresh:
                connection.execute(
                    """INSERT INTO opportunity_login_limits(bucket,attempts,window_start)
                    VALUES (?,1,?) ON CONFLICT(bucket)
                    DO UPDATE SET attempts=1,window_start=excluded.window_start""",
                    (bucket, timestamp),
                )
            else:
                connection.execute(
                    "UPDATE opportunity_login_limits SET attempts=attempts+1 WHERE bucket=?",
                    (bucket,),
                )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
