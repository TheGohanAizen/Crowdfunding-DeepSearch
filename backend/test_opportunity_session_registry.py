"""Offline session registry tests."""
import sqlite3
import unittest
from opportunity_sessions import issue_session
from opportunity_session_registry import (
    initialize, register_session, require_active_session,
    revoke_session, revoke_owner_sessions,
)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        initialize(self.db)
        self.secret = b"local-only-test-secret-32-bytes-minimum-123"

    def tearDown(self):
        self.db.close()

    def test_registered_and_revoked(self):
        first = issue_session("owner-A", self.secret, now=1000)
        second = issue_session("owner-A", self.secret, now=1000)
        with self.assertRaises(PermissionError):
            require_active_session(self.db, first, self.secret, now=1000)
        self.assertEqual(register_session(self.db, first, self.secret, now=1000), "owner-A")
        register_session(self.db, second, self.secret, now=1000)
        self.assertEqual(require_active_session(self.db, first, self.secret, now=1000), "owner-A")
        self.assertTrue(revoke_session(self.db, first))
        with self.assertRaises(PermissionError):
            require_active_session(self.db, first, self.secret, now=1000)
        self.assertEqual(require_active_session(self.db, second, self.secret, now=1000), "owner-A")
        self.assertEqual(revoke_owner_sessions(self.db, "owner-A"), 1)
        with self.assertRaises(PermissionError):
            require_active_session(self.db, second, self.secret, now=1000)

    def test_malformed_unicode_token_is_rejected(self):
        with self.assertRaises(ValueError):
            revoke_session(self.db, "\ud800" * 30)

    def test_revoke_owner_rejects_invalid_identifiers(self):
        for owner in ("", " " * 4, "x" * 201, "\ud800", None):
            with self.subTest(owner=repr(owner)[:24]), self.assertRaises(ValueError):
                revoke_owner_sessions(self.db, owner)

    def test_expiration_and_invalid_signature(self):
        token = issue_session("owner-A", self.secret, now=1000)
        register_session(self.db, token, self.secret, now=1000)
        with self.assertRaises(ValueError):
            require_active_session(self.db, token, self.secret, now=44201)
        with self.assertRaises(ValueError):
            register_session(self.db, "forged", self.secret, now=1000)


if __name__ == "__main__":
    unittest.main()
