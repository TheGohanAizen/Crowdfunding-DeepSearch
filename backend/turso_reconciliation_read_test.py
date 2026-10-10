#!/usr/bin/env python3
"""Read-only inspection of a potentially ambiguous Turso reservation.

Requires explicit disposable test credentials. No sends, retries, or updates.
"""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    url = os.environ.get("TEST_TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TEST_TURSO_AUTH_TOKEN", "").strip()
    if (os.environ.get("I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE") != "yes"
            or not url.startswith("libsql://") or not token
            or url == os.environ.get("TURSO_DATABASE_URL", "").strip()
            or token == os.environ.get("TURSO_AUTH_TOKEN", "").strip()):
        print("BLOCKED: dedicated disposable test credentials required.")
        return 2

    import server
    previous_backend = server.AUTOMATION_STORAGE_BACKEND
    previous_env = {k: os.environ.get(k) for k in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN")}
    try:
        server.AUTOMATION_STORAGE_BACKEND = "turso"
        os.environ["TURSO_DATABASE_URL"] = url
        os.environ["TURSO_AUTH_TOKEN"] = token
        # Existing durable test reservations are used; no new rows are written.
        with server.automation_ledger_connection() as conn:
            cursor = conn.execute(
                """SELECT idempotency_key, mechanism, outcome
                   FROM automation_execution_ledger
                   WHERE execution_mode = 'live'
                     AND mechanism LIKE 'disposable_reservation_probe_%'
                   ORDER BY recorded_at DESC LIMIT 1"""
            )
            row = cursor.fetchone()
            if row is None:
                print("INCONCLUSIVE: no prior disposable reservation found.")
                return 1
            key = server.automation_row_value(row, "idempotency_key", 0)
            mechanism = server.automation_row_value(row, "mechanism", 1)
            outcome = server.automation_row_value(row, "outcome", 2)
            ledger_count = conn.execute(
                """SELECT COUNT(*) FROM automation_execution_ledger
                   WHERE idempotency_key = ? AND execution_mode = 'live'""",
                (key,),
            ).fetchone()[0]
            quota_count = conn.execute(
                """SELECT COUNT(*) FROM automation_rate_events
                   WHERE idempotency_key = ? AND mechanism = ?""",
                (key, mechanism),
            ).fetchone()[0]
        print("Durable ledger rows:", ledger_count)
        print("Durable quota rows:", quota_count)
        print("Durable execution outcome:", outcome)
        if ledger_count == quota_count == 1 and outcome == "reserved":
            print("PASS: existing reservation and quota are independently observable.")
            print("ACTION: keep reserved attempt blocked; manual reconciliation required.")
            print("NOT VERIFIED: actual lost network acknowledgment or provider delivery.")
            return 0
        print("INCONCLUSIVE: durable state requires manual investigation; no retry authorized.")
        return 1
    except Exception as error:
        print("INCONCLUSIVE: reconciliation read raised " + type(error).__name__ + ".")
        return 1
    finally:
        server.AUTOMATION_STORAGE_BACKEND = previous_backend
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    sys.exit(main())
