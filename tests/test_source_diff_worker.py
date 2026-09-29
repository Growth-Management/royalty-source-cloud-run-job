from __future__ import annotations

import unittest

from app.source_diff_worker import (
    DiffSummary,
    ensure_delete_candidates_safe,
    summarize_multiset_counts,
)


class SummarizeMultisetCountsTest(unittest.TestCase):
    def test_exact_match(self) -> None:
        result = summarize_multiset_counts([(3, 3), (1, 1)])
        self.assertEqual(result.source_rows, 4)
        self.assertEqual(result.target_rows, 4)
        self.assertEqual(result.unchanged_rows, 4)
        self.assertEqual(result.insert_rows, 0)
        self.assertEqual(result.delete_candidate_rows, 0)
        self.assertEqual(result.update_rows, 0)
        self.assertTrue(result.matched)

    def test_insert_and_delete_candidate(self) -> None:
        result = summarize_multiset_counts([(3, 1), (1, 4), (2, 2)])
        self.assertEqual(result.source_rows, 6)
        self.assertEqual(result.target_rows, 7)
        self.assertEqual(result.unchanged_rows, 4)
        self.assertEqual(result.insert_rows, 2)
        self.assertEqual(result.delete_candidate_rows, 3)
        self.assertEqual(result.mismatch_groups, 2)
        self.assertFalse(result.matched)

    def test_duplicate_occurrences_are_counted_as_multiset(self) -> None:
        result = summarize_multiset_counts([(5, 3)])
        self.assertEqual(result.unchanged_rows, 3)
        self.assertEqual(result.insert_rows, 2)
        self.assertEqual(result.delete_candidate_rows, 0)


class DeleteCandidateGateTest(unittest.TestCase):
    def test_gate_allows_no_delete_candidates(self) -> None:
        ensure_delete_candidates_safe(
            {
                "sales": DiffSummary(10, 8, 2, 8, 2, 0),
                "store": DiffSummary(5, 5, 0, 5, 0, 0),
            }
        )

    def test_gate_blocks_delete_candidates(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "DELETE_CANDIDATE"):
            ensure_delete_candidates_safe(
                {
                    "sales": DiffSummary(8, 10, 2, 8, 0, 2),
                }
            )

    def test_explicit_override_is_supported_for_future_approved_flow(self) -> None:
        ensure_delete_candidates_safe(
            {"sales": DiffSummary(8, 10, 2, 8, 0, 2)},
            allow_delete_candidates=True,
        )


if __name__ == "__main__":
    unittest.main()
