"""Reusable multiset diff worker for monthly SOURCE publication.

The production tables do not expose a stable unique row key for every source kind.
For example, wholesale_sales_report contains legitimate duplicate business-key groups.
Therefore Phase 1 uses exact-row multiset comparison as the safe baseline:

- UNCHANGED: identical row occurrences present on both sides.
- INSERT: row occurrences present only in the validated source snapshot.
- DELETE_CANDIDATE: row occurrences present only in production.
- UPDATE: intentionally 0 until a source kind has a verified unique business key.

DELETE_CANDIDATE is a review state, not an automatic delete instruction.

This module performs no outbound communication by itself. compare_stage_to_target()
sends one read-only query to BigQuery through the client passed in by the caller.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

# project.dataset.table, each part restricted to BigQuery-safe characters.
_TABLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
_COLUMN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


@dataclass(frozen=True)
class DeleteCandidateApproval:
    """Reviewed approval for production-only rows.

    The approval pins the exact per-table DELETE_CANDIDATE counts that a human reviewed.
    If the dry-run counts differ at apply time, the gate still blocks.
    """

    target_month: str
    approved_by: str
    approved_at: datetime
    validation_run_id: str
    delete_candidate_rows: Mapping[str, int] = field(default_factory=dict)

    def validate(self, target_month: str) -> None:
        if not self.approved_by.strip():
            raise ValueError("DeleteCandidateApproval.approved_by is required")
        if not self.validation_run_id.strip():
            raise ValueError("DeleteCandidateApproval.validation_run_id is required")
        if self.target_month != target_month:
            raise ValueError(
                f"DeleteCandidateApproval target_month mismatch: "
                f"approved={self.target_month}, actual={target_month}"
            )


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


def _row_payload(row: Mapping[str, Any]) -> str:
    # Mirrors TO_JSON_STRING(row): column order is preserved and NULL becomes null.
    return json.dumps(dict(row), ensure_ascii=False, default=str)


def diff_row_multisets(
    source_rows: Iterable[Mapping[str, Any]],
    target_rows: Iterable[Mapping[str, Any]],
) -> DiffSummary:
    """Pure-Python equivalent of compare_stage_to_target() for local/dummy data checks."""
    source_counts = Counter(_row_payload(row) for row in source_rows)
    target_counts = Counter(_row_payload(row) for row in target_rows)
    keys = source_counts.keys() | target_counts.keys()
    return summarize_multiset_counts(
        (source_counts.get(key, 0), target_counts.get(key, 0)) for key in keys
    )


def _require_table_id(value: str) -> str:
    if not _TABLE_ID_PATTERN.match(value):
        raise ValueError(f"invalid BigQuery table id: {value!r}")
    return value


def _require_column(value: str) -> str:
    if not _COLUMN_PATTERN.match(value):
        raise ValueError(f"invalid column name: {value!r}")
    return value


def build_compare_sql(stage_id: str, target_id: str, month_column: str) -> str:
    """Build the multiset comparison SQL.

    The stage table must be created from the target table schema (same columns in the
    same order) so that TO_JSON_STRING payloads are comparable. The production publisher
    guarantees this by creating the stage with target.schema.
    """
    stage_id = _require_table_id(stage_id)
    target_id = _require_table_id(target_id)
    month_column = _require_column(month_column)
    return f"""
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

    sql = build_compare_sql(stage_id, target_id, month_column)
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
    target_month: str | None = None,
    approval: DeleteCandidateApproval | None = None,
) -> None:
    """Block apply when production-only rows exist.

    A reviewed flow may pass ``approval``. It only passes when every table's
    DELETE_CANDIDATE count exactly equals the reviewed count, so a changed dry-run
    result is blocked again.
    """
    blocked = {
        key: summary.delete_candidate_rows
        for key, summary in summaries.items()
        if summary.delete_candidate_rows > 0
    }
    if not blocked:
        return

    detail = ", ".join(f"{key}={count}" for key, count in sorted(blocked.items()))
    if approval is not None:
        if target_month is None:
            raise ValueError("target_month is required when an approval is supplied")
        approval.validate(target_month)
        approved = {
            key: int(count)
            for key, count in approval.delete_candidate_rows.items()
            if int(count) > 0
        }
        if approved == blocked:
            return
        approved_detail = ", ".join(f"{key}={count}" for key, count in sorted(approved.items()))
        raise RuntimeError(
            "DELETE_CANDIDATE counts differ from the reviewed approval; apply is blocked: "
            f"actual [{detail}], approved [{approved_detail}]"
        )

    raise RuntimeError(
        "DELETE_CANDIDATE detected; apply is blocked until the deletion is reviewed: "
        + detail
    )
