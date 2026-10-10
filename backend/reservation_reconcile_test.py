#!/usr/bin/env python3
"""Offline classification checks for the read-only reconciliation command."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reservation_reconcile as tool


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, ledger, events):
        self.ledger, self.events = ledger, events
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def execute(self, sql, args):
        assert len(args) == 1
        return Cursor(self.ledger if "FROM automation_execution_ledger" in sql else self.events)


def main():
    original = tool.server.automation_ledger_connection
    cases = [
        ([], [], "missing_or_unconfirmed"),
        ([], [("test", 1)], "inconsistent_quota_without_ledger"),
        ([("test", "reserved", 0, None)], [("test", 1)], "confirmed_ledger_and_quota"),
        ([("test", "reserved", 0, None)], [], "inconsistent_ledger_quota"),
        ([("test", "reserved", 0, None)], [("other", 1)], "inconsistent_ledger_quota"),
    ]
    try:
        for ledger, events, expected in cases:
            tool.server.automation_ledger_connection = lambda: Connection(ledger, events)
            result = tool.inspect("offline-test-key")
            assert result["classification"] == expected, (expected, result)
            assert result["automatic_retry_allowed"] is False
            assert result["transport_invoked"] is False
        print("PASS: five read-only reconciliation classifications; retries always denied.")
        print("NOT VERIFIED: real network interruption or provider delivery.")
        return 0
    finally:
        tool.server.automation_ledger_connection = original


if __name__ == "__main__":
    sys.exit(main())
