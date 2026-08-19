from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from google.cloud import bigquery


LEGACY_TARGET_MONTH = "202602"
LEGACY_SOURCE_DATASET = "ice_qb_source_p1"
LEGACY_SOURCE_TABLE = "author_condition_list"
LEGACY_LOOKUP_TABLE = "legacy_author_lookup_202602"
EXPECTED_SOURCE_ROWS = 13848
EXPECTED_PRODUCT_COUNT = 4765


def sync_legacy_author_lookup(
    project_id: str,
    target_dataset: str,
    target_month: str,
    target_location: str,
) -> int:
    """Sync the Access-era author lookup used for the 202602 migration boundary.

    The canonical legacy author-condition table is in the US multi-region while
    the new royalty pipeline is in asia-northeast1. BigQuery cannot issue a
    cross-location query, so the small distinct product lookup is transferred
    through the client. This path is intentionally limited to 202602.
    """
    normalized_month = target_month.replace("-", "")
    if normalized_month != LEGACY_TARGET_MONTH:
        return 0

    source_client = bigquery.Client(project=project_id, location="US")
    source_sql = f"""
        SELECT
            CONCAT('01-', product_code) AS product_key
            , digital_pub_code AS electronic_publication_code
            , COUNT(*) OVER () AS product_count
            , SUM(author_rows) OVER () AS source_rows
        FROM (
            SELECT
                product_code
                , ANY_VALUE(digital_pub_code) AS digital_pub_code
                , COUNT(*) AS author_rows
            FROM
                `{project_id}.{LEGACY_SOURCE_DATASET}.{LEGACY_SOURCE_TABLE}`
            WHERE
                product_code IS NOT NULL
            GROUP BY
                product_code
        )
        ORDER BY
            product_key
    """
    iterator = source_client.query(source_sql, location="US").result()
    records = [dict(row.items()) for row in iterator]
    if not records:
        raise RuntimeError("legacy 202602 author lookup source is empty")

    product_count = int(records[0]["product_count"])
    source_rows = int(records[0]["source_rows"])
    if source_rows != EXPECTED_SOURCE_ROWS or product_count != EXPECTED_PRODUCT_COUNT:
        raise RuntimeError(
            "legacy 202602 author lookup source changed: "
            f"source_rows={source_rows}, product_count={product_count}"
        )

    synced_at = datetime.now(timezone.utc)
    dataframe = pd.DataFrame(
        {
            "product_key": [row["product_key"] for row in records],
            "electronic_publication_code": [row["electronic_publication_code"] for row in records],
            "synced_at": [synced_at] * len(records),
            "source_table": [f"{LEGACY_SOURCE_DATASET}.{LEGACY_SOURCE_TABLE}"] * len(records),
        }
    )

    target_client = bigquery.Client(project=project_id, location=target_location)
    dataset = bigquery.Dataset(f"{project_id}.{target_dataset}")
    dataset.location = target_location
    target_client.create_dataset(dataset, exists_ok=True)

    table_id = f"{project_id}.{target_dataset}.{LEGACY_LOOKUP_TABLE}"
    schema = [
        bigquery.SchemaField("product_key", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("electronic_publication_code", "STRING"),
        bigquery.SchemaField("synced_at", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("source_table", "STRING", mode="REQUIRED"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    target_client.load_table_from_dataframe(
        dataframe,
        table_id,
        job_config=job_config,
        location=target_location,
    ).result()

    loaded = target_client.get_table(table_id)
    if int(loaded.num_rows) != EXPECTED_PRODUCT_COUNT:
        raise RuntimeError(
            "legacy 202602 author lookup load count mismatch: "
            f"expected={EXPECTED_PRODUCT_COUNT}, actual={loaded.num_rows}"
        )
    return EXPECTED_PRODUCT_COUNT
