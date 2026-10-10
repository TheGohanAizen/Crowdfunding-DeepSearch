#!/usr/bin/env python3
"""Non-sending Turso test of the application's real ledger and quota schema.

Only disposable TEST_TURSO_* credentials are accepted. The production
reservation function remains disabled for Turso. This probe does not call any
provider or production endpoint.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone


def main():
    url = os.environ.get("TEST_TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TEST_TURSO_AUTH_TOKEN", "").strip()
    if (os.environ.get("I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE") != "yes"
            or not url.startswith("libsql://") or not token
            or url == os.environ.get("TURSO_DATABASE_URL", "").strip()
            or token == os.environ.get("TURSO_AUTH_TOKEN", "").strip()):
        print("BLOCKED: dedicated disposable TEST_TURSO_* credentials required.")
        return 2
    try:
        import libsql
    except ImportError:
        print("BLOCKED: libsql package unavailable.")
        return 2

    conn = None
    try:
        conn = libsql.connect(database=url, auth_token=token)
        # Match the application schema and uniqueness constraints, using
        # separate probe tables to avoid disturbing existing test data.
        conn.execute("""CREATE TABLE IF NOT EXISTS crowdfunding_app_ledger_probe_v1 (
            ledger_key TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL,
            execution_mode TEXT NOT NULL, mechanism TEXT NOT NULL,
            outcome TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS crowdfunding_app_rate_probe_v1 (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            mechanism TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            consumed_at TEXT NOT NULL,
            UNIQUE(mechanism, idempotency_key)
        )""")
        conn.commit()

        prefix = uuid.uuid4().hex
        mechanism = "probe_only_no_send"
        now = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        def reserve(key, hourly_limit):
            try:
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute(
                    "SELECT 1 FROM crowdfunding_app_ledger_probe_v1 WHERE ledger_key = ?",
                    (key + ":live",),
                ).fetchone():
                    conn.rollback()
                    return "duplicate_execution"
                used = conn.execute(
                    "SELECT COUNT(*) FROM crowdfunding_app_rate_probe_v1 "
                    "WHERE mechanism = ? AND consumed_at > ?",
                    (mechanism, cutoff),
                ).fetchone()[0]
                if used >= hourly_limit:
                    conn.rollback()
                    return "rate_limit_exhausted"
                conn.execute(
                    "INSERT INTO crowdfunding_app_ledger_probe_v1 "
                    "(ledger_key,idempotency_key,execution_mode,mechanism,outcome,sent,recorded_at) "
                    "VALUES (?,?,'live',?,'reserved',0,?)",
                    (key + ":live", key, mechanism, now),
                )
                conn.execute(
                    "INSERT INTO crowdfunding_app_rate_probe_v1 "
                    "(mechanism,idempotency_key,consumed_at) VALUES (?,?,?)",
                    (mechanism, key, now),
                )
                conn.commit()
                return "reserved"
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                return "blocked_error"

        first = reserve(prefix + "-a", 1)
        duplicate = reserve(prefix + "-a", 1)
        second = reserve(prefix + "-b", 1)
        rows = conn.execute(
            "SELECT COUNT(*) FROM crowdfunding_app_ledger_probe_v1 "
            "WHERE idempotency_key LIKE ?", (prefix + "-%",)
        ).fetchone()[0]
        quota_rows = conn.execute(
            "SELECT COUNT(*) FROM crowdfunding_app_rate_probe_v1 "
            "WHERE idempotency_key LIKE ?", (prefix + "-%",)
        ).fetchone()[0]
        print("First reservation:", first)
        print("Duplicate reservation:", duplicate)
        print("Second reservation after quota:", second)
        print("Ledger rows:", rows, "Quota rows:", quota_rows)
        if (first, duplicate, second, rows, quota_rows) == (
            "reserved", "duplicate_execution", "rate_limit_exhausted", 1, 1
        ):
            print("PASS: application-shaped ledger, idempotency and quota checks.")
            print("NOT VERIFIED: production function, concurrency, uncertain commits.")
            return 0
        print("INCONCLUSIVE: application-shaped reservation behavior differed.")
        return 1
    except Exception as error:
        print("INCONCLUSIVE: probe raised " + type(error).__name__ + ".")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
