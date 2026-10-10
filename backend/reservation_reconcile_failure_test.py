#!/usr/bin/env python3
"""Fault-injection regression: reconciliation never authorizes retry on read failures."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reservation_reconcile as reconcile


class BrokenConnection:
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def execute(self, *_):
        raise ConnectionError("simulated remote connection interruption")


def main():
    original = reconcile.server.automation_ledger_connection
    cases = [
        ("connect_failed", lambda: (_ for _ in ()).throw(ConnectionError("simulated connect failure"))),
        ("read_failed", lambda: BrokenConnection()),
    ]
    try:
        for name, factory in cases:
            reconcile.server.automation_ledger_connection = factory
            try:
                reconcile.inspect("fault-injection-only")
            except ConnectionError:
                print("PASS:", name, "reported as unavailable; no retry decision returned.")
            else:
                print("FAIL:", name, "unexpectedly returned a reconciliation decision.")
                return 1
        print("PASS: reconciliation connection failures do not authorize retries or sending.")
        print("NOT VERIFIED: real Turso network disconnection or provider delivery.")
        return 0
    finally:
        reconcile.server.automation_ledger_connection = original


if __name__ == "__main__":
    sys.exit(main())
