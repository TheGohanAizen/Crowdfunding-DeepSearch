#!/usr/bin/env python3
"""Non-sending remote libSQL transaction capability probe.

IMPORTANT: Run ONLY with a dedicated disposable Turso test database.
This script deliberately creates a table, writes rows, and rolls back a row.
It never imports the app backend, reads production credentials, or sends email.

Required:
    TEST_TURSO_DATABASE_URL=libsql://<dedicated-test-database>
    TEST_TURSO_AUTH_TOKEN=<dedicated-test-token>
    I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE=yes
    python3 backend/turso_transaction_probe.py

This probes transaction behavior; it does NOT certify concurrency, quota safety,
or permission to enable live sending.
"""
import os
import sys
import uuid


def main():
    url = os.environ.get("TEST_TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TEST_TURSO_AUTH_TOKEN", "").strip()
    acknowledged = os.environ.get("I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE") == "yes"
    if not acknowledged or not url.startswith("libsql://") or not token:
        print("BLOCKED: dedicated TEST_TURSO_* credentials and disposable-database acknowledgment required.")
        return 2
    if (url == os.environ.get("TURSO_DATABASE_URL", "").strip()
            or token == os.environ.get("TURSO_AUTH_TOKEN", "").strip()):
        print("BLOCKED: test database credentials match configured production credentials.")
        return 2

    try:
        import libsql
    except ImportError:
        print("BLOCKED: libsql Python package is not installed.")
        return 2

    table = "crowdfunding_atomic_probe_v1"
    first = "probe-" + uuid.uuid4().hex
    second = "probe-" + uuid.uuid4().hex
    connection = None
    try:
        connection = libsql.connect(database=url, auth_token=token)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS " + table +
            " (probe_key TEXT PRIMARY KEY, note TEXT NOT NULL)"
        )
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO " + table + " (probe_key, note) VALUES (?, ?)",
            (first, "committed"),
        )
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO " + table + " (probe_key, note) VALUES (?, ?)",
            (second, "rolled_back"),
        )
        connection.rollback()

        committed = connection.execute(
            "SELECT COUNT(*) FROM " + table + " WHERE probe_key = ?", (first,)
        ).fetchone()[0]
        rolled_back = connection.execute(
            "SELECT COUNT(*) FROM " + table + " WHERE probe_key = ?", (second,)
        ).fetchone()[0]

        reopened = libsql.connect(database=url, auth_token=token)
        try:
            durable = reopened.execute(
                "SELECT COUNT(*) FROM " + table + " WHERE probe_key = ?", (first,)
            ).fetchone()[0]
            absent = reopened.execute(
                "SELECT COUNT(*) FROM " + table + " WHERE probe_key = ?", (second,)
            ).fetchone()[0]
        finally:
            reopened.close()

        if (committed, rolled_back, durable, absent) != (1, 0, 1, 0):
            print("FAILED: remote commit/rollback visibility did not match expectations.")
            return 1
        print("PASS: remote BEGIN IMMEDIATE, commit, rollback and reopened-connection visibility.")
        print("NOT VERIFIED: concurrent quota reservations, crash/commit ambiguity, live sending.")
        return 0
    except Exception as error:
        # Avoid printing exception details: driver errors may contain database URLs.
        print("INCONCLUSIVE: remote transaction probe raised " + type(error).__name__ + ".")
        return 1
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
