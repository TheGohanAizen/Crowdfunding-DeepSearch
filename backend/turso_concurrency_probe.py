#!/usr/bin/env python3
"""Non-sending Turso concurrent transaction probe (disposable test DB only).

Uses the TEST_TURSO_* variables from turso_transaction_probe.py. Runs two
separate remote connections in concurrent threads; never touches app tables.
This is a capability probe, not production authorization.
"""
import os
import sys
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


def classify_error(error):
    """Classify known driver failures without printing URLs or tokens."""
    message = str(error).lower()
    if any(word in message for word in ("busy", "locked", "conflict", "concurrent")):
        return "lock_or_conflict"
    if any(word in message for word in ("transaction", "begin", "commit")):
        return "transaction_state"
    if any(word in message for word in ("timeout", "timed out", "deadline")):
        return "timeout"
    if any(word in message for word in ("closed", "connection", "transport")):
        return "connection"
    return "unclassified"


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

    table = "crowdfunding_concurrency_probe_v1"
    batch = uuid.uuid4().hex
    connection = None
    try:
        connection = libsql.connect(database=url, auth_token=token)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS " + table +
            " (probe_key TEXT PRIMARY KEY, batch_id TEXT NOT NULL)"
        )
        connection.commit()
        barrier = Barrier(2)

        def attempt(label, key):
            conn = libsql.connect(database=url, auth_token=token)
            try:
                barrier.wait(timeout=15)
                conn.execute("BEGIN IMMEDIATE")
                try:
                    existing = conn.execute(
                        "SELECT COUNT(*) FROM " + table + " WHERE batch_id = ?", (batch,)
                    ).fetchone()[0]
                    if existing:
                        conn.rollback()
                        return label, "quota_rejected"
                    conn.execute(
                        "INSERT INTO " + table + " (probe_key, batch_id) VALUES (?, ?)",
                        (key, batch),
                    )
                    conn.commit()
                    return label, "committed"
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise
            except Exception as error:
                category = classify_error(error)
                if category == "lock_or_conflict":
                    # Read-only reconciliation, never a second reservation attempt.
                    # A conflict never authorizes a provider send.
                    for _ in range(5):
                        time.sleep(0.2)
                        check = None
                        try:
                            check = libsql.connect(database=url, auth_token=token)
                            occupied = check.execute(
                                "SELECT COUNT(*) FROM " + table + " WHERE batch_id = ?",
                                (batch,),
                            ).fetchone()[0]
                            if occupied:
                                return label, "conflict_reconciled_quota_exhausted"
                        except Exception:
                            pass
                        finally:
                            if check is not None:
                                check.close()
                    return label, "conflict_unresolved"
                return label, "error:" + type(error).__name__ + ":" + category
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(attempt, "worker_a", batch + "-a"),
                pool.submit(attempt, "worker_b", batch + "-b"),
            ]
            outcomes = [future.result(timeout=60) for future in futures]

        count = connection.execute(
            "SELECT COUNT(*) FROM " + table + " WHERE batch_id = ?", (batch,)
        ).fetchone()[0]
        print("Worker outcomes:", ", ".join(label + "=" + result for label, result in outcomes))
        print("Persisted reservations for one-slot quota:", count)
        statuses = sorted(result for _, result in outcomes)
        if statuses in (
            ["committed", "quota_rejected"],
            ["committed", "conflict_reconciled_quota_exhausted"],
        ) and count == 1:
            print("PASS: one reservation committed; the other rejected or reconciled without retry.")
            print("NOT VERIFIED: process-level concurrency, crash ambiguity, production quota logic.")
            return 0
        print("INCONCLUSIVE: concurrent reservation behavior needs investigation; live sending remains blocked.")
        return 1
    except Exception as error:
        print("INCONCLUSIVE: concurrency probe raised " + type(error).__name__ + ".")
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())
