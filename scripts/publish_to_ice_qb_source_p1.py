#!/usr/bin/env python3
"""Publish a validated monthly snapshot into ice_qb_source_p1.

The validated cumulative dataset is in asia-northeast1 while ice_qb_source_p1
is in US. The job transfers mapped rows through the client into temporary US
tables, compares them as multisets, and when apply=true performs one US
transaction across the three production tables.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from google.cloud import bigquery


@dataclass(frozen=True)
class TableConfig:
    key: str
    target_table: str
    month_column: str
    source_sql: str


@dataclass(frozen=True)
class Comparison:
    source_rows: int
    target_rows: int
    mismatch_groups: int
    source_only_rows: int
    target_only_rows: int

    @property
    def matched(self) -> bool:
        return (
            self.source_rows == self.target_rows
            and self.mismatch_groups == 0
            and self.source_only_rows == 0
            and self.target_only_rows == 0
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", default=os.getenv("GCP_PROJECT_ID", "ice-qb"))
    parser.add_argument("--source-dataset", default=os.getenv("BQ_CUMULATIVE_DATASET", "royalty_cumulative"))
    parser.add_argument("--audit-dataset", default=os.getenv("BQ_AUDIT_DATASET", "royalty_audit"))
    parser.add_argument("--target-dataset", default=os.getenv("PRODUCTION_TARGET_DATASET", "ice_qb_source_p1"))
    parser.add_argument("--source-location", default=os.getenv("SOURCE_LOCATION", "asia-northeast1"))
    parser.add_argument("--target-location", default=os.getenv("TARGET_LOCATION", "US"))
    parser.add_argument("--target-month", default=os.getenv("JOB_TARGET_MONTH", ""))
    parser.add_argument(
        "--apply",
        action="store_true",
        default=os.getenv("PRODUCTION_PUBLISH_APPLY", "false").lower() == "true",
    )
    return parser.parse_args()


def validate_target_month(target_month: str) -> None:
    if len(target_month) != 6 or not target_month.isdigit():
        raise ValueError("target_month must use YYYYMM format")
    month = int(target_month[4:6])
    if month < 1 or month > 12:
        raise ValueError(f"invalid target month: {target_month}")


def table_configs(project_id: str, source_dataset: str) -> list[TableConfig]:
    return [
        TableConfig(
            key="sales",
            target_table="wholesale_sales_report",
            month_column="year_month",
            source_sql=f"""
                SELECT
                    CAST(accounting_month_1 AS INT64) AS year_month
                    , CAST(accounting_month_2 AS INT64) AS dl_year_month
                    , billing_code_1 AS billing_code
                    , billing_name_1 AS billing_name
                    , billing_code_2 AS wholesale_code
                    , billing_name_2 AS wholesale_name
                    , product_code
                    , product_key AS contents_code
                    , electronic_publication_code AS digital_pub_code
                    , title
                    , CAST(list_price_1 AS INT64) AS base_price
                    , exchange_rate_1 AS rate
                    , list_price_2 AS unit_price
                    , sales_quantity_1 AS sales_quantity
                    , sales_amount_1 AS license_fee
                    , list_price_3 AS sales_unit_price
                    , sales_quantity_2 AS dl_quantity
                    , list_price_4 AS sales_unit_price_amount
                    , sales_amount_2 AS net_amount
                    , exchange_rate_2 AS difference_amount
                FROM
                    `{project_id}.{source_dataset}.sales`
                WHERE
                    target_month = @target_month
            """,
        ),
        TableConfig(
            key="store",
            target_table="bookstore_dl_quantity",
            month_column="year_month",
            source_sql=f"""
                SELECT
                    CAST(accounting_month_1 AS INT64) AS year_month
                    , billing_code
                    , billing_name AS publisher_name
                    , electronic_publication_code AS digital_pub_code
                    , title
                    , CAST(list_price AS INT64) AS base_price
                    , CAST(accounting_month_2 AS INT64) AS dl_year_month
                    , store_name AS bookstore_name
                    , CAST(sales_quantity AS INT64) AS sales_quantity
                FROM
                    `{project_id}.{source_dataset}.store_detail`
                WHERE
                    target_month = @target_month
            """,
        ),
        TableConfig(
            key="pod",
            target_table="pod_sales_summary",
            month_column="year_month",
            source_sql=f"""
                SELECT
                    publisher AS publisher_name
                    , product_code
                    , isbn
                    , title
                    , unit_price
                    , rate AS discount_rate
                    , quantity AS sales_quantity
                    , net_amount
                    , tax AS tax_amount
                    , sales_amount
                    , manufacturing_cost
                    , factor_15 AS printing_cost
                    , factor_108 AS binding_cost
                    , pages AS page_count
                    , CAST(dl_month AS INT64) AS dl_year_month
                    , CAST(sales_month AS INT64) AS year_month
                FROM
                    `{project_id}.{source_dataset}.pod_sales`
                WHERE
                    target_month = @target_month
            """,
        ),
    ]


def month_query_config(target_month: str) -> bigquery.QueryJobConfig:
    return bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("target_month", "STRING", target_month)]
    )


def query_dataframe(
    client: bigquery.Client,
    sql: str,
    location: str,
    target_month: str,
) -> pd.DataFrame:
    iterator = client.query(
        sql,
        job_config=month_query_config(target_month),
        location=location,
    ).result()
    columns = [field.name for field in iterator.schema]
    return pd.DataFrame.from_records([dict(row.items()) for row in iterator], columns=columns)


def ensure_audit_table(
    client: bigquery.Client,
    project_id: str,
    audit_dataset: str,
    location: str,
) -> str:
    table_id = f"{project_id}.{audit_dataset}.production_publish_log"
    sql = f"""
        CREATE TABLE IF NOT EXISTS `{table_id}` (
            published_at TIMESTAMP NOT NULL
            , target_month STRING NOT NULL
            , validation_github_run_id STRING
            , cumulative_promoted_at TIMESTAMP
            , apply_requested BOOL NOT NULL
            , status STRING NOT NULL
            , execution_name STRING
            , source_sales_rows INT64
            , source_store_rows INT64
            , source_pod_rows INT64
            , target_before_sales_rows INT64
            , target_before_store_rows INT64
            , target_before_pod_rows INT64
            , target_after_sales_rows INT64
            , target_after_store_rows INT64
            , target_after_pod_rows INT64
            , sales_mismatch_groups_before INT64
            , store_mismatch_groups_before INT64
            , pod_mismatch_groups_before INT64
            , error_message STRING
        )
        PARTITION BY DATE(published_at)
        CLUSTER BY target_month, status
    """
    client.query(sql, location=location).result()
    return table_id


def get_promotion_gate(
    client: bigquery.Client,
    project_id: str,
    audit_dataset: str,
    location: str,
    target_month: str,
) -> dict[str, Any]:
    sql = f"""
        SELECT
            validation_github_run_id
            , promoted_at
            , sales_rows
            , store_rows
            , pod_rows
        FROM
            `{project_id}.{audit_dataset}.cumulative_promotion_log`
        WHERE
            target_month = @target_month
            AND status = 'SUCCESS'
        ORDER BY
            promoted_at DESC
        LIMIT 1
    """
    rows = list(
        client.query(
            sql,
            job_config=month_query_config(target_month),
            location=location,
        ).result()
    )
    if not rows:
        raise RuntimeError(f"{target_month} has not been promoted to royalty_cumulative")
    row = rows[0]
    return {
        "run_id": str(row.validation_github_run_id),
        "promoted_at": row.promoted_at,
        "sales_rows": int(row.sales_rows or 0),
        "store_rows": int(row.store_rows or 0),
        "pod_rows": int(row.pod_rows or 0),
    }


def validate_cumulative_snapshot(
    client: bigquery.Client,
    project_id: str,
    source_dataset: str,
    location: str,
    target_month: str,
    gate: dict[str, Any],
) -> None:
    for table, expected_count in (
        ("sales", gate["sales_rows"]),
        ("store_detail", gate["store_rows"]),
        ("pod_sales", gate["pod_rows"]),
    ):
        sql = f"""
            SELECT
                COUNT(*) AS row_count
                , COUNT(DISTINCT validation_github_run_id) AS run_id_count
                , ANY_VALUE(validation_github_run_id) AS run_id
            FROM
                `{project_id}.{source_dataset}.{table}`
            WHERE
                target_month = @target_month
        """
        row = next(
            iter(
                client.query(
                    sql,
                    job_config=month_query_config(target_month),
                    location=location,
                ).result()
            )
        )
        row_count = int(row.row_count)
        run_id_count = int(row.run_id_count)
        run_id = str(row.run_id or "")
        if row_count != expected_count:
            raise RuntimeError(
                f"cumulative row count mismatch: table={table}, "
                f"expected={expected_count}, actual={row_count}"
            )
        if row_count > 0 and (run_id_count != 1 or run_id != gate["run_id"]):
            raise RuntimeError(
                f"cumulative validation run mismatch: table={table}, "
                f"expected={gate['run_id']}, actual={run_id}, distinct={run_id_count}"
            )


def load_stage_table(
    client: bigquery.Client,
    dataframe: pd.DataFrame,
    project_id: str,
    target_dataset: str,
    target_table: str,
    location: str,
    stage_suffix: str,
) -> str:
    target_id = f"{project_id}.{target_dataset}.{target_table}"
    target = client.get_table(target_id)
    stage_id = f"{project_id}.{target_dataset}._stg_prod_publish_{target_table}_{stage_suffix}"
    client.delete_table(stage_id, not_found_ok=True)
    client.create_table(bigquery.Table(stage_id, schema=target.schema))
    if not dataframe.empty:
        job_config = bigquery.LoadJobConfig(
            schema=target.schema,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        client.load_table_from_dataframe(
            dataframe,
            stage_id,
            job_config=job_config,
            location=location,
        ).result()
    return stage_id


def compare_stage_to_target(
    client: bigquery.Client,
    stage_id: str,
    target_id: str,
    month_column: str,
    target_month: str,
    location: str,
) -> Comparison:
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
            , COALESCE(SUM(GREATEST(COALESCE(s.row_count, 0) - COALESCE(t.row_count, 0), 0)), 0) AS source_only_rows
            , COALESCE(SUM(GREATEST(COALESCE(t.row_count, 0) - COALESCE(s.row_count, 0), 0)), 0) AS target_only_rows
        FROM
            source_grouped s
        FULL OUTER JOIN
            target_grouped t
            USING (row_json)
    """
    config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("target_month_int", "INT64", int(target_month))]
    )
    row = next(iter(client.query(sql, job_config=config, location=location).result()))
    return Comparison(
        source_rows=int(row.source_rows),
        target_rows=int(row.target_rows),
        mismatch_groups=int(row.mismatch_groups),
        source_only_rows=int(row.source_only_rows),
        target_only_rows=int(row.target_only_rows),
    )


