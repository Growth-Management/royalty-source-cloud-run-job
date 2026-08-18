#!/usr/bin/env python3
"""Copy the legacy Access-derived POD history from US into royalty processing region.

This is a one-time migration. It intentionally transfers rows through the client
instead of issuing a cross-region BigQuery query, which BigQuery does not allow.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal

import pandas as pd
from google.cloud import bigquery


SOURCE_DATASET = "ice_qb_source_p1"
SOURCE_TABLE = "pod_sales_summary"
TARGET_TABLE = "pod_access_history_seed"
EXPECTED_TOTAL_ROWS = 3392
EXPECTED_202602 = {
    "row_count": 19,
    "quantity_total": 38,
    "sales_amount_total": Decimal("51138"),
    "manufacturing_cost_total": Decimal("37027"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", default="ice-qb")
    parser.add_argument("--target-dataset", default="royalty_source")
    parser.add_argument("--source-location", default="US")
    parser.add_argument("--target-location", default="asia-northeast1")
    return parser.parse_args()


def source_sql(project_id: str) -> str:
    return f"""
        SELECT
            CAST(year_month AS STRING) AS target_month,
            publisher_name AS publisher,
            product_code,
            isbn,
            title,
            unit_price,
            discount_rate AS rate,
            sales_quantity AS quantity,
            net_amount,
            tax_amount AS tax,
            sales_amount,
            manufacturing_cost,
            printing_cost AS factor_15,
            binding_cost AS factor_108,
            page_count AS pages,
            CAST(dl_year_month AS STRING) AS dl_month,
            CAST(year_month AS STRING) AS sales_month,
            'pod_access_history' AS source_kind,
            ROW_NUMBER() OVER (
                ORDER BY year_month, product_code, isbn, title, sales_amount, manufacturing_cost
            ) AS source_row_number,
            'legacy-access-bigquery-us' AS source_file_id,
            '{SOURCE_DATASET}.{SOURCE_TABLE}' AS source_file_name,
            CURRENT_TIMESTAMP() AS loaded_at
        FROM `{project_id}.{SOURCE_DATASET}.{SOURCE_TABLE}`
        ORDER BY source_row_number
    """


def ensure_target_table(
    client: bigquery.Client, project_id: str, dataset: str, location: str
) -> str:
    dataset_ref = bigquery.Dataset(f"{project_id}.{dataset}")
    dataset_ref.location = location
    client.create_dataset(dataset_ref, exists_ok=True)
    table_id = f"{project_id}.{dataset}.{TARGET_TABLE}"
    sql = f"""
        CREATE TABLE IF NOT EXISTS `{table_id}` (
          target_month STRING,
          publisher STRING,
          product_code STRING,
          isbn STRING,
          title STRING,
          unit_price NUMERIC,
          rate STRING,
          quantity INT64,
          net_amount NUMERIC,
          tax NUMERIC,
          sales_amount NUMERIC,
          manufacturing_cost NUMERIC,
          factor_15 NUMERIC,
          factor_108 NUMERIC,
          pages INT64,
          dl_month STRING,
          sales_month STRING,
          source_kind STRING,
          source_row_number INT64,
          source_file_id STRING,
          source_file_name STRING,
          loaded_at TIMESTAMP
        )
        CLUSTER BY target_month, source_kind
    """
    client.query(sql, location=location).result()
    return table_id


def fetch_source(project_id: str, location: str) -> pd.DataFrame:
    client = bigquery.Client(project=project_id, location=location)
    iterator = client.query(source_sql(project_id), location=location).result()
    columns = [field.name for field in iterator.schema]
    dataframe = pd.DataFrame.from_records([dict(row.items()) for row in iterator], columns=columns)
    if len(dataframe) != EXPECTED_TOTAL_ROWS:
        raise RuntimeError(
            f"legacy POD source row count mismatch: expected={EXPECTED_TOTAL_ROWS}, actual={len(dataframe)}"
        )
    return dataframe


def load_target(
    dataframe: pd.DataFrame,
    project_id: str,
    dataset: str,
    location: str,
) -> str:
    client = bigquery.Client(project=project_id, location=location)
    table_id = ensure_target_table(client, project_id, dataset, location)
    table = client.get_table(table_id)
    job_config = bigquery.LoadJobConfig(
        schema=table.schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_dataframe(dataframe, table_id, job_config=job_config, location=location)
    job.result()
    return table_id


def validate_target(project_id: str, table_id: str, location: str) -> dict[str, object]:
    client = bigquery.Client(project=project_id, location=location)
    sql = f"""
        SELECT
          COUNT(*) AS total_rows,
          COUNTIF(target_month = '202602') AS row_count_202602,
          COALESCE(SUM(IF(target_month = '202602', quantity, 0)), 0) AS quantity_202602,
          COALESCE(SUM(IF(target_month = '202602', sales_amount, 0)), 0) AS sales_amount_202602,
          COALESCE(SUM(IF(target_month = '202602', manufacturing_cost, 0)), 0) AS manufacturing_cost_202602
        FROM `{table_id}`
    """
    row = next(iter(client.query(sql, location=location).result()))
    result = {
        "total_rows": int(row["total_rows"]),
        "row_count_202602": int(row["row_count_202602"]),
        "quantity_202602": int(row["quantity_202602"]),
        "sales_amount_202602": str(row["sales_amount_202602"]),
        "manufacturing_cost_202602": str(row["manufacturing_cost_202602"]),
    }
    expected = EXPECTED_202602
    ok = (
        result["total_rows"] == EXPECTED_TOTAL_ROWS
        and result["row_count_202602"] == expected["row_count"]
        and result["quantity_202602"] == expected["quantity_total"]
        and Decimal(result["sales_amount_202602"]) == expected["sales_amount_total"]
        and Decimal(result["manufacturing_cost_202602"]) == expected["manufacturing_cost_total"]
    )
    result["ok"] = ok
    if not ok:
        raise RuntimeError(f"POD history seed validation failed: {json.dumps(result, ensure_ascii=False)}")
    return result


def main() -> None:
    args = parse_args()
    dataframe = fetch_source(args.project_id, args.source_location)
    table_id = load_target(
        dataframe,
        args.project_id,
        args.target_dataset,
        args.target_location,
    )
    result = validate_target(args.project_id, table_id, args.target_location)
    print(json.dumps({"table_id": table_id, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
