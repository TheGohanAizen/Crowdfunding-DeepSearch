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

    def test_disabled_account_cannot_issue_or_consume(self):
        token = issue_challenge(self.db, self.owner, now=1000)
        self.db.execute("UPDATE opportunity_accounts SET disabled_at=? WHERE owner_id=?", ("2026-10-11", self.owner))
        self.db.commit()
        self.assertFalse(consume_challenge(self.db, self.owner, token, now=1000))
        with self.assertRaises(ValueError):
            issue_challenge(self.db, self.owner, now=1000)

    def test_wrong_account_cannot_consume(self):
        second = register_unverified(self.db, "other@example.org", "another secure password 2026")
        token = issue_challenge(self.db, self.owner, now=1000)
        self.assertFalse(consume_challenge(self.db, second, token, now=1000))
        self.assertTrue(consume_challenge(self.db, self.owner, token, now=1000))

    def test_resend_cooldown_does_not_invalidate_original_token(self):
        first = issue_challenge(self.db, self.owner, now=1000)
        with self.assertRaises(ValueError):
            issue_challenge(self.db, self.owner, now=1059)
        self.assertTrue(consume_challenge(self.db, self.owner, first, now=1059))

    def test_resend_after_cooldown_invalidates_old_token(self):
        first = issue_challenge(self.db, self.owner, now=1000)
        second = issue_challenge(self.db, self.owner, now=1060)
        self.assertFalse(consume_challenge(self.db, self.owner, first, now=1060))
        self.assertTrue(consume_challenge(self.db, self.owner, second, now=1060))

    def test_hourly_issue_cap_and_window_reset(self):
        tokens = [issue_challenge(self.db, self.owner, now=1000 + i * 60) for i in range(6)]
        with self.assertRaisesRegex(ValueError, "hourly limit"):
            issue_challenge(self.db, self.owner, now=1360)
        # Rejected issuance must preserve the previously issued token.
        self.assertTrue(consume_challenge(self.db, self.owner, tokens[-1], now=1360))

    def test_hourly_limit_resets_after_window(self):
        for i in range(6):
            issue_challenge(self.db, self.owner, now=1000 + i * 60)
        with self.assertRaisesRegex(ValueError, "hourly limit"):
            issue_challenge(self.db, self.owner, now=1360)
        token = issue_challenge(self.db, self.owner, now=4600)
        self.assertTrue(consume_challenge(self.db, self.owner, token, now=4600))

    def test_clock_rollback_does_not_reset_hourly_limit(self):
        for i in range(6):
            issue_challenge(self.db, self.owner, now=1000 + i * 60)
        with self.assertRaises(ValueError):
            issue_challenge(self.db, self.owner, now=999)
        with self.assertRaisesRegex(ValueError, "hourly limit"):
            issue_challenge(self.db, self.owner, now=1360)

    def test_expiry_and_replacement(self):
        first = issue_challenge(self.db, self.owner, now=1000)
        self.assertFalse(consume_challenge(self.db, self.owner, first, now=2800))
        second = issue_challenge(self.db, self.owner, now=2900)
        self.assertFalse(consume_challenge(self.db, self.owner, first, now=2900))
        self.assertTrue(consume_challenge(self.db, self.owner, second, now=2900))


if __name__ == "__main__":
    unittest.main()
