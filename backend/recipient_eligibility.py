"""Conservative, non-sending recipient eligibility assessment.

Candidate-supplied flags are untrusted. This produces review requirements,
never permission to send; authorization needs separate trusted verification.
"""


def assess_recipient(candidate):
    candidate = candidate if isinstance(candidate, dict) else {}
    blockers = []
    address = str(candidate.get("email") or "").strip()
    if not address or "@" not in address or any(c.isspace() for c in address):
        blockers.append("recipient_email_missing_or_invalid")
    if candidate.get("source_url") is None or not str(candidate.get("source_url")).startswith("https://"):
        blockers.append("verifiable_public_source_required")
    if candidate.get("relevance_evidence") is None or not str(candidate.get("relevance_evidence")).strip():
        blockers.append("campaign_relevance_evidence_required")
    if candidate.get("contact_permission_evidence") is None or not str(candidate.get("contact_permission_evidence")).strip():
        blockers.append("contact_permission_evidence_required")
    if candidate.get("recipient_type") not in {"organization", "business", "individual"}:
        blockers.append("recipient_type_review_required")
    if candidate.get("suppressed") is not False:
        blockers.append("suppression_clearance_unverified")
    if candidate.get("unsubscribe_verified") is not True:
        blockers.append("unsubscribe_compliance_unverified")
    if candidate.get("human_approved") is not True:
        blockers.append("human_review_required")
    # Even complete self-reported metadata is not a trusted authorization.
    blockers.append("trusted_authorization_not_implemented")
    return {
        "review_ready": len(blockers) == 1,
        "send_allowed": False,
        "transport_invoked": False,
        "blockers": blockers,
        "reason": blockers[0],
    }
