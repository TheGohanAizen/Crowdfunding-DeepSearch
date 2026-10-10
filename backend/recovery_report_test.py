#!/usr/bin/env python3
"""Non-sending regression tests for the operator recovery report.

Run from the repository root:
    python3 backend/recovery_report_test.py

Uses an isolated temporary SQLite database. No Turso credentials, external
provider calls, or public administration endpoints are required.
"""
import os
import sys
import tempfile
from unittest.mock import patch

try:
    from backend import recovery_report
    from backend import server
except ModuleNotFoundError as error:
    if error.name != "backend":
        raise
    import recovery_report
    import server


def run():
    with tempfile.TemporaryDirectory(prefix="crowdfunding-recovery-test-") as directory:
        ledger = os.path.join(directory, "recovery.sqlite3")
        with patch.object(server, "AUTOMATION_STORAGE_BACKEND", "sqlite"), \
             patch.object(server, "AUTOMATION_LEDGER_PATH", ledger):
            empty = recovery_report.build_report()
            assert empty["unresolved_total"] == 0
            assert empty["sent"] is False
            assert empty["transport_invoked"] is False

            plans = (
                ("recovery-reserved", "brevo_email_v3"),
                ("recovery-unknown", "brevo_email_v3"),
                ("recovery-confirmed", "brevo_email_v3"),
            )
            for key, mechanism in plans:
                result = server.reserve_live_automation_execution({
                    "idempotency_key": key,
                    "mechanism": mechanism,
                    "endpoint": "https://example.invalid/non-sending-test",
                    "blockers": [],
                })
                assert result["reserved"] is True

            server.transition_automation_execution(
                "recovery-unknown", "unknown",
                resolution_reason="provider outcome cannot be established",
            )
            server.transition_automation_execution(
                "recovery-confirmed", "sent",
                provider_message_id="recovery-test-provider-id",
            )

            report = recovery_report.build_report(limit=1)
            assert report["unresolved_total"] == 2
            assert report["shown"] == 1
            assert report["truncated"] is True
            assert report["sent"] is False
            assert report["transport_invoked"] is False
            assert report["records"][0]["action"] == "manual_reconciliation_required"

            full = recovery_report.build_report(limit=10)
            assert full["unresolved_total"] == 2
            assert full["shown"] == 2
            assert full["truncated"] is False
            assert {row["outcome"] for row in full["records"]} == {"reserved", "unknown"}
            assert all(row["age_hours"] is None or row["age_hours"] >= 0 for row in full["records"])
            assert all(row["idempotency_key"] != "recovery-confirmed" for row in full["records"])

            for invalid_limit in (0, 501, "10"):
                try:
                    recovery_report.build_report(invalid_limit)
                    raise AssertionError("Invalid report limit accepted")
                except ValueError:
                    pass

            assert server.find_automation_execution_record("recovery-reserved")["outcome"] == "reserved"
            assert server.find_automation_execution_record("recovery-unknown")["outcome"] == "unknown"
            assert server.find_automation_execution_record("recovery-confirmed")["outcome"] == "sent"

    print("Recovery report regression tests passed: isolated storage, unresolved filtering, pagination, no mutation or sending.")


if __name__ == "__main__":
    run()
