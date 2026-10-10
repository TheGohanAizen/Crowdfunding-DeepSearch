#!/usr/bin/env python3
"""Concurrent actual application reservation test on disposable Turso only.

Two separate Python interpreters attempt different idempotency keys under the
same one-slot quota. This never calls a provider or enables live sending.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def validate():
    url = os.environ.get("TEST_TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TEST_TURSO_AUTH_TOKEN", "").strip()
    if (os.environ.get("I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE") != "yes"
            or not url.startswith("libsql://") or not token
            or url == os.environ.get("TURSO_DATABASE_URL", "").strip()
            or token == os.environ.get("TURSO_AUTH_TOKEN", "").strip()
            or os.environ.get("AUTOMATION_LIVE_SEND_ENABLED", "").strip().lower()
                in {"1", "true", "yes", "on"}):
        raise ValueError("disposable test credentials required")
    return url, token


def run_worker(mechanism, key):
    url, token = validate()
    import server
    server.AUTOMATION_STORAGE_BACKEND = "turso"
    os.environ["TURSO_DATABASE_URL"] = url
    os.environ["TURSO_AUTH_TOKEN"] = token
    os.environ["AUTOMATION_TURSO_RESERVATION_TEST_ONLY"] = "yes"
    server.automation_rate_limit_contract = lambda _: {
        "registered": True, "limit_per_hour": 1, "limit_per_day": 1,
    }
    result = server.reserve_automation_execution_with_quota({
        "idempotency_key": key,
        "mechanism": mechanism,
        "endpoint": "test-only-no-transport",
        "blockers": [],
    })
    print("reserved" if result.get("reserved") is True else
          "blocked:" + str(result.get("reason") or "unknown"))


def main():
    try:
        validate()
    except ValueError:
        print("BLOCKED: dedicated disposable TEST_TURSO_* credentials required.")
        return 2

    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        run_worker(sys.argv[2], sys.argv[3])
        return 0

    mechanism = "disposable_concurrent_" + uuid.uuid4().hex
    prefix = uuid.uuid4().hex
    processes = [
        subprocess.Popen(
            [sys.executable, __file__, "--worker", mechanism, prefix + "-" + str(i)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        for i in range(2)
    ]
    outcomes = []
    for process in processes:
        try:
            stdout, _ = process.communicate(timeout=60)
            outcomes.append(stdout.strip() if process.returncode == 0 else "worker_failed")
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            outcomes.append("worker_timed_out")

    # Independent verification from the durable remote ledger and rate tables.
    import libsql
    url, token = validate()
    conn = libsql.connect(database=url, auth_token=token)
    try:
        ledger_count = conn.execute(
            "SELECT COUNT(*) FROM automation_execution_ledger WHERE mechanism = ?",
            (mechanism,),
        ).fetchone()[0]
        quota_count = conn.execute(
            "SELECT COUNT(*) FROM automation_rate_events WHERE mechanism = ?",
            (mechanism,),
        ).fetchone()[0]
    finally:
        conn.close()
    print("Process outcomes:", ", ".join(outcomes))
    print("Committed ledger rows:", ledger_count)
    print("Committed quota rows:", quota_count)
    if (ledger_count == quota_count == 1
            and outcomes.count("reserved") == 1
            and all(value == "reserved" or value.startswith("blocked:")
                    for value in outcomes)):
        print("PASS: real app quota stayed within one slot across processes.")
        print("NOT VERIFIED: ambiguous commit recovery, transport readiness.")
        return 0
    print("INCONCLUSIVE: app-level concurrency result needs investigation.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
