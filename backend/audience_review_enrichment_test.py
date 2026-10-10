#!/usr/bin/env python3
"""Offline actual discovery-result enrichment regression."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


def main():
    leads = [
        {"name": "Example community directory", "url": "https://example.org",
         "channel_rules": {"status": "not_checked"}},
        {"name": "Example restricted forum", "url": "https://example.net",
         "channel_rules": {"status": "restriction_detected"}},
    ]
    results = server.finalize_audience_actions(leads)
    assert len(results) == 2
    for result in results:
        review = result["recipient_review"]
        assert review["send_allowed"] is False
        assert review["transport_invoked"] is False
        assert "contact_permission_evidence_required" in review["blockers"]
        assert result["outreach_readiness"]["safe_for_automatic_distribution"] is False
        assert result["outreach_readiness"]["requires_user_review"] is True
    assert results[1]["recommended_next_action"]["blocked_by_rules"] is True
    print("PASS: discovered audience results carry fail-closed recipient review reports.")
    print("NOT VERIFIED: live discovery provider output or source authenticity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
