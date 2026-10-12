"""Offline login throttle tests."""
import sqlite3
import unittest

from opportunity_login_limits import initialize, bucket_key, record_attempt, record_attempts, prune_expired_buckets


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

    def test_bucket_fields_cannot_collide_via_delimiters(self):
        self.assertNotEqual(bucket_key("a|b", "c"), bucket_key("a", "b|c"))
        self.assertNotEqual(bucket_key("ab", "c"), bucket_key("a", "bc"))

    def test_prune_expired_buckets_is_bounded_and_preserves_active_limits(self):
        old = [bucket_key("old%d@example.org" % i, "192.0.2.1") for i in range(3)]
        active = bucket_key("active@example.org", "192.0.2.1")
        for key in old:
            self.assertTrue(record_attempt(self.db, key, now=1000))
        for _ in range(5):
            self.assertTrue(record_attempt(self.db, active, now=1900))
        self.assertEqual(prune_expired_buckets(self.db, now=1900, batch_size=2), 2)
        self.assertEqual(prune_expired_buckets(self.db, now=1900, batch_size=2), 1)
        self.assertEqual(prune_expired_buckets(self.db, now=1900, batch_size=2), 0)
        self.assertFalse(record_attempt(self.db, active, now=1900))
        self.assertTrue(record_attempt(self.db, old[0], now=1900))

    def test_prune_rejects_invalid_batch_size(self):
        for size in (0, -1, 5001, True):
            with self.assertRaises(ValueError):
                prune_expired_buckets(self.db, now=1900, batch_size=size)

    def test_malformed_unicode_bucket_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            bucket_key("account", "\ud800")
        with self.assertRaises(ValueError):
            bucket_key("\ud800", "192.0.2.1")

    def test_invalid_bucket(self):
        with self.assertRaises(ValueError):
            record_attempt(self.db, "untrusted", now=1000)
        with self.assertRaises(ValueError):
            bucket_key("", "192.0.2.1")


if __name__ == "__main__":
    unittest.main()
