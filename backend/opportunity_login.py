"""Offline login coordinator; never exposed as an HTTP route.

Requires server-side trusted client address, persistent throttling, and
email-verified accounts. Error details must not reveal account existence.
"""
from opportunity_accounts import normalize_email, verify_credentials
from opportunity_login_limits import bucket_key, record_attempts
from opportunity_sessions import issue_session


def attempt_login(connection, email, password, client_ip, session_secret, now=None):
    """Return session token or None; no unverified accounts may log in."""
    try:
        identity = normalize_email(email)
    except ValueError:
        # Invalid identities must not bypass the IP's login attempt budget.
        identity = "invalid-email"
    buckets = (bucket_key(identity, client_ip), bucket_key('all-ip-attempts', client_ip))
    if not record_attempts(connection, buckets, now=now):
        return None
    owner_id = verify_credentials(connection, identity, password)
    if owner_id is None:
        return None
    return issue_session(owner_id, session_secret, now=now)
