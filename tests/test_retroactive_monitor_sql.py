from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = REPO_ROOT / "sql" / "audit" / "retroactive_master_update_candidates.sql"


class RetroactiveMonitorSqlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = SQL_PATH.read_text(encoding="utf-8")

    def test_uses_production_source_tables(self) -> None:
        self.assertIn(
            "`{{ project_id }}.{{ production_dataset }}.wholesale_sales_report`",
            self.sql,
        )
        self.assertIn(
            "`{{ project_id }}.{{ production_dataset }}.catalog_bibliographic_master`",
            self.sql,
        )
        self.assertIn(
            "`{{ project_id }}.{{ production_dataset }}.author_condition_list`",
            self.sql,
        )

    def test_does_not_use_incomplete_cumulative_or_pipeline_source_as_baseline(self) -> None:
        self.assertNotIn("{{ cumulative_dataset }}", self.sql)
        self.assertNotIn("{{ source_dataset }}.source_product_master", self.sql)
        self.assertNotIn("{{ source_dataset }}.source_author_conditions", self.sql)
        self.assertNotIn("source_author_conditions_ext", self.sql)

    def test_is_read_only(self) -> None:
        executable_lines = [
            line.strip().upper()
            for line in self.sql.splitlines()
            if line.strip() and not line.lstrip().startswith("--")
        ]
        joined = "\n".join(executable_lines)
        for keyword in ("INSERT INTO", "UPDATE ", "DELETE FROM", "MERGE ", "CREATE ", "ALTER ", "DROP "):
            self.assertNotIn(keyword, joined)

    def test_expected_filters_are_present(self) -> None:
        self.assertIn("digital_pub_code = '#N/A'", self.sql)
        self.assertIn("a.author_condition_rows > 0", self.sql)
        self.assertIn("{{ from_month }}", self.sql)
        self.assertIn("{{ to_month }}", self.sql)


if __name__ == "__main__":
    unittest.main()
