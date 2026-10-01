from __future__ import annotations

import unittest
from pathlib import Path

from scripts.sync_author_conditions_ext import build_salesforce_match_sql


REPO_ROOT = Path(__file__).resolve().parents[1]
ACCESS_SQL = (REPO_ROOT / "sql" / "access_input_build.sql").read_text(encoding="utf-8")
QUALITY_SQL = (REPO_ROOT / "sql" / "source_quality_checks.sql").read_text(encoding="utf-8")
SYNC_SOURCE = (REPO_ROOT / "scripts" / "sync_author_conditions_ext.py").read_text(encoding="utf-8")


class AccessAuthorConditionsExtSqlTest(unittest.TestCase):
    def test_access_input_reads_base_and_ext(self) -> None:
        self.assertIn("source_author_conditions`", ACCESS_SQL)
        self.assertIn("source_author_conditions_ext` e", ACCESS_SQL)
        self.assertIn("combined_author_conditions", ACCESS_SQL)

    def test_ext_is_supplement_only_when_base_key_missing(self) -> None:
        self.assertIn("NOT EXISTS", ACCESS_SQL)
        self.assertIn("b.product_code = e.product_code", ACCESS_SQL)
        self.assertIn("b.payee_code = e.payee_code", ACCESS_SQL)

    def test_base_lookup_precedes_ext_for_sales_and_store(self) -> None:
        self.assertGreaterEqual(
            ACCESS_SQL.count("SELECT product_key, electronic_publication_code, 1 AS priority FROM current_author_lookup"),
            2,
        )
        self.assertGreaterEqual(
            ACCESS_SQL.count("SELECT product_key, electronic_publication_code, 0 AS priority FROM ext_author_lookup"),
            2,
        )

    def test_same_product_can_keep_multiple_ext_payees(self) -> None:
        # The ext supplement is filtered on the logical key, not product_code alone.
        self.assertIn("b.payee_code = e.payee_code", ACCESS_SQL)

    def test_ext_rows_are_not_duplicated_by_base(self) -> None:
        # The anti-join makes repeated Access-input builds idempotent.
        ext_block = ACCESS_SQL.split("UNION ALL", 1)[1].split(")\nSELECT", 1)[0]
        self.assertIn("NOT EXISTS", ext_block)


class QualityCheckExtSqlTest(unittest.TestCase):
    def test_monthly_unmatched_uses_base_and_ext(self) -> None:
        marker = "monthly_sales_author_conditions_unmatched"
        section = QUALITY_SQL[QUALITY_SQL.index(marker):]
        self.assertIn("source_author_conditions`", section)
        self.assertIn("source_author_conditions_ext`", section)
        self.assertIn("UNION DISTINCT", section)

    def test_unresolved_products_remain_unmatched(self) -> None:
        # No unresolved-table bypass: only presence in base/ext resolves the warning.
        marker = "monthly_sales_author_conditions_unmatched"
        section = QUALITY_SQL[QUALITY_SQL.index(marker):]
        self.assertNotIn("source_author_conditions_ext_unresolved", section)
        self.assertIn("GROUP BY product_code", section)
        self.assertIn("COUNTIF(ac.product_code IS NULL)", section)


class SalesforceExtEnrichmentTest(unittest.TestCase):
    def test_salesforce_match_includes_access_compatible_metadata(self) -> None:
        sql = build_salesforce_match_sql("ice-qb", "ice_qb_source")
        self.assertIn("bc.contributor_id__c AS author_identifier_id", sql)
        self.assertIn("bc.contributor_role__c AS author_category", sql)
        self.assertIn("b.title__c AS title", sql)
        self.assertIn("b.psf_planning_edit__c AS planning_editor", sql)
        self.assertIn("royalty_reservation_price__c", sql)
        self.assertIn("payment_hold_limit_amount", sql)

    def test_ext_logical_key_is_product_and_payee(self) -> None:
        self.assertIn(
            "e.product_code = r.product_code\n            AND e.payee_code = r.payee_code",
            SYNC_SOURCE,
        )
        self.assertIn("e.product_code = r.product_code", SYNC_SOURCE)
        self.assertIn("e.payee_code = r.payee_code", SYNC_SOURCE)

    def test_existing_sf_auto_rows_are_refreshed_before_insert(self) -> None:
        self.assertIn("UPDATE `{project_id}.{source_dataset}.source_author_conditions_ext` e", SYNC_SOURCE)
        self.assertIn("AND e.source_type = 'sf_auto'", SYNC_SOURCE)
        self.assertIn("WHERE NOT EXISTS", SYNC_SOURCE)

    def test_manual_ext_rows_are_not_overwritten(self) -> None:
        # Update is deliberately limited to sf_auto; manual rows can only block duplicate insert.
        self.assertIn("AND e.source_type = 'sf_auto'", SYNC_SOURCE)


if __name__ == "__main__":
    unittest.main()
