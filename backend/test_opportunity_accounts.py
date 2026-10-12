"""Offline account storage tests; never touch production accounts."""
import sqlite3
import unittest

from opportunity_accounts import (
    initialize, normalize_email, register_unverified, verify_credentials,
)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        initialize(self.db)

    def tearDown(self):
        self.db.close()

    def test_registration_does_not_enable_login(self):
        owner = register_unverified(self.db, "  USER@Example.ORG  ", "strong unique password 2026")
        self.assertEqual(len(owner), 32)
        self.assertIsNone(verify_credentials(self.db, "user@example.org", "strong unique password 2026"))
        row = self.db.execute(
            "SELECT email,email_verified,password_hash FROM opportunity_accounts WHERE owner_id=?",
            (owner,),
        ).fetchone()
        self.assertEqual(row[0], "user@example.org")
        self.assertEqual(row[1], 0)
        self.assertNotIn("strong unique password 2026", row[2])
        with self.assertRaises(ValueError):
            register_unverified(self.db, "user@example.org", "another strong password 2026")

    def test_verified_login_and_disabled_account(self):
        owner = register_unverified(self.db, "person@example.org", "strong unique password 2026")
        # Test fixture simulates a future verified-email workflow; no public verification endpoint.
        self.db.execute("UPDATE opportunity_accounts SET email_verified=1 WHERE owner_id=?", (owner,))
        self.db.commit()
        self.assertEqual(verify_credentials(self.db, "PERSON@example.org", "strong unique password 2026"), owner)
        self.assertIsNone(verify_credentials(self.db, "person@example.org", "wrong password 2026"))
        self.db.execute("UPDATE opportunity_accounts SET disabled_at='2026-10-11' WHERE owner_id=?", (owner,))
        self.db.commit()
        self.assertIsNone(verify_credentials(self.db, "person@example.org", "strong unique password 2026"))

    def test_bad_email(self):
        for email in ("", "missing-at-sign", "@example.org", "person@localhost", None):
            with self.subTest(email=email), self.assertRaises(ValueError):
                normalize_email(email)
        self.assertIsNone(verify_credentials(self.db, "missing-at-sign", "strong unique password 2026"))


if __name__ == "__main__":
    unittest.main()
