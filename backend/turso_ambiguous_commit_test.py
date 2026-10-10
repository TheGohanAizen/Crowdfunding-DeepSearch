#!/usr/bin/env python3
"""Fault injection for ambiguous Turso reservation commits (no remote DB needed).

The fake connection commits both rows, then raises as if the acknowledgment was
lost. The actual application reservation function must deny authorization.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


class AmbiguousConnection:
    def __init__(self):
        self.rows = []
        self.committed = False
        self.rollback_called = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, parameters=()):
        if sql.startswith("BEGIN"):
            return self
        if "SELECT 1 FROM automation_execution_ledger" in sql:
            return FakeResult(None)
        if "SELECT COUNT(*) FROM automation_rate_events" in sql:
            return FakeResult((0,))
        if "INSERT INTO automation_execution_ledger" in sql:
            self.rows.append("ledger")
            return self
        if "INSERT INTO automation_rate_events" in sql:
            self.rows.append("quota")
            return self
        raise AssertionError("Unexpected SQL in fault injection")

    def commit(self):
        assert self.rows == ["ledger", "quota"]
        self.committed = True
        raise ConnectionError("simulated lost commit acknowledgment")

    def rollback(self):
        self.rollback_called = True


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def main():
    original_backend = server.AUTOMATION_STORAGE_BACKEND
    original_connection = server.automation_ledger_connection
    original_contract = server.automation_rate_limit_contract
    previous_env = {
        name: os.environ.get(name) for name in (
            "AUTOMATION_TURSO_RESERVATION_TEST_ONLY",
            "I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE",
            "TURSO_DATABASE_URL", "TEST_TURSO_DATABASE_URL",
            "TURSO_AUTH_TOKEN", "TEST_TURSO_AUTH_TOKEN",
            "AUTOMATION_LIVE_SEND_ENABLED",
        )
    }
    conn = AmbiguousConnection()
    try:
        server.AUTOMATION_STORAGE_BACKEND = "turso"
        server.automation_ledger_connection = lambda: conn
        server.automation_rate_limit_contract = lambda _: {
            "registered": True, "limit_per_hour": 1, "limit_per_day": 1,
        }
        os.environ.update({
            "AUTOMATION_TURSO_RESERVATION_TEST_ONLY": "yes",
            "I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE": "yes",
            "TURSO_DATABASE_URL": "libsql://disposable.invalid",
            "TEST_TURSO_DATABASE_URL": "libsql://disposable.invalid",
            "TURSO_AUTH_TOKEN": "fake-test-token",
            "TEST_TURSO_AUTH_TOKEN": "fake-test-token",
            "AUTOMATION_LIVE_SEND_ENABLED": "false",
        })
        result = server.reserve_automation_execution_with_quota({
            "idempotency_key": "fault-injection-unique-key",
            "mechanism": "fake-no-transport",
            "endpoint": "test-only",
            "blockers": [],
        })
        print("Simulated database committed:", conn.committed)
        print("Application allowed sending:", result.get("allowed"))
        print("Application outcome:", result.get("reason"))
        if (conn.committed and result.get("allowed") is False
                and result.get("reserved") is False
                and result.get("reason") == "turso_reservation_uncertain"):
            print("PASS: lost commit acknowledgment fails closed without authorizing sending.")
            print("NOT VERIFIED: real network interruption, durable reconciliation.")
            return 0
        print("FAILED: ambiguous commit safety invariant.")
        return 1
    finally:
        server.AUTOMATION_STORAGE_BACKEND = original_backend
        server.automation_ledger_connection = original_connection
        server.automation_rate_limit_contract = original_contract
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    sys.exit(main())
