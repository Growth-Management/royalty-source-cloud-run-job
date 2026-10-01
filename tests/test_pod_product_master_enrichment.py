from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POD_SOURCE_SQL = (REPO_ROOT / "sql" / "pod_source_build.sql").read_text(encoding="utf-8")
ACCESS_QUALITY_SQL = (REPO_ROOT / "sql" / "access_input_quality_checks.sql").read_text(encoding="utf-8")


class PodProductMasterEnrichmentSqlTest(unittest.TestCase):
    def test_current_product_master_is_used_for_supplement(self) -> None:
        self.assertIn("source_product_master`", POD_SOURCE_SQL)
        self.assertIn("product_master_physical", POD_SOURCE_SQL)

    def test_input_product_code_has_precedence(self) -> None:
        self.assertIn(
            "WHEN NULLIF(TRIM(product_code), '') IS NOT NULL THEN TRIM(product_code)",
            POD_SOURCE_SQL,
        )

    def test_exact_isbn_precedes_normalized_title(self) -> None:
        self.assertIn("exact_isbn_match_count", POD_SOURCE_SQL)
        self.assertIn("normalized_title_match_count", POD_SOURCE_SQL)
        self.assertIn("r'[\\p{P}\\p{Z}\\p{S}]'", POD_SOURCE_SQL)
        self.assertIn("IF(\n                    NULLIF(m.isbn, '') IS NOT NULL", POD_SOURCE_SQL)

    def test_ambiguous_matches_are_not_silently_selected(self) -> None:
        self.assertIn("WHEN exact_isbn_match_count = 1 THEN master_product_code", POD_SOURCE_SQL)
        self.assertIn(
            "WHEN exact_isbn_match_count = 0 AND normalized_title_match_count = 1 THEN master_product_code",
            POD_SOURCE_SQL,
        )
        self.assertIn("ELSE ''", POD_SOURCE_SQL)

    def test_only_physical_master_rows_are_candidates(self) -> None:
        self.assertIn("p.electronic_publication_code IS NULL", POD_SOURCE_SQL)
        self.assertIn("NULLIF(p.normalized_base_isbn, '') IS NULL", POD_SOURCE_SQL)

    def test_missing_physical_isbn_uses_unique_title_base_isbn(self) -> None:
        self.assertIn("product_master_title_isbn", POD_SOURCE_SQL)
        self.assertIn("COUNT(DISTINCT normalized_base_isbn) = 1", POD_SOURCE_SQL)
        self.assertIn("COALESCE(NULLIF(p.normalized_isbn, ''), t.normalized_title_isbn)", POD_SOURCE_SQL)

    def test_blank_product_code_blocks_access_input_validation(self) -> None:
        self.assertIn("access_input_pod_sales_product_code_blank", ACCESS_QUALITY_SQL)
        self.assertIn("COUNTIF(NULLIF(TRIM(product_code), '') IS NULL)", ACCESS_QUALITY_SQL)


if __name__ == "__main__":
    unittest.main()
