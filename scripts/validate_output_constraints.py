#!/usr/bin/env python3
"""Compare required-null and duplicate-key counts between output workbooks and BigQuery."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from openpyxl import load_workbook

SHEET_CONFIG = {
    "sales": {
        "workbook": "access",
        "table": "access_input_sales",
        "required": [
            "accounting_month_1",
            "accounting_month_2",
            "billing_code_1",
            "product_code",
            "product_key",
            "source_file_id",
            "source_row_number",
        ],
        "key": ["source_file_id", "source_row_number"],
    },
    "store_detail": {
        "workbook": "access",
        "table": "access_input_store_detail",
        "required": [
            "accounting_month_1",
            "accounting_month_2",
            "billing_code",
            "store_name",
            "product_key",
            "source_file_name",
        ],
        "key": ["source_file_name", "source_row_number", "product_key"],
    },
    "author_conditions": {
        "workbook": "access",
        "table": "access_input_author_conditions",
        "required": ["product_key", "product_code", "author_identifier_id", "author_name"],
        "key": ["product_code", "author_identifier_id"],
    },
    "pod_sales": {
        "workbook": "pod",
        "table": "access_input_pod_sales",
        "required": [
            "sales_month",
            "isbn",
            "quantity",
            "sales_amount",
            "source_kind",
            "source_file_id",
            "source_row_number",
        ],
        "key": ["source_file_id", "source_row_number"],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--access", required=True)
    parser.add_argument("--pod", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--location", default="asia-northeast1")
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    return parser.parse_args()


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def normalized_key_value(value: Any) -> str:
    if is_blank(value):
        return "<NULL>"
    return str(value).strip()


def workbook_counts(path: Path, sheet_name: str, required: list[str], key: list[str]) -> dict[str, Any]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    worksheet = workbook[sheet_name]
    rows = worksheet.iter_rows(values_only=True)
    headers = ["" if value is None else str(value).strip() for value in next(rows, ())]
    positions = {name: index for index, name in enumerate(headers)}
    missing_columns = [column for column in required + key if column not in positions]
    if missing_columns:
        workbook.close()
        return {
            "missing_columns": sorted(set(missing_columns)),
            "required_null_counts": {},
            "duplicate_key_groups": None,
            "duplicate_rows": None,
            "duplicate_samples": [],
        }

    null_counts = {column: 0 for column in required}
    key_counts: Counter[tuple[str, ...]] = Counter()
    for row in rows:
        for column in required:
            index = positions[column]
            value = row[index] if index < len(row) else None
            if is_blank(value):
                null_counts[column] += 1
        key_value = tuple(
            normalized_key_value(row[positions[column]] if positions[column] < len(row) else None)
            for column in key
        )
        key_counts[key_value] += 1

    duplicates = [(values, count) for values, count in key_counts.items() if count > 1]
    workbook.close()
    return {
        "missing_columns": [],
        "required_null_counts": null_counts,
        "duplicate_key_groups": len(duplicates),
        "duplicate_rows": sum(count - 1 for _, count in duplicates),
        "duplicate_samples": [
            {"key": dict(zip(key, values)), "count": count} for values, count in duplicates[:5]
        ],
    }


def bigquery_counts(
    client: bigquery.Client,
    table_id: str,
    required: list[str],
    key: list[str],
    location: str,
) -> dict[str, Any]:
    null_expressions = [
        f"COUNTIF(`{column}` IS NULL OR TRIM(CAST(`{column}` AS STRING)) = '') AS `{column}`"
        for column in required
    ]
    null_row = next(
        iter(client.query(f"SELECT {', '.join(null_expressions)} FROM `{table_id}`", location=location).result())
    )
    null_counts = {column: int(null_row[column]) for column in required}

    key_columns = ", ".join(f"`{column}`" for column in key)
    duplicate_sql = f"""
        WITH duplicate_keys AS (
          SELECT {key_columns}, COUNT(*) AS duplicate_count
          FROM `{table_id}`
          GROUP BY {key_columns}
          HAVING COUNT(*) > 1
        )
        SELECT
          COUNT(*) AS duplicate_key_groups,
          COALESCE(SUM(duplicate_count - 1), 0) AS duplicate_rows,
          TO_JSON_STRING(ARRAY_AGG(STRUCT({key_columns}, duplicate_count) LIMIT 5)) AS duplicate_samples
        FROM duplicate_keys
    """
    duplicate_row = next(iter(client.query(duplicate_sql, location=location).result()))
    samples_json = duplicate_row["duplicate_samples"]
    return {
        "required_null_counts": null_counts,
        "duplicate_key_groups": int(duplicate_row["duplicate_key_groups"]),
        "duplicate_rows": int(duplicate_row["duplicate_rows"]),
        "duplicate_samples": json.loads(samples_json) if samples_json else [],
    }


def build_markdown(report: dict[str, Any]) -> str:
    lines = [
        "## Required values and duplicate keys",
        "",
        f"**Result:** {'OK' if report['ok'] else 'NG'}",
        "",
        "### Required NULL counts",
        "",
        "| Sheet | Column | Excel | BigQuery | Counts match | Required valid |",
        "|---|---|---:|---:|---|---|",
    ]
    for result in report["results"]:
        sheet_name = result["sheet"]
        if result.get("missing_columns"):
            lines.append(f"| {sheet_name} | missing: {', '.join(result['missing_columns'])} | - | - | no | no |")
            continue
        for column, excel_count in result["excel_required_null_counts"].items():
            bq_count = result["bigquery_required_null_counts"][column]
            lines.append(
                f"| {sheet_name} | {column} | {excel_count} | {bq_count} | "
                f"{'yes' if excel_count == bq_count else 'no'} | {'yes' if excel_count == 0 else 'no'} |"
            )
    lines.extend([
        "",
        "### Duplicate keys",
        "",
        "| Sheet | Excel groups | BigQuery groups | Excel extra rows | BigQuery extra rows | Match | Unique |",
        "|---|---:|---:|---:|---:|---|---|",
    ])
    for result in report["results"]:
        if result.get("missing_columns"):
            lines.append(f"| {result['sheet']} | - | - | - | - | no | no |")
            continue
        lines.append(
            f"| {result['sheet']} | {result['excel_duplicate_key_groups']} | {result['bigquery_duplicate_key_groups']} | "
            f"{result['excel_duplicate_rows']} | {result['bigquery_duplicate_rows']} | "
            f"{'yes' if result['duplicate_counts_match'] else 'no'} | {'yes' if result['keys_unique'] else 'no'} |"
        )
        if result["excel_duplicate_samples"]:
            lines.extend(["", f"#### {result['sheet']} duplicate samples", "", "```json", json.dumps(result["excel_duplicate_samples"], ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    paths = {"access": Path(args.access), "pod": Path(args.pod)}
    client = bigquery.Client(project=args.project_id)
    results = []
    ok = True

    for sheet_name, config in SHEET_CONFIG.items():
        excel = workbook_counts(paths[config["workbook"]], sheet_name, config["required"], config["key"])
        result: dict[str, Any] = {"sheet": sheet_name, "table": config["table"], "missing_columns": excel["missing_columns"]}
        if excel["missing_columns"]:
            result.update(excel)
            ok = False
            results.append(result)
            continue
        bq = bigquery_counts(
            client,
            f"{args.project_id}.{args.dataset}.{config['table']}",
            config["required"],
            config["key"],
            args.location,
        )
        null_counts_match = excel["required_null_counts"] == bq["required_null_counts"]
        required_valid = all(count == 0 for count in excel["required_null_counts"].values())
        duplicate_counts_match = (
            excel["duplicate_key_groups"] == bq["duplicate_key_groups"]
            and excel["duplicate_rows"] == bq["duplicate_rows"]
        )
        keys_unique = excel["duplicate_key_groups"] == 0
        result.update({
            "excel_required_null_counts": excel["required_null_counts"],
            "bigquery_required_null_counts": bq["required_null_counts"],
            "null_counts_match": null_counts_match,
            "required_valid": required_valid,
            "excel_duplicate_key_groups": excel["duplicate_key_groups"],
            "bigquery_duplicate_key_groups": bq["duplicate_key_groups"],
            "excel_duplicate_rows": excel["duplicate_rows"],
            "bigquery_duplicate_rows": bq["duplicate_rows"],
            "duplicate_counts_match": duplicate_counts_match,
            "keys_unique": keys_unique,
            "excel_duplicate_samples": excel["duplicate_samples"],
            "bigquery_duplicate_samples": bq["duplicate_samples"],
        })
        if not (null_counts_match and required_valid and duplicate_counts_match and keys_unique):
            ok = False
        results.append(result)

    report = {"ok": ok, "results": results}
    Path(args.json_output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.markdown_output).write_text(build_markdown(report), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
