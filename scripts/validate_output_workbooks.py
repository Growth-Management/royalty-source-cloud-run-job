#!/usr/bin/env python3
"""Validate generated Access/POD input workbooks and emit JSON/Markdown reports."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

EXPECTED_SHEETS = {
    "access": ["sales", "store_detail", "author_conditions"],
    "pod": ["pod_sales"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--access", required=True)
    parser.add_argument("--pod", required=True)
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    return parser.parse_args()


def normalize_header(value: Any) -> str:
    return "" if value is None else str(value).strip()


def inspect_workbook(path: Path, kind: str) -> dict[str, Any]:
    expected = EXPECTED_SHEETS[kind]
    result: dict[str, Any] = {
        "kind": kind,
        "file": path.name,
        "exists": path.exists(),
        "expected_sheets": expected,
        "actual_sheets": [],
        "missing_sheets": [],
        "unexpected_sheets": [],
        "sheets": [],
        "errors": [],
        "warnings": [],
    }
    if not path.exists():
        result["errors"].append("file_not_found")
        return result

    workbook = load_workbook(path, read_only=True, data_only=True)
    actual = workbook.sheetnames
    result["actual_sheets"] = actual
    result["missing_sheets"] = [name for name in expected if name not in actual]
    result["unexpected_sheets"] = [name for name in actual if name not in expected]
    if result["missing_sheets"]:
        result["errors"].append("missing_expected_sheets")
    if result["unexpected_sheets"]:
        result["warnings"].append("unexpected_sheets")

    for sheet_name in actual:
        worksheet = workbook[sheet_name]
        rows = worksheet.iter_rows(values_only=True)
        header_row = next(rows, ())
        headers = [normalize_header(value) for value in header_row]
        duplicate_headers = sorted(
            name for name, count in Counter(headers).items() if name and count > 1
        )
        blank_headers = sum(1 for name in headers if not name)
        data_rows = 0
        completely_blank_rows = 0
        null_cells = 0
        non_null_cells = 0
        for row in rows:
            data_rows += 1
            values = list(row)
            if not any(value not in (None, "") for value in values):
                completely_blank_rows += 1
            for value in values[: len(headers)]:
                if value in (None, ""):
                    null_cells += 1
                else:
                    non_null_cells += 1

        sheet_result = {
            "name": sheet_name,
            "columns": len(headers),
            "rows": data_rows,
            "headers": headers,
            "duplicate_headers": duplicate_headers,
            "blank_headers": blank_headers,
            "completely_blank_rows": completely_blank_rows,
            "null_cells": null_cells,
            "non_null_cells": non_null_cells,
        }
        if not headers:
            result["errors"].append(f"{sheet_name}:missing_header")
        if duplicate_headers:
            result["errors"].append(f"{sheet_name}:duplicate_headers")
        if blank_headers:
            result["errors"].append(f"{sheet_name}:blank_headers")
        if data_rows == 0:
            result["warnings"].append(f"{sheet_name}:zero_data_rows")
        if completely_blank_rows:
            result["warnings"].append(f"{sheet_name}:blank_data_rows")
        result["sheets"].append(sheet_result)

    workbook.close()
    return result


def build_markdown(report: dict[str, Any]) -> str:
    lines = [
        "## Generated workbook validation",
        "",
        f"**Result:** {'OK' if report['ok'] else 'NG'}",
        "",
        "| Workbook | Sheet | Rows | Columns | Blank headers | Duplicate headers | Blank rows | Null cells |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for workbook in report["workbooks"]:
        if not workbook["sheets"]:
            lines.append(
                f"| {workbook['file']} | file unavailable | 0 | 0 | 0 | 0 | 0 | 0 |"
            )
        for sheet in workbook["sheets"]:
            lines.append(
                "| {file} | {name} | {rows} | {columns} | {blank_headers} | "
                "{duplicates} | {blank_rows} | {null_cells} |".format(
                    file=workbook["file"],
                    name=sheet["name"],
                    rows=sheet["rows"],
                    columns=sheet["columns"],
                    blank_headers=sheet["blank_headers"],
                    duplicates=len(sheet["duplicate_headers"]),
                    blank_rows=sheet["completely_blank_rows"],
                    null_cells=sheet["null_cells"],
                )
            )

    lines.extend(["", "### Checks", ""])
    for workbook in report["workbooks"]:
        missing = ", ".join(workbook["missing_sheets"]) or "none"
        unexpected = ", ".join(workbook["unexpected_sheets"]) or "none"
        errors = ", ".join(workbook["errors"]) or "none"
        warnings = ", ".join(workbook["warnings"]) or "none"
        lines.extend(
            [
                f"- **{workbook['file']}**",
                f"  - Missing sheets: {missing}",
                f"  - Unexpected sheets: {unexpected}",
                f"  - Errors: {errors}",
                f"  - Warnings: {warnings}",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    workbooks = [
        inspect_workbook(Path(args.access), "access"),
        inspect_workbook(Path(args.pod), "pod"),
    ]
    report = {
        "ok": all(not workbook["errors"] for workbook in workbooks),
        "error_count": sum(len(workbook["errors"]) for workbook in workbooks),
        "warning_count": sum(len(workbook["warnings"]) for workbook in workbooks),
        "workbooks": workbooks,
    }

    json_path = Path(args.json_output)
    markdown_path = Path(args.markdown_output)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(build_markdown(report), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "errors": report["error_count"], "warnings": report["warning_count"]}))

    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
