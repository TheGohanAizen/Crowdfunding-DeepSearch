#!/usr/bin/env python3
"""Test the real Turso reservation function against disposable test credentials.

No transport calls. Never use production credentials or run on Render.
"""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    url = os.environ.get("TEST_TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TEST_TURSO_AUTH_TOKEN", "").strip()
    if (os.environ.get("I_ACKNOWLEDGE_DISPOSABLE_TEST_DATABASE") != "yes"
            or not url.startswith("libsql://") or not token
            or url == os.environ.get("TURSO_DATABASE_URL", "").strip()
            or token == os.environ.get("TURSO_AUTH_TOKEN", "").strip()
            or os.environ.get("AUTOMATION_LIVE_SEND_ENABLED", "").strip().lower()
                in {"1", "true", "yes", "on"}):
        print("BLOCKED: disposable test credentials and disabled sending required.")
        return 2

    import server
    previous_backend = server.AUTOMATION_STORAGE_BACKEND
    original_contract = server.automation_rate_limit_contract
    previous_env = {
        key: os.environ.get(key)
        for key in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN",
                    "AUTOMATION_TURSO_RESERVATION_TEST_ONLY")
    }
    try:
        server.AUTOMATION_STORAGE_BACKEND = "turso"
        os.environ["TURSO_DATABASE_URL"] = url
        os.environ["TURSO_AUTH_TOKEN"] = token
        os.environ["AUTOMATION_TURSO_RESERVATION_TEST_ONLY"] = "yes"
        mechanism = "disposable_reservation_probe_" + uuid.uuid4().hex
        server.automation_rate_limit_contract = lambda _: {
            "registered": True, "limit_per_hour": 1, "limit_per_day": 1,
        }
        prefix = uuid.uuid4().hex
        def plan(key):
            return {"idempotency_key": key, "mechanism": mechanism,
                    "endpoint": "test-only-no-transport", "blockers": []}

        first = server.reserve_automation_execution_with_quota(plan(prefix + "-a"))
        duplicate = server.reserve_automation_execution_with_quota(plan(prefix + "-a"))
        exhausted = server.reserve_automation_execution_with_quota(plan(prefix + "-b"))
        print("First reservation:", first.get("reason"))
        print("Duplicate reservation:", duplicate.get("reason"))
        print("Exhausted reservation:", exhausted.get("reason"))
        if (first.get("reserved") is True
                and duplicate.get("reason") == "duplicate_execution"
                and exhausted.get("reason") == "rate_limit_exhausted"):
            print("PASS: real Turso reservation function enforces idempotency and quota.")
            print("NOT VERIFIED: concurrent app reservations or ambiguous commit recovery.")
            return 0
        print("INCONCLUSIVE: real Turso reservation outcomes differed; sending remains disabled.")
        return 1
    except Exception as error:
        print("INCONCLUSIVE: integration probe raised " + type(error).__name__ + ".")
        return 1
    finally:
        server.AUTOMATION_STORAGE_BACKEND = previous_backend
        server.automation_rate_limit_contract = original_contract
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    sys.exit(main())
