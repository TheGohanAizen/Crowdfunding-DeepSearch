"""Isolated, offline-only opportunity repository prototype.

Not wired to Flask routes or production Turso. The caller MUST supply an
owner ID derived from independently verified authentication, never request JSON.
"""
import uuid
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
import ipaddress

VALID_STATUSES = frozenset({
    "needs_review", "researching", "submission_route_found",
    "not_suitable", "manually_contacted",
})
MAX_RECORDS = 250


def normalize_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid URL")
    # URL parsers and browsers may silently remove control characters or
    # reinterpret backslashes. Reject ambiguous source links before parsing.
    if any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in value):
        raise ValueError("invalid URL")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("invalid URL") from exc
    value = value.strip()
    # Literal whitespace within a URL is browser-dependent. Require callers
    # to percent-encode spaces and other whitespace in paths and queries.
    if any(char.isspace() for char in value):
        raise ValueError("invalid URL")
    # urllib.parse accepts malformed percent escapes, but clients may
    # reinterpret them. Only complete hexadecimal escapes are allowed.
    for index, char in enumerate(value):
        if char == "%" and (index + 2 >= len(value) or
                            any(digit not in "0123456789abcdefABCDEF"
                                for digit in value[index + 1:index + 3])):
            raise ValueError("invalid URL escape")
    # Reject all encoded ASCII controls (including CR, LF, NUL and DEL).
    # Downstream URL consumers must not be able to reinterpret these bytes.
    for index, char in enumerate(value):
        if char == "%":
            decoded_byte = int(value[index + 1:index + 3], 16)
            if decoded_byte < 32 or decoded_byte == 127:
                raise ValueError("invalid URL control escape")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise ValueError("invalid URL") from exc
    if parts.scheme.lower() != "https" or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError("HTTPS source URL required")
    try:
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except (UnicodeError, AttributeError) as exc:
        raise ValueError("invalid hostname") from exc
    if len(host) > 253 or any(c.isspace() or ord(c) < 33 for c in host):
        raise ValueError("invalid hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if len(labels) < 2 or any(
            not label or len(label) > 63 or label.startswith("-")
            or label.endswith("-") or not all(c.isascii() and (c.isalnum() or c == "-") for c in label)
            for label in labels
        ) or not any(c.isalpha() for c in labels[-1]):
            raise ValueError("public DNS hostname required")
    else:
        raise ValueError("IP addresses are not supported")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid port") from exc
    if port is not None and port != 443:
        raise ValueError("nonstandard port not supported")
    # Explicit default ports are accepted, but canonicalize to the same
    # source URL so an opportunity cannot be saved twice under :443.
    if parts.netloc.endswith(":"):
        raise ValueError("invalid port")
    netloc = host
    path = parts.path.rstrip("/") or "/"
    # Hex digits in percent escapes are case-insensitive; canonicalize them
    # without decoding reserved characters or changing URL semantics.
    path = re.sub(r"%[0-9A-Fa-f]{2}", lambda match: match.group().upper(), path)
    query = re.sub(r"%[0-9A-Fa-f]{2}", lambda match: match.group().upper(), parts.query)
    normalized = urlunsplit(("https", netloc, path, query, ""))
    # Normalization must not introduce a stored value beyond the URL limit.
    if len(normalized) > 2048 or len(normalized.encode("utf-8")) > 2048:
        raise ValueError("invalid URL")
    return normalized


