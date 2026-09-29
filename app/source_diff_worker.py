"""Reusable multiset diff worker for monthly SOURCE publication.

The production tables do not expose a stable unique row key for every source kind.
For example, wholesale_sales_report contains legitimate duplicate business-key groups.
Therefore Phase 1 uses exact-row multiset comparison as the safe baseline:

- UNCHANGED: identical row occurrences present on both sides.
- INSERT: row occurrences present only in the validated source snapshot.
- DELETE_CANDIDATE: row occurrences present only in production.
- UPDATE: intentionally 0 until a source kind has a verified unique business key.

DELETE_CANDIDATE is a review state, not an automatic delete instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Any


@dataclass(frozen=True)
class DiffSummary:
    source_rows: int
    target_rows: int
    mismatch_groups: int
    unchanged_rows: int
    insert_rows: int
    delete_candidate_rows: int
    update_rows: int = 0

    @property
    def source_only_rows(self) -> int:
        """Backward-compatible alias used by the original production publisher."""
        return self.insert_rows

    @property
    def target_only_rows(self) -> int:
        """Backward-compatible alias used by the original production publisher."""
        return self.delete_candidate_rows

    @property
    def matched(self) -> bool:
        return (
            self.source_rows == self.target_rows
            and self.mismatch_groups == 0
            and self.insert_rows == 0
            and self.delete_candidate_rows == 0
            and self.update_rows == 0
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "source_rows": self.source_rows,
            "target_rows": self.target_rows,
            "mismatch_groups": self.mismatch_groups,
            "unchanged_rows": self.unchanged_rows,
            "insert_rows": self.insert_rows,
            "delete_candidate_rows": self.delete_candidate_rows,
            "update_rows": self.update_rows,
        }


def summarize_multiset_counts(count_pairs: Iterable[tuple[int, int]]) -> DiffSummary:
    """Summarize pairs of (source_count, target_count) for exact row payloads."""
    source_rows = 0
    target_rows = 0
    mismatch_groups = 0
    unchanged_rows = 0
    insert_rows = 0
    delete_candidate_rows = 0

    for source_count, target_count in count_pairs:
        source_count = int(source_count or 0)
        target_count = int(target_count or 0)
        source_rows += source_count
        target_rows += target_count
        unchanged_rows += min(source_count, target_count)
        insert_rows += max(source_count - target_count, 0)
        delete_candidate_rows += max(target_count - source_count, 0)
        if source_count != target_count:
            mismatch_groups += 1

    return DiffSummary(
        source_rows=source_rows,
        target_rows=target_rows,
        mismatch_groups=mismatch_groups,
        unchanged_rows=unchanged_rows,
        insert_rows=insert_rows,
        delete_candidate_rows=delete_candidate_rows,
        update_rows=0,
    )


def compare_stage_to_target(
    client: Any,
    stage_id: str,
    target_id: str,
    month_column: str,
    target_month: str,
    location: str,
) -> DiffSummary:
    """Compare a staged monthly snapshot with production as an exact-row multiset."""
    from google.cloud import bigquery

    sql = f"""
        WITH source_grouped AS (
            SELECT
                TO_JSON_STRING(s) AS row_json
                , COUNT(*) AS row_count
            FROM
                `{stage_id}` s
            GROUP BY
                row_json
        )
        , target_grouped AS (
            SELECT
                TO_JSON_STRING(t) AS row_json
                , COUNT(*) AS row_count
            FROM
                `{target_id}` t
            WHERE
                {month_column} = @target_month_int
            GROUP BY
                row_json
        )
        SELECT
            (SELECT COUNT(*) FROM `{stage_id}`) AS source_rows
            , (SELECT COUNT(*) FROM `{target_id}` WHERE {month_column} = @target_month_int) AS target_rows
            , COUNTIF(COALESCE(s.row_count, 0) != COALESCE(t.row_count, 0)) AS mismatch_groups
            , COALESCE(SUM(LEAST(COALESCE(s.row_count, 0), COALESCE(t.row_count, 0))), 0) AS unchanged_rows
            , COALESCE(SUM(GREATEST(COALESCE(s.row_count, 0) - COALESCE(t.row_count, 0), 0)), 0) AS insert_rows
            , COALESCE(SUM(GREATEST(COALESCE(t.row_count, 0) - COALESCE(s.row_count, 0), 0)), 0) AS delete_candidate_rows
        FROM
            source_grouped s
        FULL OUTER JOIN
            target_grouped t
            USING (row_json)
    """
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_month_int", "INT64", int(target_month))
        ]
    )
    row = next(iter(client.query(sql, job_config=config, location=location).result()))
    return DiffSummary(
        source_rows=int(row.source_rows),
        target_rows=int(row.target_rows),
        mismatch_groups=int(row.mismatch_groups),
        unchanged_rows=int(row.unchanged_rows),
        insert_rows=int(row.insert_rows),
        delete_candidate_rows=int(row.delete_candidate_rows),
        update_rows=0,
    )


def ensure_delete_candidates_safe(
    summaries: Mapping[str, DiffSummary],
    *,
    allow_delete_candidates: bool = False,
) -> None:
    """Block apply when production-only rows exist unless explicitly overridden."""
    if allow_delete_candidates:
        return

    blocked = {
        key: summary.delete_candidate_rows
        for key, summary in summaries.items()
        if summary.delete_candidate_rows > 0
    }
    if blocked:
        detail = ", ".join(f"{key}={count}" for key, count in sorted(blocked.items()))
        raise RuntimeError(
            "DELETE_CANDIDATE detected; apply is blocked until the deletion is reviewed: "
            + detail
        )
