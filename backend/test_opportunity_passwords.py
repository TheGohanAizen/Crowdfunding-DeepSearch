"""Offline password hash tests; no user accounts or external services."""
import unittest
from opportunity_passwords import hash_password, verify_password


class PasswordTests(unittest.TestCase):
    def test_salted_hashes_and_verification(self):
        password = "correct horse battery staple 2026!"
        first = hash_password(password)
        second = hash_password(password)
        self.assertNotEqual(first, second)
        self.assertNotIn(password, first)
        self.assertTrue(verify_password(password, first))
        self.assertFalse(verify_password("incorrect password 2026", first))
        self.assertFalse(verify_password(password, first[:-1] + ("0" if first[-1] != "0" else "1")))

    def test_malformed_unicode_password_fails_closed(self):
        with self.assertRaises(ValueError):
            hash_password("\ud800" * 12)
        valid_hash = hash_password("correct horse battery staple 2026!")
        self.assertFalse(verify_password("\ud800" * 12, valid_hash))

    def test_invalid_passwords_and_hashes(self):
        for value in ("short", "", None, 123, "x" * 1025):
            with self.subTest(value=repr(value)[:40]), self.assertRaises(ValueError):
                hash_password(value)
        for stored in ("", None, "plaintext", "scrypt$1$8$1$00$00", "scrypt$16384$8$1$00$00"):
            with self.subTest(stored=stored):
                self.assertFalse(verify_password("correct horse battery staple 2026!", stored))


if __name__ == "__main__":
    unittest.main()
