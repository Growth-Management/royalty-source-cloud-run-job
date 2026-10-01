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
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from google.cloud import bigquery

# Cloud Run runs this file as `python scripts/publish_to_ice_qb_source_p1.py`, which puts
# scripts/ (not the repository root) on sys.path. Add the root so `app` is importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.source_diff_worker import (  # noqa: E402
    DeleteCandidateApproval,
    DiffSummary,
    compare_stage_to_target,
    ensure_delete_candidates_safe,
)

TABLE_KEYS = ("sales", "store", "pod")
# Diff audit columns added in Phase 1. Kept nullable so existing audit rows stay valid.
DIFF_AUDIT_COLUMNS = tuple(
    f"{key}_{metric}_rows_before"
    for metric in ("unchanged", "insert", "delete_candidate")
    for key in TABLE_KEYS
)
APPROVAL_AUDIT_COLUMNS = {
    "delete_candidate_approved_by": "STRING",
    "delete_candidate_approved_at": "TIMESTAMP",
    "delete_candidate_approval_validation_run_id": "STRING",
    "delete_candidate_approval_counts_json": "STRING",
}


@dataclass(frozen=True)
class TableConfig:
    key: str
    target_table: str
    month_column: str
    source_sql: str


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
    parser.add_argument(
        "--delete-candidate-approval-json",
        default=os.getenv("DELETE_CANDIDATE_APPROVAL_JSON", ""),
        help=(
            "Reviewed approval JSON with target_month, approved_by, approved_at, "
            "validation_run_id, and delete_candidate_rows."
        ),
    )
    return parser.parse_args()


def validate_target_month(target_month: str) -> None:
    if len(target_month) != 6 or not target_month.isdigit():
        raise ValueError("target_month must use YYYYMM format")
    month = int(target_month[4:6])
    if month < 1 or month > 12:
        raise ValueError(f"invalid target month: {target_month}")


