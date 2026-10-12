"""Offline owner-isolation regression tests. No network, Turso or outreach."""
import sqlite3
import unittest
from opportunity_store import (
    initialize, add, list_for_owner, update_for_owner, delete_for_owner,
    normalize_url, MAX_RECORDS,
)


class OpportunityStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        initialize(self.db)

    def tearDown(self):
        self.db.close()

    def test_owner_isolation_and_duplicate_merge(self):
        item = {"url": "https://example.org/submit/#form", "name": "Submission page",
                "campaign_label": "Transportation", "notes": "Public form", "status": "researching"}
        a = add(self.db, "verified-user-A", item)
        self.assertTrue(a["created"])
        self.assertFalse(add(self.db, "verified-user-A", {**item, "url": "https://example.org/submit"})["created"])
        self.assertEqual(len(list_for_owner(self.db, "verified-user-A")), 1)
        self.assertEqual(list_for_owner(self.db, "verified-user-B"), [])
        self.assertFalse(update_for_owner(self.db, "verified-user-B", a["record_id"], {"notes": "Intrusion"}))
        self.assertFalse(delete_for_owner(self.db, "verified-user-B", a["record_id"]))
        self.assertEqual(list_for_owner(self.db, "verified-user-A")[0]["notes"], "Public form")
        self.assertTrue(update_for_owner(self.db, "verified-user-A", a["record_id"], {"status": "submission_route_found"}))
        self.assertEqual(list_for_owner(self.db, "verified-user-A")[0]["status"], "submission_route_found")
        b = add(self.db, "verified-user-B", item)
        self.assertTrue(b["created"])
        self.assertNotEqual(a["record_id"], b["record_id"])
        self.assertTrue(delete_for_owner(self.db, "verified-user-A", a["record_id"]))
        self.assertEqual(len(list_for_owner(self.db, "verified-user-B")), 1)

    def test_reject_invalid_urls_and_inputs(self):
        for url in ("http://example.org", "javascript:alert(1)", "https://user:pass@example.org",
                    "https://example.org:444/", "https://example.org:abc/",
                    "https://@example.org/", "https://:@example.org/", "https://", "not a url",
                    "https://127.0.0.1/", "https://localhost/", "https://example..org/",
                    "https://example.org.evil@attacker.org/", "https://bad_host.example.org/",
                    "https://-bad.example.org/", "https://bad-.example.org/",
                    "https://example.123/", "https://[broken/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)
        self.assertEqual(normalize_url("https://EXAMPLE.ORG/path/#section"), "https://example.org/path")
        self.assertEqual(normalize_url("https://bücher.example/"), "https://xn--bcher-kva.example/")
        with self.assertRaises(ValueError):
            add(self.db, "", {"url": "https://example.org"})
        with self.assertRaises(ValueError):
            add(self.db, "verified-user-A", {"url": "https://example.org", "status": "sent"})
        with self.assertRaises(ValueError):
            add(self.db, "verified-user-A", {"url": "https://example.org", "notes": 42})
        with self.assertRaises(ValueError):
            update_for_owner(self.db, "verified-user-A", "missing", {"owner_id": "verified-user-B"})
        with self.assertRaises(ValueError):
            add(self.db, "verified-user-A", {"url": "https://example.org", "status": []})
        with self.assertRaises(ValueError):
            update_for_owner(self.db, "verified-user-A", "0" * 32, {"status": []})
        with self.assertRaises(ValueError):
            delete_for_owner(self.db, "verified-user-A", "../other-user")

    def test_ambiguous_url_characters_are_rejected(self):
        for url in (
            "https://example.org/pa\\th",
            "https://example.org/path\nnext",
            "https://example.org/path\tmore",
            "https://example.org/path\x00hidden",
            "https://example.org/path\x7fhidden",
        ):
            with self.subTest(url=repr(url)), self.assertRaises(ValueError):
                normalize_url(url)
        self.assertEqual(normalize_url(" https://example.org/path "), "https://example.org/path")

    def test_malformed_unicode_urls_rejected_before_storage(self):
        for url in ("https://example.org/\ud800", "https://example.org/?q=\ud800"):
            with self.subTest(url=repr(url)), self.assertRaises(ValueError):
                normalize_url(url)
            with self.assertRaises(ValueError):
                add(self.db, "verified-user-A", {"url": url})
        self.assertEqual(list_for_owner(self.db, "verified-user-A"), [])

    def test_malformed_unicode_owner_rejected_across_operations(self):
        invalid_owner = "\ud800"
        record = add(self.db, "verified-user-A", {"url": "https://example.org/item"})
        for operation in (
            lambda: add(self.db, invalid_owner, {"url": "https://example.org/item2"}),
            lambda: list_for_owner(self.db, invalid_owner),
            lambda: update_for_owner(self.db, invalid_owner, record["record_id"], {"notes": "bad"}),
            lambda: delete_for_owner(self.db, invalid_owner, record["record_id"]),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(len(list_for_owner(self.db, "verified-user-A")), 1)

    def test_malformed_unicode_metadata_is_rejected_without_changes(self):
        record = add(self.db, "verified-user-A", {"url": "https://example.org/original", "notes": "Safe"})
        for field in ("name", "lead_type", "campaign_label", "notes"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                add(self.db, "verified-user-A", {"url": "https://example.org/new", field: "\ud800"})
        for field in ("campaign_label", "notes"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                update_for_owner(self.db, "verified-user-A", record["record_id"], {field: "\ud800"})
        rows = list_for_owner(self.db, "verified-user-A")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["notes"], "Safe")

    def test_malformed_unicode_after_truncation_boundary_is_rejected(self):
        for field, limit in (("name", 250), ("lead_type", 100),
                             ("campaign_label", 100), ("notes", 1500)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                add(self.db, "verified-user-A", {
                    "url": "https://example.org/new", field: "x" * limit + "\ud800",
                })
        self.assertEqual(list_for_owner(self.db, "verified-user-A"), [])

    def test_boolean_pagination_limits_rejected(self):
        add(self.db, "verified-user-A", {"url": "https://example.org/item"})
        for limit in (True, False, 0, MAX_RECORDS + 1, 1.0, "1"):
            with self.subTest(limit=repr(limit)), self.assertRaises(ValueError):
                list_for_owner(self.db, "verified-user-A", limit=limit)
        self.assertEqual(len(list_for_owner(self.db, "verified-user-A", limit=1)), 1)

    def test_duplicate_and_quota_paths_release_write_transaction(self):
        first = add(self.db, "verified-user-A", {"url": "https://example.org/first"})
        duplicate = add(self.db, "verified-user-A", {"url": "https://example.org/first"})
        self.assertEqual(duplicate["record_id"], first["record_id"])
        self.assertFalse(duplicate["created"])
        self.assertFalse(self.db.in_transaction)
        for index in range(MAX_RECORDS - 1):
            add(self.db, "verified-user-A", {"url": f"https://example.org/item/{index}"})
        with self.assertRaisesRegex(ValueError, "quota"):
            add(self.db, "verified-user-A", {"url": "https://example.org/overflow"})
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(len(list_for_owner(self.db, "verified-user-A", limit=MAX_RECORDS)), MAX_RECORDS)
        self.assertTrue(add(self.db, "verified-user-B", {"url": "https://example.org/allowed"})["created"])

    def test_quota_per_owner(self):
        for index in range(MAX_RECORDS):
            self.assertTrue(add(self.db, "verified-user-A", {"url": f"https://example.org/{index}"})["created"])
        with self.assertRaisesRegex(ValueError, "quota"):
            add(self.db, "verified-user-A", {"url": "https://example.org/extra"})
        self.assertTrue(add(self.db, "verified-user-B", {"url": "https://example.org/extra"})["created"])
        self.assertFalse(add(self.db, "verified-user-A", {"url": "https://example.org/0"})["created"])


if __name__ == "__main__":
    unittest.main()
