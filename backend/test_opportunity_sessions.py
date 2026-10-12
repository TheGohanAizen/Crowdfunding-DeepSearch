"""Offline session security tests; no production secrets or network."""
import unittest
from opportunity_sessions import issue_session, verify_session, issue_csrf, verify_csrf


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.secret = b"local-test-only-secret-not-for-production-000000"

    def test_issue_verify_and_expire(self):
        token = issue_session("verified-user-A", self.secret, now=1000)
        self.assertEqual(verify_session(token, self.secret, now=1000), "verified-user-A")
        self.assertEqual(verify_session(token, self.secret, now=1000 + 43200), "verified-user-A")
        with self.assertRaises(ValueError):
            verify_session(token, self.secret, now=1000 + 43201)
        with self.assertRaises(ValueError):
            verify_session(token, self.secret, now=1000 - 61)

    def test_tampering_and_wrong_secret(self):
        token = issue_session("verified-user-A", self.secret, now=1000)
        with self.assertRaises(ValueError):
            verify_session(token, b"another-local-test-secret-not-production-0000", now=1000)
        with self.assertRaises(ValueError):
            verify_session(("A" if token[0] != "A" else "B") + token[1:], self.secret, now=1000)
        with self.assertRaises(ValueError):
            verify_session("not-a-token", self.secret, now=1000)

    def test_non_ascii_token_is_rejected(self):
        token = issue_session("verified-user-A", self.secret, now=1000)
        with self.assertRaises(ValueError):
            verify_session("é" + token, self.secret, now=1000)

    def test_csrf_session_binding(self):
        first = issue_session("verified-user-A", self.secret, now=1000)
        second = issue_session("verified-user-A", self.secret, now=1000)
        csrf = issue_csrf(first, self.secret)
        self.assertTrue(verify_csrf(first, csrf, self.secret))
        self.assertFalse(verify_csrf(second, csrf, self.secret))
        self.assertFalse(verify_csrf(first, "not-valid", self.secret))
        self.assertFalse(verify_csrf(first, csrf, b"another-local-test-secret-not-production-0000"))
        self.assertFalse(verify_csrf(first, "x" * 500, self.secret))

    def test_bad_secret_and_claims(self):
        with self.assertRaises(ValueError):
            issue_session("verified-user-A", b"short")
        with self.assertRaises(ValueError):
            issue_session("", self.secret)
        token = issue_session("verified-user-A", self.secret, now=1000)
        with self.assertRaises(ValueError):
            verify_session(token, self.secret, now=1000, max_age=1000000)


if __name__ == "__main__":
    unittest.main()
