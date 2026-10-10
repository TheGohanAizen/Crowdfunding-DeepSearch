#!/usr/bin/env python3
"""Disposable Turso atomic ledger+quota rollback test; no provider sending.

Requires TEST_TURSO_DATABASE_URL, TEST_TURSO_AUTH_TOKEN, and
I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE=yes. Never use production credentials.
"""
import os
import sys
import uuid


def main():
    url = os.environ.get("TEST_TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TEST_TURSO_AUTH_TOKEN", "").strip()
    if (os.environ.get("I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE") != "yes"
            or not url.startswith("libsql://") or not token
            or url == os.environ.get("TURSO_DATABASE_URL", "").strip()
            or token == os.environ.get("TURSO_AUTH_TOKEN", "").strip()):
        print("BLOCKED: dedicated disposable test database credentials required.")
        return 2
    try:
        import libsql
    except ImportError:
        print("BLOCKED: libsql package unavailable.")
        return 2

    prefix = uuid.uuid4().hex
    ledger = "crowdfunding_atomic_ledger_probe_v1"
    quota = "crowdfunding_atomic_quota_probe_v1"
    conn = None
    try:
        conn = libsql.connect(database=url, auth_token=token)
        conn.execute("CREATE TABLE IF NOT EXISTS " + ledger +
                     " (key TEXT PRIMARY KEY, batch_id TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS " + quota +
                     " (key TEXT PRIMARY KEY, batch_id TEXT NOT NULL)")
        conn.commit()

        # Successful two-table commit.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO " + ledger + " VALUES (?, ?)", (prefix + "-ok", prefix))
        conn.execute("INSERT INTO " + quota + " VALUES (?, ?)", (prefix + "-ok", prefix))
        conn.commit()

        # Force a quota constraint failure after the ledger insert.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO " + ledger + " VALUES (?, ?)", (prefix + "-fail", prefix))
        try:
            conn.execute("INSERT INTO " + quota + " VALUES (?, ?)", (prefix + "-ok", prefix))
            print("FAILED: duplicate quota key was accepted.")
            conn.rollback()
            return 1
        except Exception:
            conn.rollback()

        # Force a ledger constraint failure; quota must remain unchanged.
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("INSERT INTO " + ledger + " VALUES (?, ?)", (prefix + "-ok", prefix))
            print("FAILED: duplicate ledger key was accepted.")
            conn.rollback()
            return 1
        except Exception:
            conn.rollback()

        reopened = libsql.connect(database=url, auth_token=token)
        try:
            ledger_count = reopened.execute(
                "SELECT COUNT(*) FROM " + ledger + " WHERE batch_id = ?", (prefix,)
            ).fetchone()[0]
            quota_count = reopened.execute(
                "SELECT COUNT(*) FROM " + quota + " WHERE batch_id = ?", (prefix,)
            ).fetchone()[0]
            partial = reopened.execute(
                "SELECT COUNT(*) FROM " + ledger + " WHERE key = ?", (prefix + "-fail",)
            ).fetchone()[0]
        finally:
            reopened.close()

        print("Committed ledger rows:", ledger_count)
        print("Committed quota rows:", quota_count)
        print("Partial ledger rows after forced quota failure:", partial)
        if (ledger_count, quota_count, partial) != (1, 1, 0):
            print("FAILED: atomic ledger/quota rollback invariant.")
            return 1
        print("PASS: remote ledger+quota commit and rollback remain atomic.")
        print("NOT VERIFIED: cross-process contention, uncertain commits, production implementation.")
        return 0
    except Exception as error:
        print("INCONCLUSIVE: atomic rollback probe raised " + type(error).__name__ + ".")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
