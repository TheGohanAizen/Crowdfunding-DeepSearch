"""Offline registration coordinator regression tests; no network or email."""
import sqlite3
import unittest

from opportunity_accounts import initialize as initialize_accounts
from opportunity_login_limits import initialize as initialize_limits
from opportunity_registration import attempt_registration
from opportunity_login_limits import bucket_key
import hashlib


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

    def test_identity_budget_is_charged_across_ips(self):
        email = "same@example.org"
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
        identity_bucket = bucket_key("registration-identity:" + digest, "all-client-ips")
        self.assertIsNotNone(self.register(email, ip="192.0.2.1"))
        for i in range(2, 6):
            self.assertIsNone(self.register(email, ip="192.0.2.%d" % i))
        row = self.db.execute(
            "SELECT attempts FROM opportunity_login_limits WHERE bucket=?",
            (identity_bucket,),
        ).fetchone()
        self.assertEqual(row, (5,))
        self.assertIsNone(self.register(email, ip="192.0.2.99"))
        row = self.db.execute(
            "SELECT attempts FROM opportunity_login_limits WHERE bucket=?",
            (identity_bucket,),
        ).fetchone()
        self.assertEqual(row, (5,))

    def test_maximum_length_email_registration(self):
        email = "a" * 64 + "@" + "b" * 186 + ".io"
        self.assertEqual(len(email), 254)
        self.assertIsNotNone(self.register(email))

    def test_malformed_unicode_email_fails_closed(self):
        self.assertIsNone(self.register("\ud800@example.org"))

    def test_registration_budget_resets_after_window(self):
        for i in range(5):
            self.assertIsNotNone(self.register("early%d@example.org" % i, now=1000))
        self.assertIsNone(self.register("later@example.org", now=1899))
        self.assertIsNotNone(self.register("later@example.org", now=1900))

    def test_invalid_client_ip_rejected_before_registration(self):
        for ip in ("", " " * 3, None, "x" * 65):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                self.register("new@example.org", ip=ip)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM opportunity_accounts").fetchone()[0], 0)

    def test_invalid_email_is_charged_to_ip_budget(self):
        for _ in range(5):
            self.assertIsNone(self.register("not-an-email"))
        self.assertIsNone(self.register("valid@example.org"))
        self.assertIsNotNone(self.register("valid@example.org", ip="192.0.2.2"))


if __name__ == "__main__":
    unittest.main()
