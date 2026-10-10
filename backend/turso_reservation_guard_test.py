#!/usr/bin/env python3
"""Regression: Turso application reservation must fail closed until enabled.

No database credentials or provider calls are required. This checks the actual
production reservation entry point, rather than a stand-in SQL probe.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


def main():
    previous = server.AUTOMATION_STORAGE_BACKEND
    original_connection = server.automation_ledger_connection
    calls = []
    try:
        server.AUTOMATION_STORAGE_BACKEND = "turso"

        def forbidden_connection():
            calls.append("database")
            raise AssertionError("Blocked Turso reservation must not open a database.")

        server.automation_ledger_connection = forbidden_connection
        result = server.reserve_automation_execution_with_quota({
            "idempotency_key": "test-fail-closed-only",
            "mechanism": "brevo_email_v3",
            "endpoint": "test-only",
            "blockers": [],
        })
        assert result == {
            "reserved": False,
            "allowed": False,
            "reason": "atomic_backend_not_verified",
            "network_io": False,
        }, result
        assert not calls, "Turso guard unexpectedly opened database."
        print("PASS: actual Turso reservation entry point remains fail-closed.")
        print("PASS: no database connection or provider call was attempted.")
        print("NOT VERIFIED: live Turso reservation integration or uncertain commit handling.")
        return 0
    finally:
        server.AUTOMATION_STORAGE_BACKEND = previous
        server.automation_ledger_connection = original_connection


if __name__ == "__main__":
    sys.exit(main())
