"""Offline login coordinator regression tests."""
import sqlite3
import unittest
from opportunity_accounts import initialize as init_accounts, register_unverified
from opportunity_login_limits import initialize as init_limits
from opportunity_login import attempt_login
from opportunity_sessions import verify_session


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        init_accounts(self.db)
        init_limits(self.db)
        self.secret = b"local-only-test-secret-32-bytes-minimum-123"
        self.owner = register_unverified(self.db, "user@example.org", "strong unique password 2026")

    def tearDown(self):
        self.db.close()

    def login(self, password, ip="192.0.2.1", now=1000):
        return attempt_login(self.db, "user@example.org", password, ip, self.secret, now=now)

    def test_verified_only_and_expiration(self):
        self.assertIsNone(self.login("strong unique password 2026"))
        self.db.execute("UPDATE opportunity_accounts SET email_verified=1 WHERE owner_id=?", (self.owner,))
        self.db.commit()
        token = self.login("strong unique password 2026")
        self.assertEqual(verify_session(token, self.secret, now=1000), self.owner)

    def test_throttled_wrong_password(self):
        self.db.execute("UPDATE opportunity_accounts SET email_verified=1 WHERE owner_id=?", (self.owner,))
        self.db.commit()
        for _ in range(5):
            self.assertIsNone(self.login("incorrect password 2026"))
        self.assertIsNone(self.login("strong unique password 2026"))
        self.assertIsNotNone(self.login("strong unique password 2026", now=1900))


if __name__ == "__main__":
    unittest.main()
