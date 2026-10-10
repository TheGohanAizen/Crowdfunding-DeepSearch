#!/usr/bin/env python3
"""Exercise the actual Audience discovery API without paid search or sending."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


def main():
    original = server.retrieve_audience_candidates
    try:
        server.retrieve_audience_candidates = lambda *args, **kwargs: {
            "configured": False,
            "provider": "offline-test",
            "message": "No network requests made.",
            "candidates": [],
        }
        app = server.create_app()
        client = app.test_client()
        response = client.post("/api/audience/discover", json={
            "need": "Transportation",
            "location": "Austin, Texas",
        })
        assert response.status_code == 200, (response.status_code, response.get_json())
        payload = response.get_json()
        assert payload["audience"]["automatic_distribution"] is False
        assert payload["results"] == []
        print("PASS: audience discovery API responds without provider calls or sending.")
        print("NOT VERIFIED: provider-backed results or browser UI rendering.")
        return 0
    finally:
        server.retrieve_audience_candidates = original


if __name__ == "__main__":
    sys.exit(main())
