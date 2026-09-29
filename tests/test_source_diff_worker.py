from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.source_diff_worker import (
    DeleteCandidateApproval,
    DiffSummary,
    build_compare_sql,
    diff_row_multisets,
    ensure_delete_candidates_safe,
    summarize_multiset_counts,
)


def _row(product_code: str, quantity: int | None = 1, digital_pub_code: str | None = "D001") -> dict:
    return {
        "year_month": 202608,
        "product_code": product_code,
        "digital_pub_code": digital_pub_code,
        "sales_quantity": quantity,
    }


class SummarizeMultisetCountsTest(unittest.TestCase):
    def test_case1_exact_match(self) -> None:
        result = summarize_multiset_counts([(3, 3), (1, 1)])
        self.assertEqual(result.source_rows, 4)
        self.assertEqual(result.target_rows, 4)
        self.assertEqual(result.unchanged_rows, 4)
        self.assertEqual(result.insert_rows, 0)
        self.assertEqual(result.delete_candidate_rows, 0)
        self.assertEqual(result.update_rows, 0)
        self.assertEqual(result.mismatch_groups, 0)
        self.assertTrue(result.matched)

    def test_case2_source_only_row_is_insert(self) -> None:
        result = summarize_multiset_counts([(2, 2), (1, 0)])
        self.assertEqual(result.unchanged_rows, 2)
        self.assertEqual(result.insert_rows, 1)
        self.assertEqual(result.delete_candidate_rows, 0)
        self.assertEqual(result.mismatch_groups, 1)
        self.assertFalse(result.matched)

    def test_case3_target_only_row_is_delete_candidate(self) -> None:
        result = summarize_multiset_counts([(2, 2), (0, 1)])
        self.assertEqual(result.unchanged_rows, 2)
        self.assertEqual(result.insert_rows, 0)
        self.assertEqual(result.delete_candidate_rows, 1)
        self.assertEqual(result.mismatch_groups, 1)
        self.assertFalse(result.matched)

    def test_case4_duplicate_rows_source5_target3(self) -> None:
        result = summarize_multiset_counts([(5, 3)])
        self.assertEqual(result.unchanged_rows, 3)
        self.assertEqual(result.insert_rows, 2)
        self.assertEqual(result.delete_candidate_rows, 0)
        self.assertEqual(result.mismatch_groups, 1)

    def test_case5_duplicate_rows_source1_target4(self) -> None:
        result = summarize_multiset_counts([(1, 4)])
        self.assertEqual(result.unchanged_rows, 1)
        self.assertEqual(result.insert_rows, 0)
        self.assertEqual(result.delete_candidate_rows, 3)
        self.assertEqual(result.mismatch_groups, 1)

    def test_case6_multiple_diff_groups(self) -> None:
        result = summarize_multiset_counts([(3, 1), (1, 4), (2, 2), (0, 2), (1, 0)])
        self.assertEqual(result.source_rows, 7)
        self.assertEqual(result.target_rows, 9)
        self.assertEqual(result.unchanged_rows, 4)
        self.assertEqual(result.insert_rows, 3)
        self.assertEqual(result.delete_candidate_rows, 5)
        self.assertEqual(result.mismatch_groups, 4)
        # Invariants: both sides decompose into unchanged + one-sided occurrences.
        self.assertEqual(result.source_rows, result.unchanged_rows + result.insert_rows)
        self.assertEqual(result.target_rows, result.unchanged_rows + result.delete_candidate_rows)

    def test_empty_input(self) -> None:
        result = summarize_multiset_counts([])
        self.assertTrue(result.matched)
        self.assertEqual(result.as_dict()["source_rows"], 0)

    def test_none_counts_are_zero(self) -> None:
        # FULL OUTER JOIN leaves one side NULL.
        result = summarize_multiset_counts([(None, 2), (1, None)])
        self.assertEqual(result.insert_rows, 1)
        self.assertEqual(result.delete_candidate_rows, 2)


class DiffSummaryTest(unittest.TestCase):
    def test_case10_matched(self) -> None:
        self.assertTrue(DiffSummary(5, 5, 0, 5, 0, 0).matched)
        self.assertFalse(DiffSummary(5, 5, 2, 4, 1, 1).matched)  # same count, different rows
        self.assertFalse(DiffSummary(6, 5, 1, 5, 1, 0).matched)
        self.assertFalse(DiffSummary(5, 5, 0, 5, 0, 0, update_rows=1).matched)

    def test_case11_as_dict(self) -> None:
        self.assertEqual(
            DiffSummary(10, 9, 2, 8, 2, 1).as_dict(),
            {
                "source_rows": 10,
                "target_rows": 9,
                "mismatch_groups": 2,
                "unchanged_rows": 8,
                "insert_rows": 2,
                "delete_candidate_rows": 1,
                "update_rows": 0,
            },
        )

    def test_backward_compatible_aliases(self) -> None:
        summary = DiffSummary(10, 9, 2, 8, 2, 1)
        self.assertEqual(summary.source_only_rows, 2)
        self.assertEqual(summary.target_only_rows, 1)


