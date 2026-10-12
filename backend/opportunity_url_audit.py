"""Read-only V5 URL identity audit. Offline only; never modifies a database.

Usage: python backend/opportunity_url_audit.py /path/to/copy.sqlite3
Pass a *copy* of the database, never the live store.
"""
import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from opportunity_store import normalize_url


def audit(connection):
    """Return counts and collision groups without exposing owner IDs or URLs."""
    rows = connection.execute(
        "SELECT owner_id, record_id, normalized_url FROM opportunity_records"
    ).fetchall()
    by_owner_url = defaultdict(list)
    invalid = 0
    for owner, record_id, original in rows:
        try:
            normalized = normalize_url(original)
        except (ValueError, TypeError):
            invalid += 1
            continue
        by_owner_url[(owner, normalized)].append(record_id)
    groups = [ids for ids in by_owner_url.values() if len(ids) > 1]
    return {
        "records_checked": len(rows),
        "invalid_normalized_urls": invalid,
        "collision_groups": len(groups),
        "records_in_collision_groups": sum(map(len, groups)),
        "safe_to_auto_migrate": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database_copy", type=Path)
    args = parser.parse_args()
    # URI mode=ro prevents writes even if the audit is changed later.
    uri = args.database_copy.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        report = audit(connection)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
