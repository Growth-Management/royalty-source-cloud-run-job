#!/usr/bin/env python3
"""Diagnose column-level differences before publishing to ice_qb_source_p1.

This job is read-only with respect to the production rows. It loads the validated
asia-northeast1 cumulative snapshot into temporary US tables, compares each
column against the existing production month, writes diagnostics into
royalty_audit.production_publish_diff_log, and deletes the temporary tables.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from google.cloud import bigquery

from publish_to_ice_qb_source_p1 import (
    Comparison,
    compare_stage_to_target,
    get_promotion_gate,
    load_stage_table,
    query_dataframe,
    table_configs,
    validate_cumulative_snapshot,
    validate_target_month,
)


PROJECT_ID = os.getenv("GCP_PROJECT_ID", "ice-qb")
SOURCE_DATASET = os.getenv("BQ_CUMULATIVE_DATASET", "royalty_cumulative")
AUDIT_DATASET = os.getenv("BQ_AUDIT_DATASET", "royalty_audit")
TARGET_DATASET = os.getenv("PRODUCTION_TARGET_DATASET", "ice_qb_source_p1")
SOURCE_LOCATION = os.getenv("SOURCE_LOCATION", "asia-northeast1")
TARGET_LOCATION = os.getenv("TARGET_LOCATION", "US")
TARGET_MONTH = os.getenv("JOB_TARGET_MONTH", "")


def int_month_config(target_month: str) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(
                "target_month_int",
                "INT64",
                int(target_month),
            )
        ]
    )


def compare_projection(
    client: bigquery.Client,
    stage_id: str,
    target_id: str,
    month_column: str,
    target_month: str,
    location: str,
    select_expression: str,
) -> Comparison:
    sql = f"""
        WITH source_grouped AS (
            SELECT
                TO_JSON_STRING({select_expression.format(alias='s')}) AS row_json
                , COUNT(*) AS row_count
            FROM
                `{stage_id}` s
            GROUP BY
                row_json
        )
        , target_grouped AS (
            SELECT
                TO_JSON_STRING({select_expression.format(alias='t')}) AS row_json
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
            , (
                SELECT COUNT(*)
                FROM `{target_id}`
                WHERE {month_column} = @target_month_int
            ) AS target_rows
            , COUNTIF(COALESCE(s.row_count, 0) != COALESCE(t.row_count, 0)) AS mismatch_groups
            , COALESCE(
                SUM(GREATEST(COALESCE(s.row_count, 0) - COALESCE(t.row_count, 0), 0))
                , 0
            ) AS source_only_rows
            , COALESCE(
                SUM(GREATEST(COALESCE(t.row_count, 0) - COALESCE(s.row_count, 0), 0))
                , 0
            ) AS target_only_rows
        FROM
            source_grouped s
        FULL OUTER JOIN
            target_grouped t
            USING (row_json)
    """
    row = next(
        iter(
            client.query(
                sql,
                job_config=int_month_config(target_month),
                location=location,
            ).result()
        )
    )
    return Comparison(
        source_rows=int(row.source_rows),
        target_rows=int(row.target_rows),
        mismatch_groups=int(row.mismatch_groups),
        source_only_rows=int(row.source_only_rows),
        target_only_rows=int(row.target_only_rows),
    )


def compare_without_column(
    client: bigquery.Client,
    stage_id: str,
    target_id: str,
    month_column: str,
    target_month: str,
    location: str,
    column_name: str,
) -> Comparison:
    expression = f"(SELECT AS STRUCT {{alias}}.* EXCEPT (`{column_name}`))"
    return compare_projection(
        client,
        stage_id,
        target_id,
        month_column,
        target_month,
        location,
        expression,
    )


def compare_column_values(
    client: bigquery.Client,
    stage_id: str,
    target_id: str,
    month_column: str,
    target_month: str,
    location: str,
    column_name: str,
) -> Comparison:
    expression = f"{{alias}}.`{column_name}`"
    return compare_projection(
        client,
        stage_id,
        target_id,
        month_column,
        target_month,
        location,
        expression,
    )


def ensure_diff_audit_table(client: bigquery.Client) -> str:
    table_id = f"{PROJECT_ID}.{AUDIT_DATASET}.production_publish_diff_log"
    sql = f"""
        CREATE TABLE IF NOT EXISTS `{table_id}` (
            checked_at TIMESTAMP NOT NULL
            , target_month STRING NOT NULL
            , validation_github_run_id STRING NOT NULL
            , execution_name STRING
            , table_name STRING NOT NULL
            , column_name STRING NOT NULL
            , base_mismatch_groups INT64 NOT NULL
            , base_source_only_rows INT64 NOT NULL
            , base_target_only_rows INT64 NOT NULL
            , value_mismatch_groups INT64 NOT NULL
            , value_source_only_rows INT64 NOT NULL
            , value_target_only_rows INT64 NOT NULL
            , mismatch_groups_without_column INT64 NOT NULL
            , source_only_rows_without_column INT64 NOT NULL
            , target_only_rows_without_column INT64 NOT NULL
            , improvement_groups INT64 NOT NULL
            , single_column_explains_all BOOL NOT NULL
        )
        PARTITION BY DATE(checked_at)
        CLUSTER BY target_month, table_name, column_name
    """
    client.query(sql, location=SOURCE_LOCATION).result()
    return table_id


def write_diff_rows(
    client: bigquery.Client,
    table_id: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    dataframe = pd.DataFrame(rows)
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    client.load_table_from_dataframe(
        dataframe,
        table_id,
        job_config=job_config,
        location=SOURCE_LOCATION,
    ).result()


def main() -> None:
    validate_target_month(TARGET_MONTH)

    source_client = bigquery.Client(project=PROJECT_ID, location=SOURCE_LOCATION)
    target_client = bigquery.Client(project=PROJECT_ID, location=TARGET_LOCATION)
    audit_table = ensure_diff_audit_table(source_client)
    execution_name = os.getenv("CLOUD_RUN_EXECUTION") or os.getenv("K_REVISION") or ""

    gate = get_promotion_gate(
        source_client,
        PROJECT_ID,
        AUDIT_DATASET,
        SOURCE_LOCATION,
        TARGET_MONTH,
    )
    validate_cumulative_snapshot(
        source_client,
        PROJECT_ID,
        SOURCE_DATASET,
        SOURCE_LOCATION,
        TARGET_MONTH,
        gate,
    )

    configs = table_configs(PROJECT_ID, SOURCE_DATASET)
    stage_suffix = f"diag_{TARGET_MONTH}_{uuid.uuid4().hex[:10]}"
    stage_ids: dict[str, str] = {}
    diff_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    checked_at = datetime.now(timezone.utc)

    try:
        for config in configs:
            source_df = query_dataframe(
                source_client,
                config.source_sql,
                SOURCE_LOCATION,
                TARGET_MONTH,
            )
            stage_id = load_stage_table(
                target_client,
                source_df,
                PROJECT_ID,
                TARGET_DATASET,
                config.target_table,
                TARGET_LOCATION,
                stage_suffix,
            )
            stage_ids[config.key] = stage_id
            target_id = f"{PROJECT_ID}.{TARGET_DATASET}.{config.target_table}"
            base = compare_stage_to_target(
                target_client,
                stage_id,
                target_id,
                config.month_column,
                TARGET_MONTH,
                TARGET_LOCATION,
            )
            summary[config.key] = {"base": base.__dict__, "columns": {}}

            if base.matched:
                continue

            target_schema = target_client.get_table(target_id).schema
            for field in target_schema:
                column_name = field.name
                value_comparison = compare_column_values(
                    target_client,
                    stage_id,
                    target_id,
                    config.month_column,
                    TARGET_MONTH,
                    TARGET_LOCATION,
                    column_name,
                )
                without_comparison = compare_without_column(
                    target_client,
                    stage_id,
                    target_id,
                    config.month_column,
                    TARGET_MONTH,
                    TARGET_LOCATION,
                    column_name,
                )
                improvement = base.mismatch_groups - without_comparison.mismatch_groups
                row = {
                    "checked_at": checked_at,
                    "target_month": TARGET_MONTH,
                    "validation_github_run_id": gate["run_id"],
                    "execution_name": execution_name,
                    "table_name": config.key,
                    "column_name": column_name,
                    "base_mismatch_groups": base.mismatch_groups,
                    "base_source_only_rows": base.source_only_rows,
                    "base_target_only_rows": base.target_only_rows,
                    "value_mismatch_groups": value_comparison.mismatch_groups,
                    "value_source_only_rows": value_comparison.source_only_rows,
                    "value_target_only_rows": value_comparison.target_only_rows,
                    "mismatch_groups_without_column": without_comparison.mismatch_groups,
                    "source_only_rows_without_column": without_comparison.source_only_rows,
                    "target_only_rows_without_column": without_comparison.target_only_rows,
                    "improvement_groups": improvement,
                    "single_column_explains_all": without_comparison.matched,
                }
                diff_rows.append(row)
                summary[config.key]["columns"][column_name] = {
                    "value_mismatch_groups": value_comparison.mismatch_groups,
                    "mismatch_groups_without_column": without_comparison.mismatch_groups,
                    "improvement_groups": improvement,
                    "single_column_explains_all": without_comparison.matched,
                }

        write_diff_rows(source_client, audit_table, diff_rows)
        print(json.dumps(summary, ensure_ascii=False, default=str))
    finally:
        for stage_id in stage_ids.values():
            try:
                target_client.delete_table(stage_id, not_found_ok=True)
            except Exception as exc:
                print(f"failed to delete stage table {stage_id}: {exc}")


if __name__ == "__main__":
    main()