class DiffRowMultisetsTest(unittest.TestCase):
    def test_exact_rows_with_duplicates(self) -> None:
        source = [_row("A")] * 5 + [_row("B")]
        target = [_row("A")] * 3 + [_row("B")] + [_row("C")]
        result = diff_row_multisets(source, target)
        self.assertEqual(result.unchanged_rows, 4)
        self.assertEqual(result.insert_rows, 2)
        self.assertEqual(result.delete_candidate_rows, 1)
        self.assertEqual(result.mismatch_groups, 2)

    def test_value_change_is_insert_plus_delete_candidate_not_update(self) -> None:
        result = diff_row_multisets([_row("A", quantity=2)], [_row("A", quantity=1)])
        self.assertEqual(result.insert_rows, 1)
        self.assertEqual(result.delete_candidate_rows, 1)
        self.assertEqual(result.update_rows, 0)

    def test_case12_null_values(self) -> None:
        # NULL matches NULL (TO_JSON_STRING renders both as null) ...
        same = diff_row_multisets(
            [_row("A", quantity=None, digital_pub_code=None)],
            [_row("A", quantity=None, digital_pub_code=None)],
        )
        self.assertTrue(same.matched)
        # ... but NULL is distinct from '', '#N/A' and 0.
        for other in ("", "#N/A"):
            result = diff_row_multisets(
                [_row("A", digital_pub_code=None)], [_row("A", digital_pub_code=other)]
            )
            self.assertEqual((result.insert_rows, result.delete_candidate_rows), (1, 1), other)
        zero = diff_row_multisets([_row("A", quantity=None)], [_row("A", quantity=0)])
        self.assertEqual((zero.insert_rows, zero.delete_candidate_rows), (1, 1))


class BuildCompareSqlTest(unittest.TestCase):
    def test_sql_uses_parameter_for_month_and_multiset_columns(self) -> None:
        sql = build_compare_sql(
            "ice-qb.ice_qb_source_p1._stg_prod_publish_x_202608_abc",
            "ice-qb.ice_qb_source_p1.wholesale_sales_report",
            "year_month",
        )
        self.assertIn("year_month = @target_month_int", sql)
        self.assertIn("TO_JSON_STRING(s)", sql)
        self.assertIn("FULL OUTER JOIN", sql)
        self.assertIn("AS unchanged_rows", sql)
        self.assertIn("AS delete_candidate_rows", sql)

    def test_rejects_unsafe_identifiers(self) -> None:
        good = "ice-qb.ice_qb_source_p1.wholesale_sales_report"
        for bad_table in ("x` WHERE 1=1 --", "ice-qb.ds", "ice-qb.ds.t; DROP TABLE t"):
            with self.assertRaises(ValueError):
                build_compare_sql(bad_table, good, "year_month")
            with self.assertRaises(ValueError):
                build_compare_sql(good, bad_table, "year_month")
        for bad_column in ("year_month OR 1=1", "1col", "year-month", ""):
            with self.assertRaises(ValueError):
                build_compare_sql(good, good, bad_column)


class DeleteCandidateGateTest(unittest.TestCase):
    def _approval(self, **overrides) -> DeleteCandidateApproval:
        values = {
            "target_month": "202608",
            "approved_by": "reviewer@example.com",
            "approved_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "validation_run_id": "123456",
            "delete_candidate_rows": {"sales": 2},
        }
        values.update(overrides)
        return DeleteCandidateApproval(**values)

    def test_case7_gate_passes_without_delete_candidates(self) -> None:
        ensure_delete_candidates_safe(
            {
                "sales": DiffSummary(10, 8, 2, 8, 2, 0),
                "store": DiffSummary(5, 5, 0, 5, 0, 0),
            }
        )

    def test_case8_gate_blocks_delete_candidates(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "DELETE_CANDIDATE detected.*sales=2"):
            ensure_delete_candidates_safe(
                {
                    "sales": DiffSummary(8, 10, 2, 8, 0, 2),
                    "store": DiffSummary(5, 5, 0, 5, 0, 0),
                }
            )

    def test_case9_only_explicit_matching_approval_passes(self) -> None:
        summaries = {"sales": DiffSummary(8, 10, 2, 8, 0, 2), "pod": DiffSummary(1, 1, 0, 1, 0, 0)}
        ensure_delete_candidates_safe(summaries, target_month="202608", approval=self._approval())

    def test_approval_with_different_counts_blocks(self) -> None:
        summaries = {"sales": DiffSummary(8, 11, 2, 8, 0, 3)}
        with self.assertRaisesRegex(RuntimeError, "differ from the reviewed approval"):
            ensure_delete_candidates_safe(summaries, target_month="202608", approval=self._approval())

    def test_approval_missing_table_blocks(self) -> None:
        summaries = {
            "sales": DiffSummary(8, 10, 2, 8, 0, 2),
            "store": DiffSummary(4, 5, 1, 4, 0, 1),
        }
        with self.assertRaises(RuntimeError):
            ensure_delete_candidates_safe(summaries, target_month="202608", approval=self._approval())

    def test_approval_requires_identity_and_month(self) -> None:
        summaries = {"sales": DiffSummary(8, 10, 2, 8, 0, 2)}
        with self.assertRaises(ValueError):
            ensure_delete_candidates_safe(summaries, approval=self._approval())
        with self.assertRaises(ValueError):
            ensure_delete_candidates_safe(
                summaries, target_month="202609", approval=self._approval()
            )
        with self.assertRaises(ValueError):
            ensure_delete_candidates_safe(
                summaries, target_month="202608", approval=self._approval(approved_by=" ")
            )
        with self.assertRaises(ValueError):
            ensure_delete_candidates_safe(
                summaries, target_month="202608", approval=self._approval(validation_run_id="")
            )

    def test_boolean_override_is_not_supported(self) -> None:
        with self.assertRaises(TypeError):
            ensure_delete_candidates_safe(  # type: ignore[call-arg]
                {"sales": DiffSummary(8, 10, 2, 8, 0, 2)}, allow_delete_candidates=True
            )


if __name__ == "__main__":
    unittest.main()
