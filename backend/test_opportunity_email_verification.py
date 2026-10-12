import sqlite3
import unittest
from opportunity_accounts import initialize as init_accounts, register_unverified, verify_credentials
from opportunity_email_verification import initialize, issue_challenge, consume_challenge


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        init_accounts(self.db)
        initialize(self.db)
        self.owner = register_unverified(self.db, "person@example.org", "secure password for testing 2026")

    def tearDown(self):
        self.db.close()

    def test_one_use(self):
        token = issue_challenge(self.db, self.owner, now=1000)
        self.assertIsNone(verify_credentials(self.db, "person@example.org", "secure password for testing 2026"))
        self.assertTrue(consume_challenge(self.db, self.owner, token, now=1000))
        self.assertFalse(consume_challenge(self.db, self.owner, token, now=1000))
        self.assertEqual(verify_credentials(self.db, "person@example.org", "secure password for testing 2026"), self.owner)

    def test_expiry_and_replacement(self):
        first = issue_challenge(self.db, self.owner, now=1000)
        self.assertFalse(consume_challenge(self.db, self.owner, first, now=2800))
        second = issue_challenge(self.db, self.owner, now=2900)
        self.assertFalse(consume_challenge(self.db, self.owner, first, now=2900))
        self.assertTrue(consume_challenge(self.db, self.owner, second, now=2900))


if __name__ == "__main__":
    unittest.main()