def apply_transaction(
    client: bigquery.Client,
    project_id: str,
    target_dataset: str,
    configs: list[TableConfig],
    stage_ids: dict[str, str],
    target_month: str,
    location: str,
) -> None:
    statements = ["BEGIN TRANSACTION;"]
    for config in configs:
        target_id = f"{project_id}.{target_dataset}.{config.target_table}"
        statements.extend(
            [
                f"DELETE FROM `{target_id}` WHERE {config.month_column} = @target_month_int;",
                f"INSERT INTO `{target_id}` SELECT * FROM `{stage_ids[config.key]}`;",
            ]
        )
    statements.append("COMMIT TRANSACTION;")
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("target_month_int", "INT64", int(target_month))]
    )
    client.query("\n".join(statements), job_config=job_config, location=location).result()


def write_audit(
    client: bigquery.Client,
    table_id: str,
    location: str,
    payload: dict[str, Any],
) -> None:
    columns = [
        "published_at",
        "target_month",
        "validation_github_run_id",
        "cumulative_promoted_at",
        "apply_requested",
        "status",
        "execution_name",
        "source_sales_rows",
        "source_store_rows",
        "source_pod_rows",
        "target_before_sales_rows",
        "target_before_store_rows",
        "target_before_pod_rows",
        "target_after_sales_rows",
        "target_after_store_rows",
        "target_after_pod_rows",
        "sales_mismatch_groups_before",
        "store_mismatch_groups_before",
        "pod_mismatch_groups_before",
        "error_message",
    ]
    types = {
        "published_at": "TIMESTAMP",
        "target_month": "STRING",
        "validation_github_run_id": "STRING",
        "cumulative_promoted_at": "TIMESTAMP",
        "apply_requested": "BOOL",
        "status": "STRING",
        "execution_name": "STRING",
        "source_sales_rows": "INT64",
        "source_store_rows": "INT64",
        "source_pod_rows": "INT64",
        "target_before_sales_rows": "INT64",
        "target_before_store_rows": "INT64",
        "target_before_pod_rows": "INT64",
        "target_after_sales_rows": "INT64",
        "target_after_store_rows": "INT64",
        "target_after_pod_rows": "INT64",
        "sales_mismatch_groups_before": "INT64",
        "store_mismatch_groups_before": "INT64",
        "pod_mismatch_groups_before": "INT64",
        "error_message": "STRING",
    }
    params = [
        bigquery.ScalarQueryParameter(name, types[name], payload.get(name))
        for name in columns
    ]
    placeholders = ", ".join(f"@{name}" for name in columns)
    sql = f"INSERT INTO `{table_id}` ({', '.join(columns)}) VALUES ({placeholders})"
    client.query(
        sql,
        job_config=bigquery.QueryJobConfig(query_parameters=params),
        location=location,
    ).result()


