#!/usr/bin/env python3
"""Offline HTTP contract test with a realistic audience search result.

Provider retrieval and source-page inspection are substituted so no external
requests, provider credits, or outreach can occur.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


def main():
    original_retrieve = server.retrieve_audience_candidates
    original_rules = server.enrich_audience_with_rule_checks
    try:
        lead = server.normalize_audience_candidate(
            {"title": "Austin Community Transportation Support",
             "link": "https://example.org/transportation",
             "snippet": "Austin community transportation assistance resources"},
            "Community Forums",
            "transportation assistance Austin",
            source="offline-fixture",
        )
        server.retrieve_audience_candidates = lambda *args, **kwargs: {
            "configured": False,
            "provider": "offline-fixture",
            "message": "Fixture, no network access.",
            "candidates": [lead],
        }
        server.enrich_audience_with_rule_checks = lambda items: [
            {**item, "channel_rules": {
                "status": "not_checked",
                "permission_verified": False,
                "automatic_distribution": False,
                "automation_eligibility": {
                    "eligible": False, "send_enabled": False,
                    "supported_mechanism": None,
                },
            }} for item in items
        ]
        client = server.create_app().test_client()
        response = client.post("/api/audience/discover", json={
            "need": "Transportation", "location": "Austin, Texas"
        })
        assert response.status_code == 200, (response.status_code, response.get_json())
        body = response.get_json()
        assert len(body["results"]) == 1, body
        result = body["results"][0]
        review = result["recipient_review"]
        assert review["send_allowed"] is False
        assert review["transport_invoked"] is False
        assert "contact_permission_evidence_required" in review["blockers"]
        assert result["outreach_readiness"]["safe_for_automatic_distribution"] is False
        assert body["audience"]["automatic_distribution"] is False
        print("PASS: realistic audience lead carries recipient review through HTTP response.")
        print("PASS: no provider, source-page, or outreach network requests were made.")
        print("NOT VERIFIED: live provider data or browser UI.")
        return 0
    finally:
        server.retrieve_audience_candidates = original_retrieve
        server.enrich_audience_with_rule_checks = original_rules


if __name__ == "__main__":
    sys.exit(main())
