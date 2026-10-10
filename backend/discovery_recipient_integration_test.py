#!/usr/bin/env python3
"""Test that discovery distribution decisions incorporate recipient review."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


def main():
    original = server.automation_connector_status
    original_supported = server.SUPPORTED_AUTOMATION_MECHANISMS
    try:
        server.automation_connector_status = lambda _: {
            "configured": True, "send_enabled": True,
        }
        server.SUPPORTED_AUTOMATION_MECHANISMS = {"brevo_email_v3"}
        candidate = {
            "email": "contact@example.org",
            "source_url": "https://example.org/contact",
            "relevance_evidence": "Campaign support published",
            "contact_permission_evidence": "Public submission instructions",
            "recipient_type": "organization",
            "suppressed": False,
            "unsubscribe_verified": True,
            "human_approved": True,
            "channel_rules": {"automation_eligibility": {
                "supported_mechanism": "brevo_email_v3",
                "eligible": True,
                "send_enabled": True,
            }},
        }
        result = server.automation_distribution_decision(candidate)
        assert result["allowed"] is False, result
        assert result["recipient_review"]["review_ready"] is True, result
        assert "trusted_authorization_not_implemented" in result["blockers"], result
        missing = server.automation_distribution_decision({
            "channel_rules": candidate["channel_rules"],
        })
        assert missing["allowed"] is False
        assert "recipient_email_missing_or_invalid" in missing["blockers"]
        print("PASS: discovery decisions include recipient review and never authorize sending.")
        print("NOT VERIFIED: real-world source authenticity or transport integration.")
        return 0
    finally:
        server.automation_connector_status = original
        server.SUPPORTED_AUTOMATION_MECHANISMS = original_supported


if __name__ == "__main__":
    sys.exit(main())
