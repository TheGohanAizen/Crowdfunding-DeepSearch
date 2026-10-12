"""Offline password verification primitives, not exposed by public routes.

Registration, login rate limits, recovery, email verification and persistent
account storage must be completed before enabling sign-in.
"""
import hashlib
import hmac
import secrets

ALGORITHM = "scrypt"
N = 2 ** 14
R = 8
P = 1
KEY_BYTES = 32
SALT_BYTES = 16


def _password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 1024:
        raise ValueError("password must be 12 to 1024 characters")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("invalid password encoding") from exc
    if len(encoded) > 4096:
        raise ValueError("password too long")
    return encoded


def hash_password(password):
    encoded = _password(password)
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(encoded, salt=salt, n=N, r=R, p=P, dklen=KEY_BYTES)
    return "$".join((ALGORITHM, str(N), str(R), str(P), salt.hex(), digest.hex()))


def verify_password(password, stored):
    """Fail closed for malformed stored hashes; return only True or False."""
    if not isinstance(stored, str) or len(stored) > 256:
        return False
    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algorithm != ALGORITHM or (n, r, p) != (str(N), str(R), str(P)):
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if len(salt) != SALT_BYTES or len(expected) != KEY_BYTES:
            return False
        encoded = _password(password)
        candidate = hashlib.scrypt(encoded, salt=salt, n=N, r=R, p=P, dklen=KEY_BYTES)
        return hmac.compare_digest(candidate, expected)
    except (ValueError, TypeError, UnicodeError):
        return False