def initialize(connection):
    """Create the offline schema atomically without committing caller work."""
    if connection.in_transaction:
        raise ValueError("opportunity initialization requires a transaction-free connection")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS opportunity_records (
            owner_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            name TEXT NOT NULL,
            lead_type TEXT NOT NULL,
            review_status TEXT NOT NULL,
            campaign_label TEXT NOT NULL,
            notes TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_id, record_id),
            UNIQUE (owner_id, normalized_url)
        )""")
        connection.execute("""CREATE INDEX IF NOT EXISTS idx_opportunity_owner_updated
            ON opportunity_records(owner_id, updated_at)""")
        connection.commit()
    except Exception:
        connection.rollback()
        raise

def require_owner(owner_id):
    if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
        raise ValueError("verified owner required")
    try:
        owner_id.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("verified owner required") from exc
    return owner_id


def require_record_id(record_id):
    if not isinstance(record_id, str) or len(record_id) != 32 or any(c not in "0123456789abcdef" for c in record_id):
        raise ValueError("invalid record ID")
    return record_id


def _text(item, key, limit, default=""):
    value = item.get(key, default)
    if not isinstance(value, str):
        raise ValueError("invalid " + key)
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("invalid " + key) from exc
    return value[:limit]


def add(connection, owner_id, item):
    """Merge-only insert. Caller must authenticate owner_id first."""
    owner_id = require_owner(owner_id)
    if not isinstance(item, dict):
        raise ValueError("invalid opportunity")
    url = normalize_url(item.get("url"))
    name = _text(item, "name", 250, "Imported opportunity")
    lead_type = _text(item, "lead_type", 100, "unverified")
    status = item.get("status", "needs_review")
    if not isinstance(status, str) or status not in VALID_STATUSES:
        raise ValueError("invalid status")
    campaign = _text(item, "campaign_label", 100)
    notes = _text(item, "notes", 1500)
    now = datetime.now(timezone.utc).isoformat()
    # This function owns its transaction. Never commit or roll back unrelated
    # changes made by a caller on the same connection.
    if connection.in_transaction:
        raise ValueError("opportunity add requires a transaction-free connection")
    # Acquire the write lock before checking quota and duplicates so two
    # concurrent writers cannot both pass the 250-record limit.
    connection.execute("BEGIN IMMEDIATE")
    try:
        existing = connection.execute(
            "SELECT record_id FROM opportunity_records WHERE owner_id=? AND normalized_url=?",
            (owner_id, url),
        ).fetchone()
        if existing:
            connection.commit()
            return {"created": False, "record_id": existing[0]}
        count = connection.execute(
            "SELECT COUNT(*) FROM opportunity_records WHERE owner_id=?", (owner_id,)
        ).fetchone()[0]
        if count >= MAX_RECORDS:
            raise ValueError("opportunity quota exceeded")
        record_id = uuid.uuid4().hex
        connection.execute(
            """INSERT INTO opportunity_records
            (owner_id,record_id,source_url,normalized_url,name,lead_type,
             review_status,campaign_label,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (owner_id,record_id,url,url,name,lead_type,status,campaign,notes,now,now),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"created": True, "record_id": record_id}


def list_for_owner(connection, owner_id, limit=100):
    owner_id = require_owner(owner_id)
    if type(limit) is not int or not 1 <= limit <= MAX_RECORDS:
        raise ValueError("invalid limit")
    rows = connection.execute(
        """SELECT record_id,source_url,name,lead_type,review_status,
                  campaign_label,notes,updated_at
           FROM opportunity_records WHERE owner_id=?
           ORDER BY updated_at DESC,record_id LIMIT ?""",
        (owner_id, limit),
    ).fetchall()
    keys = ("record_id","url","name","lead_type","status","campaign_label","notes","updated_at")
    return [dict(zip(keys, row)) for row in rows]


def update_for_owner(connection, owner_id, record_id, changes):
    owner_id = require_owner(owner_id)
    record_id = require_record_id(record_id)
    if not isinstance(changes, dict) or not changes or set(changes) - {"status","campaign_label","notes"}:
        raise ValueError("invalid update")
    columns = {"status":"review_status","campaign_label":"campaign_label","notes":"notes"}
    limits = {"campaign_label":100,"notes":1500}
    updates = {}
    for key, value in changes.items():
        if key == "status":
            if not isinstance(value, str) or value not in VALID_STATUSES:
                raise ValueError("invalid status")
        else:
            if not isinstance(value,str) or len(value)>limits[key]:
                raise ValueError("invalid "+key)
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("invalid "+key) from exc
        updates[columns[key]] = value
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    sql = "UPDATE opportunity_records SET "+", ".join(k+"=?" for k in updates)+" WHERE owner_id=? AND record_id=?"
    if connection.in_transaction:
        raise ValueError("opportunity update requires a transaction-free connection")
    with connection:
        result = connection.execute(sql, (*updates.values(),owner_id,record_id))
    return result.rowcount == 1


def delete_for_owner(connection, owner_id, record_id):
    owner_id = require_owner(owner_id)
    record_id = require_record_id(record_id)
    if connection.in_transaction:
        raise ValueError("opportunity delete requires a transaction-free connection")
    with connection:
        result = connection.execute(
            "DELETE FROM opportunity_records WHERE owner_id=? AND record_id=?",
            (owner_id,record_id),
        )
    return result.rowcount == 1
