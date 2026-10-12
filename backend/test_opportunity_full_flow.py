"""Offline account-to-opportunity integration tests; no production services."""
import sqlite3
import unittest

from opportunity_accounts import initialize as init_accounts, register_unverified, disable_account
from opportunity_email_verification import initialize as init_email, issue_challenge, consume_challenge
from opportunity_login_limits import initialize as init_limits
from opportunity_session_registry import initialize as init_sessions, revoke_session
from opportunity_store import initialize as init_opportunities
from opportunity_login import attempt_login
from opportunity_sessions import issue_csrf
from opportunity_access import list_opportunities, create_opportunity, update_opportunity, delete_opportunity


class FullAccountFlowTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        for initializer in (init_accounts, init_email, init_limits, init_sessions, init_opportunities):
            initializer(self.db)
        self.secret = b"offline-only-integration-test-secret-2026"

    def tearDown(self):
        self.db.close()

    def test_disabling_account_revokes_session_without_affecting_others(self):
        password = "strong unique password for integration"
        first = register_unverified(self.db, "first@example.org", password)
        second = register_unverified(self.db, "second@example.org", password)
        for owner in (first, second):
            token = issue_challenge(self.db, owner, now=1000)
            self.assertTrue(consume_challenge(self.db, owner, token, now=1000))
        first_session = attempt_login(self.db, "first@example.org", password, "192.0.2.10", self.secret, now=1000)
        second_session = attempt_login(self.db, "second@example.org", password, "192.0.2.11", self.secret, now=1000)
        self.assertTrue(disable_account(self.db, first))
        self.assertFalse(disable_account(self.db, first))
        with self.assertRaises(PermissionError):
            list_opportunities(self.db, first_session, self.secret, now=1000)
        self.assertEqual(list_opportunities(self.db, second_session, self.secret, now=1000), [])
        self.assertIsNone(attempt_login(self.db, "first@example.org", password, "192.0.2.12", self.secret, now=1000))

    def test_verified_login_storage_isolation_and_logout(self):
        password = "strong unique password for integration"
        alice = register_unverified(self.db, "alice@example.org", password)
        bob = register_unverified(self.db, "bob@example.org", password)
        self.assertIsNone(attempt_login(self.db, "alice@example.org", password, "192.0.2.1", self.secret, now=1000))
        for owner in (alice, bob):
            token = issue_challenge(self.db, owner, now=1000)
            self.assertTrue(consume_challenge(self.db, owner, token, now=1000))
            self.assertFalse(consume_challenge(self.db, owner, token, now=1000))
        session_a = attempt_login(self.db, "alice@example.org", password, "192.0.2.1", self.secret, now=1000)
        session_b = attempt_login(self.db, "bob@example.org", password, "192.0.2.2", self.secret, now=1000)
        self.assertIsInstance(session_a, str)
        self.assertIsInstance(session_b, str)
        csrf_a = issue_csrf(session_a, self.secret)
        csrf_b = issue_csrf(session_b, self.secret)
        record = create_opportunity(self.db, session_a, csrf_a, self.secret,
                                    {"url": "https://example.org/fundraising", "notes": "Alice only"}, now=1000)
        self.assertEqual(len(list_opportunities(self.db, session_a, self.secret, now=1000)), 1)
        self.assertEqual(list_opportunities(self.db, session_b, self.secret, now=1000), [])
        self.assertFalse(update_opportunity(self.db, session_b, csrf_b, self.secret,
                                            record["record_id"], {"notes": "tampered"}, now=1000))
        with self.assertRaises(PermissionError):
            delete_opportunity(self.db, session_a, csrf_b, self.secret, record["record_id"], now=1000)
        self.assertTrue(revoke_session(self.db, session_a))
        with self.assertRaises(PermissionError):
            list_opportunities(self.db, session_a, self.secret, now=1000)
        self.assertEqual(list_opportunities(self.db, session_b, self.secret, now=1000), [])


if __name__ == "__main__":
    unittest.main()
