#!/usr/bin/env python3
"""Verify that the expected Crowdfunding DeepSearch commit is live on Render.

This check only calls /api/deployment and /api/health. It never calls discovery
or a search provider, so it cannot consume Tavily search credits.
"""
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("PRODUCTION_URL", "https://crowdfunding-deepsearch.onrender.com").rstrip("/")
EXPECTED_COMMIT = os.environ.get("EXPECTED_COMMIT", "").strip()
ATTEMPTS = int(os.environ.get("VERIFY_ATTEMPTS", "24"))
DELAY_SECONDS = int(os.environ.get("VERIFY_DELAY_SECONDS", "15"))


def get_json(path):
    request = Request(BASE_URL + path, headers={"User-Agent": "CrowdfundingDeepSearch-ProductionVerifier/1.0"})
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def main():
    if not EXPECTED_COMMIT:
        print("EXPECTED_COMMIT is required.", file=sys.stderr)
        return 2

    expected = EXPECTED_COMMIT.lower()
    for attempt in range(1, ATTEMPTS + 1):
        try:
            deployment = get_json("/api/deployment")
            live_commit = str(deployment.get("commit") or "").lower()
            if live_commit == expected:
                health = get_json("/api/health")
                if health.get("status") != "ok":
                    raise RuntimeError("Health endpoint did not report ok.")
                print(f"Production verified: {live_commit} is live and healthy.")
                print("No discovery/search endpoint was called; no Tavily credits were used.")
                return 0
            print(f"Attempt {attempt}/{ATTEMPTS}: live commit is {live_commit or 'unknown'}; waiting for {expected}.")
        except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as error:
            print(f"Attempt {attempt}/{ATTEMPTS}: production check not ready: {error}")
        if attempt < ATTEMPTS:
            time.sleep(DELAY_SECONDS)

    print(f"Production verification timed out waiting for commit {expected}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
