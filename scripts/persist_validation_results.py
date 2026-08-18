#!/usr/bin/env python3
"""Persist GitHub Actions validation JSON reports to BigQuery audit tables."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--target-month", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--location", default="asia-northeast1")
    parser.add_argument("--validation-json")
    parser.add_argument("--constraints-json")
    parser.add_argument("--audit-summary-json")
    return parser.parse_args()


def load_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text(encoding="utf-8"))


def ensure_tables(client: bigquery.Client, project_id: str, dataset: str, location: str) -> None:
    statements = [
        f"""
        create table if not exists `{project_id}.{dataset}.workbook_validation_results` (
            recorded_at timestamp,
            target_month string,
            github_run_id string,
            workbook_kind string,
            workbook_file string,
            sheet_name string,
            table_name string,
            result string,
            excel_rows int64,
            bigquery_rows int64,
            row_count_match bool,
            excel_columns int64,
            bigquery_columns int64,
            column_order_match bool,
            numeric_totals_match bool,
            error_count int64,
            warning_count int64,
            detail_json string
        )
        partition by date(recorded_at)
        cluster by target_month, github_run_id
        """,
        f"""
        create table if not exists `{project_id}.{dataset}.constraint_validation_results` (
            recorded_at timestamp,
            target_month string,
            github_run_id string,
            workbook_kind string,
            workbook_file string,
            sheet_name string,
            table_name string,
            result string,
            required_null_count int64,
            duplicate_group_count int64,
            duplicate_extra_row_count int64,
            error_count int64,
            warning_count int64,
            detail_json string
        )
        partition by date(recorded_at)
        cluster by target_month, github_run_id
        """,
        f"""
        create table if not exists `{project_id}.{dataset}.validation_run_summary` (
            recorded_at timestamp,
            target_month string,
            github_run_id string,
            workbook_validation_status string,
            constraint_validation_status string,
            pipeline_audit_status string,
            workbook_error_count int64,
            workbook_warning_count int64,
            constraint_error_count int64,
            constraint_warning_count int64,
            audit_error_count int64,
            audit_warning_count int64,
            all_pass bool,
            detail_json string
        )
        partition by date(recorded_at)
        cluster by target_month, github_run_id
        """,
    ]
    for sql in statements:
        client.query(sql, location=location).result()


def replace_rows(
    client: bigquery.Client,
    table_id: str,
    target_month: str,
    github_run_id: str,
    rows: list[dict[str, Any]],
    location: str,
) -> None:
    delete_sql = f"""
        delete from `{table_id}`
        where target_month = @target_month
          and github_run_id = @github_run_id
    """
    config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_month", "STRING", target_month),
            bigquery.ScalarQueryParameter("github_run_id", "STRING", github_run_id),
        ]
    )
    client.query(delete_sql, job_config=config, location=location).result()
    if rows:
        errors = client.insert_rows_json(table_id, rows)
        if errors:
            raise RuntimeError(f"failed to insert rows into {table_id}: {errors}")


def workbook_rows(report: dict[str, Any] | None, target_month: str, run_id: str, recorded_at: str) -> list[dict[str, Any]]:
    if not report:
        return []
    rows: list[dict[str, Any]] = []
    for workbook in report.get("workbooks", []):
        workbook_errors = len(workbook.get("errors", []))
        workbook_warnings = len(workbook.get("warnings", []))
        for sheet in workbook.get("sheets", []):
            comparison = sheet.get("comparison") or {}
            bq = sheet.get("bigquery") or {}
            result = "PASS" if (
                comparison.get("row_count_match")
                and comparison.get("column_order_match")
                and comparison.get("numeric_totals_match")
            ) else "FAIL"
            rows.append(
                {
                    "recorded_at": recorded_at,
                    "target_month": target_month,
                    "github_run_id": run_id,
                    "workbook_kind": workbook.get("kind"),
                    "workbook_file": workbook.get("file"),
                    "sheet_name": sheet.get("name"),
                    "table_name": sheet.get("table"),
                    "result": result,
                    "excel_rows": sheet.get("rows"),
                    "bigquery_rows": bq.get("rows"),
                    "row_count_match": comparison.get("row_count_match"),
                    "excel_columns": sheet.get("columns"),
                    "bigquery_columns": bq.get("columns"),
                    "column_order_match": comparison.get("column_order_match"),
                    "numeric_totals_match": comparison.get("numeric_totals_match"),
                    "error_count": workbook_errors,
                    "warning_count": workbook_warnings,
                    "detail_json": json.dumps(sheet, ensure_ascii=False, default=str),
                }
            )
    return rows


def constraint_rows(report: dict[str, Any] | None, target_month: str, run_id: str, recorded_at: str) -> list[dict[str, Any]]:
    """Convert validate_output_constraints.py's top-level `results` structure."""
    if not report:
        return []
    rows: list[dict[str, Any]] = []
    for result in report.get("results", []):
        missing = result.get("missing_columns") or []
        excel_nulls = result.get("excel_required_null_counts") or result.get("required_null_counts") or {}
        required_null_count = sum(int(value or 0) for value in excel_nulls.values())
        duplicate_group_count = result.get("excel_duplicate_key_groups")
        if duplicate_group_count is None:
            duplicate_group_count = result.get("duplicate_key_groups") or 0
        duplicate_extra_row_count = result.get("excel_duplicate_rows")
        if duplicate_extra_row_count is None:
            duplicate_extra_row_count = result.get("duplicate_rows") or 0

        checks = [
            not missing,
            bool(result.get("null_counts_match", not missing)),
            bool(result.get("required_valid", not missing and required_null_count == 0)),
            bool(result.get("duplicate_counts_match", not missing)),
            bool(result.get("keys_unique", not missing and int(duplicate_group_count or 0) == 0)),
        ]
        error_count = sum(1 for check in checks if not check)
        rows.append(
            {
                "recorded_at": recorded_at,
                "target_month": target_month,
                "github_run_id": run_id,
                "workbook_kind": "pod" if result.get("sheet") == "pod_sales" else "access",
                "workbook_file": None,
                "sheet_name": result.get("sheet"),
                "table_name": result.get("table"),
                "result": "PASS" if all(checks) else "FAIL",
                "required_null_count": required_null_count,
                "duplicate_group_count": int(duplicate_group_count or 0),
                "duplicate_extra_row_count": int(duplicate_extra_row_count or 0),
                "error_count": error_count,
                "warning_count": 0,
                "detail_json": json.dumps(result, ensure_ascii=False, default=str),
            }
        )
    return rows


