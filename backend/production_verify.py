#!/usr/bin/env python3
"""Verify that the expected Crowdfunding DeepSearch commit is live on Render.

This check only calls deployment/health and non-sending local readiness endpoints.
It never calls discovery, search providers, Brevo, or a mail-send endpoint, so it
cannot consume Tavily search credits or send email.
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


def get_headers(path, origin=None):
    headers = {"User-Agent": "CrowdfundingDeepSearch-ProductionVerifier/1.0"}
    if origin:
        headers["Origin"] = origin
    request = Request(BASE_URL + path, headers=headers)
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} returned HTTP {response.status}")
        response.read()
        return {name.lower(): value for name, value in response.headers.items()}


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
                brevo = get_json("/api/automation/connectors/brevo/server-readiness")
                if brevo.get("sent") is not False:
                    raise RuntimeError("Brevo readiness endpoint did not explicitly report sent=false.")
                if brevo.get("network_io") is not False:
                    raise RuntimeError("Brevo readiness endpoint unexpectedly reported network I/O.")
                if brevo.get("authorization_granted") is not False:
                    raise RuntimeError("Brevo readiness endpoint unexpectedly reported authorization.")
                if brevo.get("unsubscribe_token_storage_ready") is not True:
                    raise RuntimeError("Stateless unsubscribe token readiness is not active.")
                if not isinstance(brevo.get("durable_unsubscribe_ready"), bool):
                    raise RuntimeError("Durable unsubscribe readiness signal is missing.")
                if not isinstance(brevo.get("provider_suppression_ready"), bool):
                    raise RuntimeError("Provider suppression readiness signal is missing.")
                if brevo.get("provider_suppression_read_only") is not True:
                    raise RuntimeError("Brevo provider suppression is not explicitly read-only.")
                if brevo.get("provider_suppression_write_supported") is not False:
                    raise RuntimeError("Unverified Brevo provider suppression write capability is exposed.")
                security_headers = get_headers("/api/health", origin="https://untrusted.invalid")
                cors_origin = security_headers.get("access-control-allow-origin")
                if cors_origin in {"*", "https://untrusted.invalid"}:
                    raise RuntimeError("Production CORS accepts an untrusted origin.")
                if "max-age=" not in str(security_headers.get("strict-transport-security") or "").lower():
                    raise RuntimeError("Production HSTS header is missing.")
                permissions = str(security_headers.get("permissions-policy") or "").lower()
                if "camera=()" not in permissions or "microphone=()" not in permissions or "geolocation=()" not in permissions:
                    raise RuntimeError("Production Permissions-Policy header is incomplete.")
                print(f"Production verified: {live_commit} is live and healthy.")
                print("Brevo readiness remained non-sending: sent=false, network_io=false, authorization_granted=false.")
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
