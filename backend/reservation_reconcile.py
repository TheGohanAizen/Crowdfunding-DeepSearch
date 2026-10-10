#!/usr/bin/env python3
"""Read-only investigation of one automation reservation and its quota event.

Usage: python3 backend/reservation_reconcile.py --key YOUR_IDEMPOTENCY_KEY
Never sends, retries, changes outcomes, or declares a missing key safe to retry.
"""
import argparse
import json
import sys

try:
    from backend import server
except ModuleNotFoundError:
    import server


def inspect(key):
    if not isinstance(key, str) or not key.strip() or len(key) > 512:
        raise ValueError("A nonempty idempotency key (max 512 chars) is required")
    key = key.strip()
    with server.automation_ledger_connection() as conn:
        cursor = conn.execute(
            """SELECT mechanism, outcome, sent, provider_message_id
               FROM automation_execution_ledger
               WHERE ledger_key = ? AND execution_mode = 'live'""",
            (key + ":live",),
        )
        row = cursor.fetchone()
        events = conn.execute(
            """SELECT mechanism, COUNT(*) FROM automation_rate_events
               WHERE idempotency_key = ? GROUP BY mechanism""",
            (key,),
        ).fetchall()

    if row is None:
        status = "missing_or_unconfirmed" if not events else "inconsistent_quota_without_ledger"
        mechanism = None
        outcome = None
        sent = False
        provider_id_present = False
    else:
        mechanism = server.automation_row_value(row, "mechanism", 0)
        outcome = server.automation_row_value(row, "outcome", 1)
        sent = bool(server.automation_row_value(row, "sent", 2))
        provider_id_present = bool(server.automation_row_value(row, "provider_message_id", 3))
        matching = sum(
            server.automation_row_value(e, "COUNT(*)", 1, 0)
            for e in events
            if server.automation_row_value(e, "mechanism", 0) == mechanism
        )
        total = sum(server.automation_row_value(e, "COUNT(*)", 1, 0) for e in events)
        status = ("confirmed_ledger_and_quota" if matching == 1 and total == 1
                  else "inconsistent_ledger_quota")

    return {
        "idempotency_key": key,
        "classification": status,
        "outcome": outcome,
        "sent": sent,
        "provider_message_id_present": provider_id_present,
        "quota_event_count": sum(
            server.automation_row_value(e, "COUNT(*)", 1, 0) for e in events
        ),
        "automatic_retry_allowed": False,
        "transport_invoked": False,
        "action": "manual_reconciliation_required",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = inspect(args.key)
    except Exception as error:
        print("Reconciliation unavailable: " + type(error).__name__, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Classification:", report["classification"])
        print("Outcome:", report["outcome"])
        print("Quota events:", report["quota_event_count"])
        print("Automatic retry allowed: NO")
        print("Action: manual reconciliation required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
