"""Offline registration coordinator; NOT exposed as an HTTP endpoint.

Trusted callers must supply the actual client address, use TLS, enforce request
body limits, and deliver email verification through a separately audited flow.
A None result is deliberately ambiguous: invalid input, existing account,
or rate limit. Never return an owner ID to an untrusted public client.
"""
import hashlib

from opportunity_accounts import normalize_email, register_unverified
from opportunity_login_limits import bucket_key, record_attempts


def attempt_registration(connection, email, password, client_ip, now=None):
    """Reserve per-IP and per-identity registration budgets before hashing."""
    try:
        identity = normalize_email(email)
    except ValueError:
        identity = "invalid-email"
    # Hash the identity before adding a namespace so even maximum-length
    # valid email addresses fit the bounded bucket-key input.
    try:
        identity_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    except UnicodeError:
        identity = "invalid-email"
        identity_digest = hashlib.sha256(identity.encode("ascii")).hexdigest()
    # Separate registration namespace from login to avoid sharing budgets.
    buckets = (
        bucket_key("registration-ip", client_ip),
        bucket_key("registration-identity:" + identity_digest, "all-client-ips"),
        bucket_key("registration-pair:" + identity_digest, client_ip),
    )
    if not record_attempts(connection, buckets, now=now):
        return None
    try:
        return register_unverified(connection, email, password)
    except ValueError:
        return None
