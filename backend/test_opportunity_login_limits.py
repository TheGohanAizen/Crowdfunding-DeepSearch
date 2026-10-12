"""Offline login throttle tests."""
import sqlite3
import unittest

from opportunity_login_limits import initialize, bucket_key, record_attempt, record_attempts


class LoginThrottleTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        initialize(self.db)

    def tearDown(self):
        self.db.close()

    def test_attempt_limit_and_window_reset(self):
        key = bucket_key("USER@example.org", "192.0.2.1")
        self.assertEqual(key, bucket_key("user@example.org", "192.0.2.1"))
        for _ in range(5):
            self.assertTrue(record_attempt(self.db, key, now=1000))
        self.assertFalse(record_attempt(self.db, key, now=1001))
        self.assertTrue(record_attempt(self.db, key, now=1900))
        self.assertTrue(record_attempt(self.db, bucket_key("user@example.org", "192.0.2.2"), now=1000))

    def test_clock_rollback_does_not_reset_single_bucket(self):
        key = bucket_key("user@example.org", "192.0.2.1")
        for _ in range(5):
            self.assertTrue(record_attempt(self.db, key, now=1000))
        self.assertFalse(record_attempt(self.db, key, now=999))
        self.assertFalse(record_attempt(self.db, key, now=1000))
        self.assertTrue(record_attempt(self.db, key, now=1900))

    def test_clock_rollback_does_not_reset_multi_bucket(self):
        keys = (bucket_key("user@example.org", "192.0.2.1"),
                bucket_key("all-ip-attempts", "192.0.2.1"))
        for _ in range(5):
            self.assertTrue(record_attempts(self.db, keys, now=1000))
        self.assertFalse(record_attempts(self.db, keys, now=999))
        self.assertFalse(record_attempts(self.db, keys, now=1000))
        self.assertTrue(record_attempts(self.db, keys, now=1900))

    def test_invalid_bucket(self):
        with self.assertRaises(ValueError):
            record_attempt(self.db, "untrusted", now=1000)
        with self.assertRaises(ValueError):
            bucket_key("", "192.0.2.1")


if __name__ == "__main__":
    unittest.main()
