from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


class BigQueryClient:
    def __init__(self, project_id: str, location: str):
        from google.cloud import bigquery

        self.bigquery = bigquery
        self.project_id = project_id
        self.location = location
        self.client = bigquery.Client(project=project_id, location=location)

    def ensure_dataset(self, dataset_name: str) -> None:
        dataset = self.bigquery.Dataset(f"{self.project_id}.{dataset_name}")
        dataset.location = self.location
        self.client.create_dataset(dataset, exists_ok=True)

    def load_dataframe(self, dataframe: pd.DataFrame, table_id: str) -> int:
        if dataframe.empty:
            return 0
        job_config = self.bigquery.LoadJobConfig(write_disposition=self.bigquery.WriteDisposition.WRITE_APPEND)
        job = self.client.load_table_from_dataframe(dataframe, table_id, job_config=job_config)
        job.result()
        return int(job.output_rows or len(dataframe))

    def run_sql(self, sql: str) -> list[dict[str, Any]]:
        rows = self.client.query(sql).result()
        return [dict(row.items()) for row in rows]

    def run_sql_file(self, sql_path: str | Path, replacements: dict[str, str]) -> list[dict[str, Any]]:
        sql = Path(sql_path).read_text(encoding="utf-8")
        for key, value in replacements.items():
            sql = sql.replace("{{ " + key + " }}", value)
        return self.run_sql(sql)

    def count_rows(self, table_id: str, target_month: str) -> int:
        query = f"SELECT COUNT(*) AS row_count FROM `{table_id}` WHERE target_month = @target_month"
        job_config = self.bigquery.QueryJobConfig(
            query_parameters=[self.bigquery.ScalarQueryParameter("target_month", "STRING", target_month)]
        )
        rows = list(self.client.query(query, job_config=job_config).result())
        return int(rows[0]["row_count"]) if rows else 0

    def insert_json_rows(self, table_id: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            errors = self.client.insert_rows_json(table_id, rows)
            if errors:
                raise RuntimeError(f"BigQuery insert_rows_json failed for {table_id}: {errors}")

    def ensure_audit_tables(self, audit_dataset: str) -> None:
        self.ensure_dataset(audit_dataset)
        self.run_sql(f"""
        CREATE TABLE IF NOT EXISTS `{self.project_id}.{audit_dataset}.pipeline_audit_log` (
          run_id STRING, started_at TIMESTAMP, finished_at TIMESTAMP, target_month STRING,
          source_kind STRING, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
          staging_rows_loaded INT64, raw_rows_loaded INT64, source_rows_created INT64,
          source_quality_error_count INT64, warning_count INT64, error_count INT64,
          status STRING, error_message STRING
        ) PARTITION BY DATE(started_at) CLUSTER BY target_month, source_kind, status
        """)
        self.run_sql(f"""
        CREATE TABLE IF NOT EXISTS `{self.project_id}.{audit_dataset}.source_quality_results` (
          run_id STRING, checked_at TIMESTAMP, target_month STRING, check_name STRING,
          severity STRING, table_name STRING, error_count INT64, sample_json STRING
        ) PARTITION BY DATE(checked_at) CLUSTER BY target_month, severity, table_name
        """)
