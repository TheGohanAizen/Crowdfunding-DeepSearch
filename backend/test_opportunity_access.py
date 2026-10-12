"""End-to-end offline session-to-owner storage tests."""
import sqlite3
import unittest

from opportunity_access import (
    list_opportunities, create_opportunity, update_opportunity, delete_opportunity,
)
from opportunity_sessions import issue_session, issue_csrf
from opportunity_store import initialize


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        initialize(self.db)
        self.secret = b"local-only-test-secret-32-bytes-minimum-123"
        self.a = issue_session("verified-A", self.secret, now=1000)
        self.b = issue_session("verified-B", self.secret, now=1000)
        self.csrf_a = issue_csrf(self.a, self.secret)
        self.csrf_b = issue_csrf(self.b, self.secret)

    def tearDown(self):
        self.db.close()

    def test_session_owner_isolation_and_csrf(self):
        record = create_opportunity(self.db, self.a, self.csrf_a, self.secret,
                                    {"url": "https://example.org/submit", "notes": "Private A"}, now=1000)
        record_id = record["record_id"]
        self.assertEqual(len(list_opportunities(self.db, self.a, self.secret, now=1000)), 1)
        self.assertEqual(list_opportunities(self.db, self.b, self.secret, now=1000), [])
        with self.assertRaises(PermissionError):
            update_opportunity(self.db, self.a, self.csrf_b, self.secret, record_id,
                               {"notes": "Changed"}, now=1000)
        with self.assertRaises(PermissionError):
            delete_opportunity(self.db, self.a, "", self.secret, record_id, now=1000)
        self.assertFalse(update_opportunity(self.db, self.b, self.csrf_b, self.secret,
                                            record_id, {"notes": "Changed"}, now=1000))
        self.assertFalse(delete_opportunity(self.db, self.b, self.csrf_b, self.secret,
                                            record_id, now=1000))
        self.assertEqual(list_opportunities(self.db, self.a, self.secret, now=1000)[0]["notes"], "Private A")
        self.assertTrue(update_opportunity(self.db, self.a, self.csrf_a, self.secret,
                                           record_id, {"notes": "Approved"}, now=1000))
        self.assertTrue(delete_opportunity(self.db, self.a, self.csrf_a, self.secret,
                                           record_id, now=1000))

    def test_expired_and_forged_sessions_cannot_read_or_write(self):
        with self.assertRaises(ValueError):
            list_opportunities(self.db, self.a, self.secret, now=1000 + 43201)
        with self.assertRaises(ValueError):
            create_opportunity(self.db, self.a, self.csrf_a, self.secret,
                               {"url": "https://example.org"}, now=1000 + 43201)
        with self.assertRaises(ValueError):
            list_opportunities(self.db, "forged", self.secret, now=1000)


if __name__ == "__main__":
    unittest.main()
