"""Offline login coordinator; never exposed as an HTTP route.

Requires server-side trusted client address, persistent throttling, and
email-verified accounts. Error details must not reveal account existence.
"""
import ipaddress

from opportunity_accounts import normalize_email, verify_credentials
from opportunity_login_limits import bucket_key, record_attempts
from opportunity_sessions import issue_session
from opportunity_session_registry import register_session


def attempt_login(connection, email, password, client_ip, session_secret, now=None):
    """Return session token or None; no unverified accounts may log in."""
    try:
        identity = normalize_email(email)
    except ValueError:
        # Invalid identities must not bypass the IP's login attempt budget.
        identity = "invalid-email"
    # Normalize the server-trusted IP so equivalent IPv6 spellings share a budget.
    try:
        if not isinstance(client_ip, str):
            raise ValueError("invalid client address")
        client_ip = str(ipaddress.ip_address(client_ip.strip()))
    except ValueError as exc:
        raise ValueError("invalid client address") from exc
    # Three independent budgets: identity/IP, whole IP, and identity across IPs.
    # The last bucket prevents rotating source addresses to evade account limits.
    buckets = (bucket_key(identity, client_ip),
               bucket_key("all-ip-attempts", client_ip),
               bucket_key(identity, "all-client-ips"))
    if not record_attempts(connection, buckets, now=now):
        return None
    owner_id = verify_credentials(connection, identity, password)
    if owner_id is None:
        return None
    token = issue_session(owner_id, session_secret, now=now)
    register_session(connection, token, session_secret, now=now)
    return token
