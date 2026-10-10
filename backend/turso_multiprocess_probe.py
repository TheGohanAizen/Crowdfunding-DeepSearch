#!/usr/bin/env python3
"""Cross-process remote Turso quota probe using disposable test credentials.

Runs two independent Python processes with distinct remote connections.
This is a non-sending capability test, not a production authorization.
"""
import os
import subprocess
import sys
import uuid


TABLE = "crowdfunding_multiprocess_probe_v1"


def credentials():
    url = os.environ.get("TEST_TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TEST_TURSO_AUTH_TOKEN", "").strip()
    if (os.environ.get("I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE") != "yes"
            or not url.startswith("libsql://") or not token
            or url == os.environ.get("TURSO_DATABASE_URL", "").strip()
            or token == os.environ.get("TURSO_AUTH_TOKEN", "").strip()):
        raise ValueError("dedicated disposable test credentials required")
    return url, token


def worker(batch, key):
    import libsql
    url, token = credentials()
    conn = libsql.connect(database=url, auth_token=token)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute(
                "SELECT COUNT(*) FROM " + TABLE + " WHERE batch_id = ?", (batch,)
            ).fetchone()[0]
            if count:
                conn.rollback()
                return "quota_rejected"
            conn.execute(
                "INSERT INTO " + TABLE + " (probe_key, batch_id) VALUES (?, ?)",
                (key, batch),
            )
            conn.commit()
            return "committed"
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return "conflict_or_error"
    finally:
        conn.close()


def main():
    try:
        import libsql
        url, token = credentials()
    except (ValueError, ImportError):
        print("BLOCKED: disposable TEST_TURSO_* credentials and libsql are required.")
        return 2

    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        print(worker(sys.argv[2], sys.argv[3]))
        return 0

    batch = uuid.uuid4().hex
    conn = None
    try:
        conn = libsql.connect(database=url, auth_token=token)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS " + TABLE +
            " (probe_key TEXT PRIMARY KEY, batch_id TEXT NOT NULL)"
        )
        conn.commit()
        # Start separate interpreters simultaneously, without shell or secrets in args.
        processes = [
            subprocess.Popen(
                [sys.executable, __file__, "--worker", batch, batch + "-" + str(i)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            for i in range(2)
        ]
        results = []
        for process in processes:
            try:
                output, _ = process.communicate(timeout=45)
                results.append(output.strip() if process.returncode == 0 else "worker_failed")
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                results.append("worker_timed_out")

        count = conn.execute(
            "SELECT COUNT(*) FROM " + TABLE + " WHERE batch_id = ?", (batch,)
        ).fetchone()[0]
        print("Process outcomes:", ", ".join(results))
        print("Persisted reservations for one-slot quota:", count)
        if count == 1 and results.count("committed") == 1 and all(
            result in {"committed", "quota_rejected", "conflict_or_error"}
            for result in results
        ):
            print("PASS: cross-process test preserved one-slot quota (conflicts fail closed).")
            print("NOT VERIFIED: ambiguous commit recovery or production reservation path.")
            return 0
        print("INCONCLUSIVE: cross-process quota safety not established.")
        return 1
    except Exception as error:
        print("INCONCLUSIVE: multiprocess probe raised " + type(error).__name__ + ".")
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