def parse_delete_candidate_approval(raw_json: str) -> DeleteCandidateApproval | None:
    if not raw_json.strip():
        return None
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError("DELETE_CANDIDATE_APPROVAL_JSON must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("DELETE_CANDIDATE_APPROVAL_JSON must be a JSON object")

    required = {
        "target_month",
        "approved_by",
        "approved_at",
        "validation_run_id",
        "delete_candidate_rows",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"delete candidate approval is missing fields: {', '.join(missing)}")

    try:
        approved_at = datetime.fromisoformat(str(payload["approved_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("delete candidate approval approved_at must be ISO-8601") from exc
    if approved_at.tzinfo is None:
        raise ValueError("delete candidate approval approved_at must include a timezone")

    counts = payload["delete_candidate_rows"]
    if not isinstance(counts, dict):
        raise ValueError("delete candidate approval delete_candidate_rows must be an object")
    unknown = sorted(set(counts) - set(TABLE_KEYS))
    if unknown:
        raise ValueError(f"delete candidate approval contains unknown tables: {', '.join(unknown)}")
    normalized_counts: dict[str, int] = {}
    for key, value in counts.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"delete candidate approval count for {key} must be an integer")
        if value < 0:
            raise ValueError(f"delete candidate approval count for {key} must be non-negative")
        normalized_counts[key] = value

    return DeleteCandidateApproval(
        target_month=str(payload["target_month"]),
        approved_by=str(payload["approved_by"]),
        approved_at=approved_at,
        validation_run_id=str(payload["validation_run_id"]),
        delete_candidate_rows=normalized_counts,
    )


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
            , delete_candidate_approved_by STRING
            , delete_candidate_approved_at TIMESTAMP
            , delete_candidate_approval_validation_run_id STRING
            , delete_candidate_approval_counts_json STRING
            , error_message STRING
        )
        PARTITION BY DATE(published_at)
        CLUSTER BY target_month, status
    """
    client.query(sql, location=location).result()
    existing_columns = {schema_field.name for schema_field in client.get_table(table_id).schema}
    alter_sql = build_add_diff_columns_sql(table_id, existing_columns)
    if alter_sql:
        # One DDL statement only when columns are missing, to stay well within
        # BigQuery's per-table metadata update rate limits.
        client.query(alter_sql, location=location).result()
    return table_id


def build_add_diff_columns_sql(table_id: str, existing_columns: set[str]) -> str | None:
    missing_types = {
        **{name: "INT64" for name in DIFF_AUDIT_COLUMNS if name not in existing_columns},
        **{name: column_type for name, column_type in APPROVAL_AUDIT_COLUMNS.items() if name not in existing_columns},
    }
    if not missing_types:
        return None
    clauses = ", ".join(
        f"ADD COLUMN IF NOT EXISTS {name} {column_type}"
        for name, column_type in missing_types.items()
    )
    return f"ALTER TABLE `{table_id}` {clauses}"


def decide_publish_status(
    comparisons: dict[str, DiffSummary],
    apply: bool,
    *,
    target_month: str | None = None,
    validation_run_id: str | None = None,
    approval: DeleteCandidateApproval | None = None,
) -> str:
    """Return ALREADY_MATCHED, DRY_RUN_MISMATCH, or APPLY.

    The DELETE_CANDIDATE gate only fires when apply is requested; dry-run always
    completes so the counts can be reviewed. No override is reachable from here.
    """
    if all(comparison.matched for comparison in comparisons.values()):
        return "ALREADY_MATCHED"
    if not apply:
        return "DRY_RUN_MISMATCH"
    if approval is not None:
        if target_month is None or validation_run_id is None:
            raise ValueError("target_month and validation_run_id are required with an approval")
        if approval.validation_run_id != validation_run_id:
            raise RuntimeError(
                "DELETE_CANDIDATE approval validation_run_id differs from the promoted snapshot; "
                f"actual={validation_run_id}, approved={approval.validation_run_id}"
            )
    ensure_delete_candidates_safe(comparisons, target_month=target_month, approval=approval)
    return "APPLY"


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


def ensure_stage_within_target_month(
    client: bigquery.Client,
    stage_id: str,
    month_column: str,
    target_month: str,
    location: str,
) -> None:
    """Block apply if the stage holds rows outside the target month.

    apply_transaction deletes only the target month but inserts every stage row, so an
    out-of-month stage row would change another month in production.
    """
    sql = f"""
        SELECT
            COUNTIF({month_column} IS NULL OR {month_column} != @target_month_int) AS out_of_month_rows
        FROM
            `{stage_id}`
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("target_month_int", "INT64", int(target_month))]
    )
    row = next(iter(client.query(sql, job_config=job_config, location=location).result()))
    out_of_month_rows = int(row.out_of_month_rows or 0)
    if out_of_month_rows:
        raise RuntimeError(
            f"stage contains rows outside target month; apply is blocked: "
            f"stage={stage_id}, {month_column}!={target_month} rows={out_of_month_rows}"
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
        *DIFF_AUDIT_COLUMNS,
        *APPROVAL_AUDIT_COLUMNS,
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
        **{name: "INT64" for name in DIFF_AUDIT_COLUMNS},
        **APPROVAL_AUDIT_COLUMNS,
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
        "delete_candidate_approved_by": None,
        "delete_candidate_approved_at": None,
        "delete_candidate_approval_validation_run_id": None,
        "delete_candidate_approval_counts_json": None,
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
        approval = parse_delete_candidate_approval(args.delete_candidate_approval_json)
        if approval is not None:
            audit_payload["delete_candidate_approved_by"] = approval.approved_by
            audit_payload["delete_candidate_approved_at"] = approval.approved_at
            audit_payload["delete_candidate_approval_validation_run_id"] = approval.validation_run_id
            audit_payload["delete_candidate_approval_counts_json"] = json.dumps(
                dict(approval.delete_candidate_rows), sort_keys=True
            )
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
        comparisons_before: dict[str, DiffSummary] = {}
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
            audit_payload[f"{config.key}_unchanged_rows_before"] = comparison.unchanged_rows
            audit_payload[f"{config.key}_insert_rows_before"] = comparison.insert_rows
            audit_payload[f"{config.key}_delete_candidate_rows_before"] = comparison.delete_candidate_rows

        status = decide_publish_status(
            comparisons_before,
            args.apply,
            target_month=args.target_month,
            validation_run_id=gate["run_id"],
            approval=approval,
        )
        if status == "APPLY":
            for config in configs:
                ensure_stage_within_target_month(
                    target_client,
                    stage_ids[config.key],
                    config.month_column,
                    args.target_month,
                    args.target_location,
                )
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

        comparisons_after: dict[str, DiffSummary] = {}
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
                    "before": {key: value.as_dict() for key, value in comparisons_before.items()},
                    "after": {key: value.as_dict() for key, value in comparisons_after.items()},
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
