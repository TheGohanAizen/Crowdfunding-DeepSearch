#!/usr/bin/env python3
"""Local operator report for unresolved automation attempts. Never sends messages.

Usage:
  python3 backend/recovery_report.py
  python3 backend/recovery_report.py --json
  python3 backend/recovery_report.py --limit 20

Reads the configured automation storage. Requires access to the same environment
variables as the backend. Never enable public admin endpoints for this report.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone

try:
    from backend import server
except ModuleNotFoundError:
    import server


def _parse_utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def build_report(limit=100):
    """Read unresolved live records; never call a transport or transition."""
    if not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    now = datetime.now(timezone.utc)
    with server.automation_ledger_connection() as connection:
        total = connection.execute(
            """SELECT COUNT(*) FROM automation_execution_ledger
               WHERE execution_mode = 'live' AND outcome IN ('reserved', 'unknown')"""
        ).fetchone()[0]
        cursor = connection.execute(
            """SELECT idempotency_key, mechanism, outcome, recorded_at
               FROM automation_execution_ledger
               WHERE execution_mode = 'live' AND outcome IN ('reserved', 'unknown')
               ORDER BY recorded_at ASC LIMIT ?""",
            (limit,),
        )
        rows = cursor.fetchall()
    records = []
    for row in rows:
        recorded_at = server.automation_row_value(row, "recorded_at", 3)
        timestamp = _parse_utc(recorded_at)
        age_hours = round(max(0, (now - timestamp).total_seconds()) / 3600, 1) if timestamp else None
        records.append({
            "idempotency_key": server.automation_row_value(row, "idempotency_key", 0),
            "mechanism": server.automation_row_value(row, "mechanism", 1),
            "outcome": server.automation_row_value(row, "outcome", 2),
            "recorded_at": recorded_at,
            "age_hours": age_hours,
            "action": "manual_reconciliation_required",
        })
    return {
        "status": "ok",
        "storage_backend": str(server.AUTOMATION_STORAGE_BACKEND).lower(),
        "unresolved_total": total,
        "shown": len(records),
        "truncated": total > len(records),
        "outcomes_shown": dict(Counter(record["outcome"] for record in records)),
        "records": records,
        "sent": False,
        "transport_invoked": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable report")
    parser.add_argument("--limit", type=int, default=100, help="maximum records, 1-500")
    args = parser.parse_args(argv)
    try:
        report = build_report(args.limit)
    except (ValueError, RuntimeError, OSError) as error:
        print("Recovery report unavailable: " + str(error), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("Automation recovery report (no sending)")
        print("Storage:", report["storage_backend"])
        print("Unresolved:", report["unresolved_total"], "| Showing:", report["shown"])
        if report["truncated"]:
            print("Additional records omitted; increase --limit to inspect more.")
        for record in report["records"]:
            print("{outcome} | {mechanism} | {idempotency_key} | age_hours={age_hours}".format(**record))
        print("Every unresolved record requires manual reconciliation; do not automatically resend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
