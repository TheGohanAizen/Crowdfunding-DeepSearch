"""Offline owner-isolation regression tests. No network, Turso or outreach."""
import sqlite3
import tempfile
import os
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

    def test_default_https_port_merges_with_portless_url(self):
        first = add(self.db, "verified-user-A", {
            "url": "https://example.org:443/submit", "notes": "Original",
        })
        duplicate = add(self.db, "verified-user-A", {
            "url": "https://EXAMPLE.ORG/submit", "notes": "Should not overwrite",
        })
        self.assertTrue(first["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(first["record_id"], duplicate["record_id"])
        rows = list_for_owner(self.db, "verified-user-A")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://example.org/submit")
        self.assertEqual(rows[0]["notes"], "Original")

    def test_reject_invalid_urls_and_inputs(self):
        for url in ("http://example.org", "javascript:alert(1)", "https://user:pass@example.org",
                    "https://example.org:444/", "https://example.org:abc/",
                    "https://@example.org/", "https://:@example.org/",
                    "https://example.org:/", "https://", "not a url",
                    "https://127.0.0.1/", "https://localhost/", "https://example..org/",
                    "https://example.org.evil@attacker.org/", "https://bad_host.example.org/",
                    "https://-bad.example.org/", "https://bad-.example.org/",
                    "https://example.123/", "https://[broken/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)
        self.assertEqual(normalize_url("https://EXAMPLE.ORG/path/#section"), "https://example.org/path")
        self.assertEqual(normalize_url("https://bücher.example/"), "https://xn--bcher-kva.example/")
        self.assertEqual(normalize_url("https://example.org:443/"), "https://example.org/")
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
            "https://example.org/a b",
            "https://example.org/?q=a b",
            "https://example.org/word\u00a0break",
        ):
            with self.subTest(url=repr(url)), self.assertRaises(ValueError):
                normalize_url(url)
        self.assertEqual(normalize_url(" https://example.org/path "), "https://example.org/path")
        self.assertEqual(normalize_url("https://example.org/a%20b"), "https://example.org/a%20b")

    def test_percent_escapes_must_be_complete_hexadecimal(self):
        for url in (
            "https://example.org/%",
            "https://example.org/%2",
            "https://example.org/%GG",
            "https://example.org/?q=%Q1",
            "https://example.org/%20%4",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)
            with self.assertRaises(ValueError):
                add(self.db, "verified-user-A", {"url": url})
        self.assertEqual(normalize_url("https://example.org/a%20b"),
                         "https://example.org/a%20b")
        self.assertEqual(normalize_url("https://example.org/?q=%2F"),
                         "https://example.org/?q=%2F")
        self.assertEqual(list_for_owner(self.db, "verified-user-A"), [])

    def test_encoded_control_bytes_are_rejected(self):
        for url in (
            "https://example.org/%0d",
            "https://example.org/path%0Aheader",
            "https://example.org/?q=%00",
            "https://example.org/?q=%0D%0A",
            "https://example.org/%01",
            "https://example.org/?q=%1f",
            "https://example.org/%7F",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)
            with self.assertRaises(ValueError):
                add(self.db, "verified-user-A", {"url": url})
        self.assertEqual(normalize_url("https://example.org/%20"),
                         "https://example.org/%20")
        self.assertEqual(normalize_url("https://example.org/%80"),
                         "https://example.org/%80")
        self.assertEqual(list_for_owner(self.db, "verified-user-A"), [])

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

    def test_idna_expansion_cannot_exceed_stored_url_limit(self):
        # Unicode hostname labels can grow when encoded as ASCII IDNA.
        unicode_host = ".".join(["é" * 20] * 3) + ".example"
        ascii_host = unicode_host.encode("idna").decode("ascii")
        self.assertGreater(len(ascii_host), len(unicode_host))
        max_input = 2048 - len("https:///") - len(unicode_host)
        url = "https://" + unicode_host + "/" + ("x" * max_input)
        self.assertLessEqual(len(url), 2048)
        self.assertGreater(len("https://" + ascii_host + "/" + ("x" * max_input)), 2048)
        with self.assertRaises(ValueError):
            normalize_url(url)
        with self.assertRaises(ValueError):
            add(self.db, "verified-user-A", {"url": url})
        self.assertEqual(list_for_owner(self.db, "verified-user-A"), [])

    def test_multibyte_url_cannot_exceed_utf8_storage_limit(self):
        prefix = "https://example.org/"
        # A Unicode path can be below the character limit but over 2048 bytes.
        url = prefix + ("é" * 1100)
        self.assertLessEqual(len(url), 2048)
        self.assertGreater(len(url.encode("utf-8")), 2048)
        with self.assertRaises(ValueError):
            normalize_url(url)
        with self.assertRaises(ValueError):
            add(self.db, "verified-user-A", {"url": url})
        self.assertEqual(list_for_owner(self.db, "verified-user-A"), [])
        self.assertEqual(normalize_url(prefix + "café"), prefix + "café")

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

    def test_initialize_does_not_commit_caller_transaction(self):
        self.db.execute("CREATE TABLE unrelated_work (value TEXT)")
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("INSERT INTO unrelated_work VALUES (?)", ("pending",))
        with self.assertRaisesRegex(ValueError, "transaction-free"):
            initialize(self.db)
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM unrelated_work").fetchone()[0], 1)
        self.db.rollback()
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM unrelated_work").fetchone()[0], 0)
        initialize(self.db)

    def test_add_does_not_interfere_with_caller_transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            """INSERT INTO opportunity_records
            (owner_id,record_id,source_url,normalized_url,name,lead_type,
             review_status,campaign_label,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("verified-user-A", "a" * 32, "https://example.org/existing",
             "https://example.org/existing", "Existing", "unverified",
             "needs_review", "", "", "2026-01-01", "2026-01-01"),
        )
        with self.assertRaisesRegex(ValueError, "transaction-free"):
            add(self.db, "verified-user-A", {"url": "https://example.org/new"})
        self.assertTrue(self.db.in_transaction)
        self.assertEqual(len(list_for_owner(self.db, "verified-user-A")), 1)
        self.db.rollback()
        self.assertEqual(list_for_owner(self.db, "verified-user-A"), [])
        self.assertTrue(add(self.db, "verified-user-A",
                            {"url": "https://example.org/new"})["created"])

    def test_update_and_delete_preserve_caller_transaction(self):
        record = add(self.db, "verified-user-A", {
            "url": "https://example.org/protected", "notes": "Original",
        })
        self.db.execute("BEGIN IMMEDIATE")
        for operation in (
            lambda: update_for_owner(self.db, "verified-user-A",
                                     record["record_id"], {"notes": "Changed"}),
            lambda: delete_for_owner(self.db, "verified-user-A", record["record_id"]),
        ):
            with self.assertRaisesRegex(ValueError, "transaction-free"):
                operation()
            self.assertTrue(self.db.in_transaction)
        self.assertEqual(list_for_owner(self.db, "verified-user-A")[0]["notes"], "Original")
        self.db.rollback()
        self.assertTrue(update_for_owner(self.db, "verified-user-A",
                                         record["record_id"], {"notes": "Changed"}))
        self.assertTrue(delete_for_owner(self.db, "verified-user-A", record["record_id"]))

    def test_concurrent_writer_cannot_bypass_quota(self):
        # Separate SQLite connections exercise the database-level write lock.
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "opportunities.sqlite3")
            writer = sqlite3.connect(path, timeout=0.1)
            contender = sqlite3.connect(path, timeout=0.1)
            try:
                initialize(writer)
                writer.execute("BEGIN IMMEDIATE")
                with self.assertRaises(sqlite3.OperationalError):
                    add(contender, "verified-user-A", {"url": "https://example.org/blocked"})
                writer.rollback()
                self.assertFalse(contender.in_transaction)
                self.assertTrue(add(contender, "verified-user-A",
                                    {"url": "https://example.org/allowed"})["created"])
                self.assertEqual(len(list_for_owner(writer, "verified-user-A")), 1)
            finally:
                contender.close()
                writer.close()

    def test_quota_per_owner(self):
        for index in range(MAX_RECORDS):
            self.assertTrue(add(self.db, "verified-user-A", {"url": f"https://example.org/{index}"})["created"])
        with self.assertRaisesRegex(ValueError, "quota"):
            add(self.db, "verified-user-A", {"url": "https://example.org/extra"})
        self.assertTrue(add(self.db, "verified-user-B", {"url": "https://example.org/extra"})["created"])
        self.assertFalse(add(self.db, "verified-user-A", {"url": "https://example.org/0"})["created"])


if __name__ == "__main__":
    unittest.main()
