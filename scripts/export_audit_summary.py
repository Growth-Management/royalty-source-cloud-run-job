#!/usr/bin/env python3
"""Export the latest pipeline audit and quality-check results for a target month."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from google.cloud import bigquery


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--target-month", required=True, help="YYYYMM or YYYY-MM")
    parser.add_argument("--location", default="asia-northeast1")
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    return parser.parse_args()


def normalized_target_month(value: str) -> str:
    value = value.strip()
    if len(value) == 6 and value.isdigit():
        return f"{value[:4]}-{value[4:]}"
    return value


def json_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def fetch_latest_audit(
    client: bigquery.Client, project_id: str, dataset: str, target_month: str, location: str
) -> dict[str, Any] | None:
    sql = f"""
      SELECT *
      FROM `{project_id}.{dataset}.pipeline_audit_log`
      WHERE target_month = @target_month
        AND source_kind IS NULL
      ORDER BY finished_at DESC, started_at DESC
      LIMIT 1
    """
    config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("target_month", "STRING", target_month)]
    )
    rows = list(client.query(sql, job_config=config, location=location).result())
    return {key: json_value(value) for key, value in rows[0].items()} if rows else None


def fetch_quality_results(
    client: bigquery.Client,
    project_id: str,
    dataset: str,
    run_id: str,
    location: str,
) -> list[dict[str, Any]]:
    sql = f"""
      SELECT run_id, checked_at, target_month, check_name, severity, table_name,
             error_count, sample_json
      FROM `{project_id}.{dataset}.source_quality_results`
      WHERE run_id = @run_id
      ORDER BY
        CASE severity WHEN 'ERROR' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,
        error_count DESC,
        check_name
    """
    config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    )
    rows = client.query(sql, job_config=config, location=location).result()
    return [{key: json_value(value) for key, value in row.items()} for row in rows]


def build_markdown(report: dict[str, Any]) -> str:
    audit = report.get("audit")
    lines = ["## BigQuery pipeline audit", ""]
    if not audit:
        lines.extend(["**Result:** NG", "", "No final audit row was found for the target month."])
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            f"**Result:** {'OK' if report['ok'] else 'NG'}",
            "",
            "| Item | Value |",
            "|---|---|",
            f"| Run ID | `{audit.get('run_id', '')}` |",
            f"| Target month | `{audit.get('target_month', '')}` |",
            f"| Status | `{audit.get('status', '')}` |",
            f"| Started | `{audit.get('started_at', '')}` |",
            f"| Finished | `{audit.get('finished_at', '')}` |",
            f"| Staging rows | {audit.get('staging_rows_loaded', 0)} |",
            f"| Raw rows | {audit.get('raw_rows_loaded', 0)} |",
            f"| Source rows | {audit.get('source_rows_created', 0)} |",
            f"| Quality errors | {audit.get('source_quality_error_count', 0)} |",
            f"| Warnings | {audit.get('warning_count', 0)} |",
            f"| Pipeline errors | {audit.get('error_count', 0)} |",
        ]
    )
    if audit.get("error_message"):
        lines.append(f"| Error message | {str(audit['error_message']).replace('|', '\\|')} |")

    quality = report.get("quality_results", [])
    lines.extend(
        [
            "",
            "### Quality-check results",
            "",
            "| Severity | Check | Table | Error count | Sample |",
            "|---|---|---|---:|---|",
        ]
    )
    if not quality:
        lines.append("| - | No recorded quality errors | - | 0 | - |")
    else:
        for result in quality:
            sample = (result.get("sample_json") or "-").replace("|", "\\|")
            if len(sample) > 240:
                sample = sample[:237] + "..."
            lines.append(
                f"| {result.get('severity', '')} | {result.get('check_name', '')} | "
                f"{result.get('table_name', '')} | {result.get('error_count', 0)} | `{sample}` |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    target_month = normalized_target_month(args.target_month)
    client = bigquery.Client(project=args.project_id, location=args.location)
    audit = fetch_latest_audit(client, args.project_id, args.dataset, target_month, args.location)
    quality_results = (
        fetch_quality_results(client, args.project_id, args.dataset, audit["run_id"], args.location)
        if audit
        else []
    )
    quality_error_count = sum(
        int(row.get("error_count") or 0)
        for row in quality_results
        if row.get("severity") == "ERROR"
    )
    ok = bool(
        audit
        and audit.get("status") == "SUCCESS"
        and int(audit.get("error_count") or 0) == 0
        and int(audit.get("source_quality_error_count") or 0) == 0
        and quality_error_count == 0
    )
    report = {
        "ok": ok,
        "target_month": target_month,
        "audit": audit,
        "quality_error_count": quality_error_count,
        "quality_result_count": len(quality_results),
        "quality_results": quality_results,
    }

    json_path = Path(args.json_output)
    markdown_path = Path(args.markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_markdown(report), encoding="utf-8")
    print(json.dumps({"ok": ok, "run_id": audit.get("run_id") if audit else None}))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
