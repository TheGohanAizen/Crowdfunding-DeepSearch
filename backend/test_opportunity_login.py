"""Offline login coordinator regression tests."""
import sqlite3
import unittest
from opportunity_accounts import initialize as init_accounts, register_unverified
from opportunity_login_limits import initialize as init_limits
from opportunity_login import attempt_login
from opportunity_sessions import verify_session
from opportunity_session_registry import initialize as initialize_sessions, require_active_session, revoke_session


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        init_accounts(self.db)
        init_limits(self.db)
        initialize_sessions(self.db)
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
        self.assertEqual(require_active_session(self.db, token, self.secret, now=1000), self.owner)
        self.assertTrue(revoke_session(self.db, token))
        with self.assertRaises(PermissionError):
            require_active_session(self.db, token, self.secret, now=1000)

    def test_equivalent_ipv6_spellings_share_ip_budget(self):
        for i in range(5):
            self.assertIsNone(attempt_login(
                self.db, "unknown%d@example.org" % i, "wrong password 2026",
                ("2001:db8::1" if i % 2 else "2001:0DB8:0:0:0:0:0:1"),
                self.secret, now=1000,
            ))
        self.db.execute("UPDATE opportunity_accounts SET email_verified=1 WHERE owner_id=?", (self.owner,))
        self.db.commit()
        self.assertIsNone(self.login("strong unique password 2026", ip="2001:db8::1"))
        self.assertIsNotNone(self.login("strong unique password 2026", ip="2001:db8::2"))

    def test_invalid_client_ip_is_rejected(self):
        for ip in ("not-an-ip", "", None, "192.0.2.1, 198.51.100.2"):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                self.login("strong unique password 2026", ip=ip)

    def test_throttled_wrong_password(self):
        self.db.execute("UPDATE opportunity_accounts SET email_verified=1 WHERE owner_id=?", (self.owner,))
        self.db.commit()
        for _ in range(5):
            self.assertIsNone(self.login("incorrect password 2026"))
        self.assertIsNone(self.login("strong unique password 2026"))
        self.assertIsNotNone(self.login("strong unique password 2026", now=1900))


    def test_rotating_ip_cannot_bypass_account_limit(self):
        self.db.execute("UPDATE opportunity_accounts SET email_verified=1 WHERE owner_id=?", (self.owner,))
        self.db.commit()
        for i in range(5):
            self.assertIsNone(self.login("incorrect password 2026", ip="192.0.2.%d" % (i + 1)))
        self.assertIsNone(self.login("strong unique password 2026", ip="192.0.2.99"))
        self.assertIsNotNone(self.login("strong unique password 2026", ip="192.0.2.99", now=1900))

    def test_rotating_email_cannot_bypass_ip_limit(self):
        for i in range(5):
            self.assertIsNone(attempt_login(
                self.db, "unknown%d@example.org" % i, "wrong password 2026",
                "192.0.2.1", self.secret, now=1000,
            ))
        self.db.execute("UPDATE opportunity_accounts SET email_verified=1 WHERE owner_id=?", (self.owner,))
        self.db.commit()
        self.assertIsNone(self.login("strong unique password 2026", now=1000))
        self.assertIsNotNone(self.login("strong unique password 2026", ip="192.0.2.2", now=1000))


if __name__ == "__main__":
    unittest.main()
