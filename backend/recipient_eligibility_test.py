#!/usr/bin/env python3
"""Non-sending eligibility regression tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from recipient_eligibility import assess_recipient


def main():
    complete = {
        "email": "contact@example.org",
        "source_url": "https://example.org/contact",
        "relevance_evidence": "Accepts community funding inquiries",
        "contact_permission_evidence": "Explicit published submission instructions",
        "recipient_type": "organization",
        "suppressed": False,
        "unsubscribe_verified": True,
        "human_approved": True,
    }
    cases = [
        ({}, "recipient_email_missing_or_invalid"),
        ({**complete, "email": "invalid"}, "recipient_email_missing_or_invalid"),
        ({**complete, "source_url": "http://example.org"}, "verifiable_public_source_required"),
        ({**complete, "relevance_evidence": ""}, "campaign_relevance_evidence_required"),
        ({**complete, "contact_permission_evidence": ""}, "contact_permission_evidence_required"),
        ({**complete, "suppressed": True}, "suppression_clearance_unverified"),
        ({**complete, "unsubscribe_verified": False}, "unsubscribe_compliance_unverified"),
        ({**complete, "human_approved": False}, "human_review_required"),
        (complete, "trusted_authorization_not_implemented"),
    ]
    for data, reason in cases:
        result = assess_recipient(data)
        assert reason in result["blockers"], (reason, result)
        assert result["send_allowed"] is False
        assert result["transport_invoked"] is False
    assert assess_recipient(complete)["review_ready"] is True
    print("PASS: nine recipient eligibility cases fail closed; no sending authorized.")
    print("NOT VERIFIED: trusted evidence verification or integration with transport.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
