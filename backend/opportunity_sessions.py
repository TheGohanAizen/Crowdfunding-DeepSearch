"""Offline session-token primitives; NOT connected to public Flask routes.

The secret must come from a protected deployment environment, never from
client input. Production integration must also enforce CSRF and login limits.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

MAX_AGE_SECONDS = 12 * 60 * 60
CLOCK_SKEW_SECONDS = 60


def _secret(value):
    if not isinstance(value, (bytes, bytearray)) or len(value) < 32:
        raise ValueError("session secret must contain at least 32 bytes")
    return bytes(value)


def _encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("invalid session token")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except Exception as exc:
        raise ValueError("invalid session token") from exc


def issue_session(owner_id, secret, now=None):
    if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
        raise ValueError("verified owner required")
    key = _secret(secret)
    issued = int(time.time() if now is None else now)
    payload = json.dumps({"v": 1, "sub": owner_id, "iat": issued,
                          "nonce": secrets.token_hex(16)}, separators=(",", ":"), sort_keys=True).encode()
    encoded = _encode(payload)
    signature = _encode(hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest())
    return encoded + "." + signature


def verify_session(token, secret, now=None, max_age=MAX_AGE_SECONDS):
    key = _secret(secret)
    if not isinstance(token, str) or len(token) > 4096 or token.count(".") != 1:
        raise ValueError("invalid session token")
    payload_part, signature_part = token.split(".")
    try:
        payload_bytes = payload_part.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid session token") from exc
    expected = hmac.new(key, payload_bytes, hashlib.sha256).digest()
    supplied = _decode(signature_part)
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("invalid session signature")
    try:
        payload = json.loads(_decode(payload_part))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid session payload") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("unsupported session")
    owner = payload.get("sub")
    issued = payload.get("iat")
    nonce = payload.get("nonce")
    if (not isinstance(owner, str) or not owner.strip() or len(owner) > 200
            or type(issued) is not int or not isinstance(nonce, str) or len(nonce) != 32
            or type(max_age) is not int or not 1 <= max_age <= MAX_AGE_SECONDS):
        raise ValueError("invalid session claims")
    current = int(time.time() if now is None else now)
    if issued > current + CLOCK_SKEW_SECONDS or current - issued > max_age:
        raise ValueError("session expired or not yet valid")
    return owner


def issue_csrf(session_token, secret):
    """Issue a session-bound CSRF token for a separately verified session.

    Callers MUST validate the session before issuing a CSRF token.
    """
    _secret(secret)
    if not isinstance(session_token, str) or not session_token:
        raise ValueError("session required")
    nonce = secrets.token_hex(16)
    signed = ("csrf-v1:" + session_token + ":" + nonce).encode("utf-8")
    signature = _encode(hmac.new(_secret(secret), signed, hashlib.sha256).digest())
    return nonce + "." + signature


def verify_csrf(session_token, csrf_token, secret):
    """Validate a CSRF token against the current session cookie value."""
    key = _secret(secret)
    if not isinstance(session_token, str) or not session_token:
        return False
    if not isinstance(csrf_token, str) or csrf_token.count(".") != 1 or len(csrf_token) > 256:
        return False
    nonce, signature = csrf_token.split(".")
    if len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        return False
    try:
        supplied = _decode(signature)
    except ValueError:
        return False
    try:
        signed = ("csrf-v1:" + session_token + ":" + nonce).encode("utf-8")
    except UnicodeError:
        return False
    expected = hmac.new(key, signed, hashlib.sha256).digest()
    return hmac.compare_digest(expected, supplied)
