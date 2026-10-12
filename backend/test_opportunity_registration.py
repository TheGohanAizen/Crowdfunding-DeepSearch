"""Offline registration coordinator regression tests; no network or email."""
import sqlite3
import unittest

from opportunity_accounts import initialize as initialize_accounts
from opportunity_login_limits import initialize as initialize_limits
from opportunity_registration import attempt_registration


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        initialize_accounts(self.db)
        initialize_limits(self.db)

    def tearDown(self):
        self.db.close()

    def register(self, email, ip="192.0.2.1", now=1000):
        return attempt_registration(
            self.db, email, "strong unique password 2026", ip, now=now
        )

    def test_creates_unverified_account_and_rejects_duplicate(self):
        owner = self.register("NEW@example.org")
        self.assertIsInstance(owner, str)
        self.assertIsNone(self.register("new@example.org"))
        row = self.db.execute(
            "SELECT email,email_verified FROM opportunity_accounts WHERE owner_id=?",
            (owner,),
        ).fetchone()
        self.assertEqual(row, ("new@example.org", 0))

    def test_rotating_identity_cannot_bypass_ip_budget(self):
        for i in range(5):
            self.assertIsNotNone(self.register("new%d@example.org" % i))
        self.assertIsNone(self.register("sixth@example.org"))
        self.assertIsNotNone(self.register("sixth@example.org", ip="192.0.2.2"))

    def test_rotating_ip_cannot_bypass_identity_budget(self):
        self.assertIsNotNone(self.register("same@example.org", ip="192.0.2.1"))
        for i in range(2, 6):
            self.assertIsNone(self.register("same@example.org", ip="192.0.2.%d" % i))
        self.assertIsNone(self.register("same@example.org", ip="192.0.2.99"))
        self.assertIsNone(self.register("same@example.org", ip="192.0.2.99", now=1900))

    def test_invalid_email_is_charged_to_ip_budget(self):
        for _ in range(5):
            self.assertIsNone(self.register("not-an-email"))
        self.assertIsNone(self.register("valid@example.org"))
        self.assertIsNotNone(self.register("valid@example.org", ip="192.0.2.2"))


if __name__ == "__main__":
    unittest.main()
