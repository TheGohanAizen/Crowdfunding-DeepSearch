"""Offline read-only URL audit tests; no production services."""
import sqlite3
import unittest

from opportunity_store import initialize, add
from opportunity_url_audit import audit


class OpportunityUrlAuditTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        initialize(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_empty_store_reports_no_collisions(self):
        report = audit(self.connection)
        self.assertEqual(report["records_checked"], 0)
        self.assertEqual(report["collision_groups"], 0)
        self.assertEqual(report["stored_key_mismatches"], 0)
        self.assertFalse(report["safe_to_auto_migrate"])

    def test_audit_does_not_modify_records_or_expose_private_values(self):
        add(self.connection, "private-owner", {
            "url": "https://example.org/apply/", "notes": "Private notes",
        })
        add(self.connection, "private-owner", {
            "url": "https://example.org/apply", "notes": "Other notes",
        })
        add(self.connection, "other-owner", {
            "url": "https://example.org/apply/",
        })
        before = self.connection.execute(
            "SELECT owner_id, record_id, normalized_url, notes "
            "FROM opportunity_records ORDER BY owner_id, record_id"
        ).fetchall()
        report = audit(self.connection)
        after = self.connection.execute(
            "SELECT owner_id, record_id, normalized_url, notes "
            "FROM opportunity_records ORDER BY owner_id, record_id"
        ).fetchall()
        self.assertEqual(before, after)
        self.assertEqual(report["records_checked"], 3)
        self.assertEqual(report["collision_groups"], 0)
        self.assertNotIn("private-owner", str(report))
        self.assertNotIn("Private notes", str(report))
        self.assertNotIn("https://", str(report))

    def test_invalid_source_url_is_reported_without_disclosing_it(self):
        add(self.connection, "owner", {"url": "https://example.org/valid"})
        self.connection.execute(
            "UPDATE opportunity_records SET source_url=? WHERE owner_id=?",
            ("https://example.org/unsafe\\\\path", "owner"),
        )
        self.connection.commit()
        report = audit(self.connection)
        self.assertEqual(report["records_checked"], 1)
        self.assertEqual(report["invalid_source_urls"], 1)
        self.assertEqual(report["collision_groups"], 0)
        self.assertNotIn("unsafe", str(report))
        self.assertFalse(report["safe_to_auto_migrate"])

    def test_detects_same_owner_normalization_collision(self):
        add(self.connection, "owner", {"url": "https://example.org/a"})
        # Simulate legacy data that stored a noncanonical equivalent.
        self.connection.execute(
            "UPDATE opportunity_records SET normalized_url=? WHERE owner_id=?",
            ("https://EXAMPLE.ORG/a", "owner"),
        )
        self.connection.execute(
            """INSERT INTO opportunity_records
               (owner_id, record_id, source_url, normalized_url, name,
                lead_type, review_status, campaign_label, notes, created_at, updated_at)
               SELECT owner_id, ?, source_url, ?, name, lead_type, review_status,
                      campaign_label, notes, created_at, updated_at
               FROM opportunity_records WHERE owner_id=? LIMIT 1""",
            ("f" * 32, "https://example.org/a", "owner"),
        )
        self.connection.commit()
        report = audit(self.connection)
        self.assertEqual(report["records_checked"], 2)
        self.assertEqual(report["collision_groups"], 1)
        self.assertEqual(report["stored_key_mismatches"], 1)
        self.assertEqual(report["records_in_collision_groups"], 2)


if __name__ == "__main__":
    unittest.main()