def status(report: dict[str, Any] | None) -> str:
    if report is None:
        return "NOT_RUN"
    return "PASS" if bool(report.get("ok")) else "FAIL"


def count_value(report: dict[str, Any] | None, key: str) -> int:
    if not report:
        return 0
    value = report.get(key, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def constraint_error_count(report: dict[str, Any] | None) -> int:
    if not report:
        return 0
    return sum(1 for result in report.get("results", []) if (
        result.get("missing_columns")
        or not result.get("null_counts_match", True)
        or not result.get("required_valid", True)
        or not result.get("duplicate_counts_match", True)
        or not result.get("keys_unique", True)
    ))


def main() -> None:
    args = parse_args()
    recorded_at = datetime.now(timezone.utc).isoformat()
    validation = load_json(args.validation_json)
    constraints = load_json(args.constraints_json)
    audit = load_json(args.audit_summary_json)

    client = bigquery.Client(project=args.project_id, location=args.location)
    ensure_tables(client, args.project_id, args.dataset, args.location)

    workbook_table = f"{args.project_id}.{args.dataset}.workbook_validation_results"
    constraint_table = f"{args.project_id}.{args.dataset}.constraint_validation_results"
    summary_table = f"{args.project_id}.{args.dataset}.validation_run_summary"

    replace_rows(
        client,
        workbook_table,
        args.target_month,
        args.run_id,
        workbook_rows(validation, args.target_month, args.run_id, recorded_at),
        args.location,
    )
    replace_rows(
        client,
        constraint_table,
        args.target_month,
        args.run_id,
        constraint_rows(constraints, args.target_month, args.run_id, recorded_at),
        args.location,
    )

    workbook_status = status(validation)
    constraint_status = status(constraints)
    audit_status = status(audit)
    all_pass = workbook_status == constraint_status == audit_status == "PASS"
    summary = {
        "recorded_at": recorded_at,
        "target_month": args.target_month,
        "github_run_id": args.run_id,
        "workbook_validation_status": workbook_status,
        "constraint_validation_status": constraint_status,
        "pipeline_audit_status": audit_status,
        "workbook_error_count": count_value(validation, "error_count"),
        "workbook_warning_count": count_value(validation, "warning_count"),
        "constraint_error_count": constraint_error_count(constraints),
        "constraint_warning_count": 0,
        "audit_error_count": count_value(audit, "quality_error_count") + count_value((audit or {}).get("audit"), "error_count"),
        "audit_warning_count": count_value((audit or {}).get("audit"), "warning_count"),
        "all_pass": all_pass,
        "detail_json": json.dumps(
            {"validation": validation, "constraints": constraints, "audit": audit},
            ensure_ascii=False,
            default=str,
        ),
    }
    replace_rows(client, summary_table, args.target_month, args.run_id, [summary], args.location)
    print(json.dumps({"target_month": args.target_month, "github_run_id": args.run_id, "all_pass": all_pass}))


if __name__ == "__main__":
    main()