def main() -> None:
    args = parse_args()
    validate_target_month(args.target_month)

    source_client = bigquery.Client(project=args.project_id, location=args.source_location)
    target_client = bigquery.Client(project=args.project_id, location=args.target_location)
    audit_table = ensure_audit_table(
        source_client,
        args.project_id,
        args.audit_dataset,
        args.source_location,
    )

    execution_name = os.getenv("CLOUD_RUN_EXECUTION") or os.getenv("K_REVISION") or ""
    audit_payload: dict[str, Any] = {
        "published_at": datetime.now(timezone.utc),
        "target_month": args.target_month,
        "validation_github_run_id": None,
        "cumulative_promoted_at": None,
        "apply_requested": args.apply,
        "status": "FAILED",
        "execution_name": execution_name,
        "error_message": None,
    }
    stage_ids: dict[str, str] = {}

    try:
        gate = get_promotion_gate(
            source_client,
            args.project_id,
            args.audit_dataset,
            args.source_location,
            args.target_month,
        )
        audit_payload["validation_github_run_id"] = gate["run_id"]
        audit_payload["cumulative_promoted_at"] = gate["promoted_at"]
        validate_cumulative_snapshot(
            source_client,
            args.project_id,
            args.source_dataset,
            args.source_location,
            args.target_month,
            gate,
        )

        configs = table_configs(args.project_id, args.source_dataset)
        dataframes: dict[str, pd.DataFrame] = {}
        for config in configs:
            dataframe = query_dataframe(
                source_client,
                config.source_sql,
                args.source_location,
                args.target_month,
            )
            dataframes[config.key] = dataframe
            audit_payload[f"source_{config.key}_rows"] = len(dataframe)

        stage_suffix = f"{args.target_month}_{uuid.uuid4().hex[:12]}"
        comparisons_before: dict[str, Comparison] = {}
        for config in configs:
            stage_id = load_stage_table(
                target_client,
                dataframes[config.key],
                args.project_id,
                args.target_dataset,
                config.target_table,
                args.target_location,
                stage_suffix,
            )
            stage_ids[config.key] = stage_id
            target_id = f"{args.project_id}.{args.target_dataset}.{config.target_table}"
            comparison = compare_stage_to_target(
                target_client,
                stage_id,
                target_id,
                config.month_column,
                args.target_month,
                args.target_location,
            )
            comparisons_before[config.key] = comparison
            audit_payload[f"target_before_{config.key}_rows"] = comparison.target_rows
            audit_payload[f"{config.key}_mismatch_groups_before"] = comparison.mismatch_groups

        all_matched = all(comparison.matched for comparison in comparisons_before.values())
        if all_matched:
            status = "ALREADY_MATCHED"
        elif not args.apply:
            status = "DRY_RUN_MISMATCH"
        else:
            apply_transaction(
                target_client,
                args.project_id,
                args.target_dataset,
                configs,
                stage_ids,
                args.target_month,
                args.target_location,
            )
            status = "SUCCESS"

        comparisons_after: dict[str, Comparison] = {}
        for config in configs:
            target_id = f"{args.project_id}.{args.target_dataset}.{config.target_table}"
            comparison = compare_stage_to_target(
                target_client,
                stage_ids[config.key],
                target_id,
                config.month_column,
                args.target_month,
                args.target_location,
            )
            comparisons_after[config.key] = comparison
            audit_payload[f"target_after_{config.key}_rows"] = comparison.target_rows

        if status in {"SUCCESS", "ALREADY_MATCHED"}:
            failed_tables = [key for key, value in comparisons_after.items() if not value.matched]
            if failed_tables:
                raise RuntimeError(f"post-publish verification failed: {failed_tables}")

        audit_payload["status"] = status
        print(
            json.dumps(
                {
                    "target_month": args.target_month,
                    "apply": args.apply,
                    "status": status,
                    "validation_github_run_id": gate["run_id"],
                    "before": {key: value.__dict__ for key, value in comparisons_before.items()},
                    "after": {key: value.__dict__ for key, value in comparisons_after.items()},
                },
                ensure_ascii=False,
                default=str,
            )
        )
    except Exception as exc:
        audit_payload["status"] = "FAILED"
        audit_payload["error_message"] = str(exc)[:10000]
        raise
    finally:
        try:
            write_audit(source_client, audit_table, args.source_location, audit_payload)
        except Exception as audit_exc:
            print(f"failed to write production publish audit: {audit_exc}")
        for stage_id in stage_ids.values():
            try:
                target_client.delete_table(stage_id, not_found_ok=True)
            except Exception as cleanup_exc:
                print(f"failed to delete stage table {stage_id}: {cleanup_exc}")


if __name__ == "__main__":
    main()
