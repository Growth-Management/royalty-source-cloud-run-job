#!/usr/bin/env python3
"""Monthly auto-sync of source_author_conditions_ext from Salesforce.

source_author_conditions (Excel-origin) is frozen. product_code values that appear in
this month's sales but are missing from both source_author_conditions and
source_author_conditions_ext are looked up against the Salesforce mirror
(ice_qb_source.sf_Biblio__c / sf_BiblioContributor__c, US) and, where a match exists,
inserted into source_author_conditions_ext with source_type='sf_auto'.

BigQuery cannot join across locations, so matched rows are transferred through the
client into a relay dataset in asia-northeast1 first (ice_qb_source_salesforce, per the
2026-08 decision to use it as an approved relay), then merged into
source_author_conditions_ext with a same-region query.

product_code values that still have no Salesforce match after this step need manual
entry (see royalty-source-admin pages/author_conditions_ext.py, or the Sheets +
Apps Script path) and are written out as the "unresolved list" for that path.

Required IAM for the runtime service account
(suggested name: royalty-source-ext-loader@ice-qb.iam.gserviceaccount.com), scoped
narrower than the main pipeline's royalty-source-runner:
  - roles/bigquery.dataViewer on the `ice_qb_source` dataset (US)
      read sf_Biblio__c / sf_BiblioContributor__c
  - roles/bigquery.dataViewer on the `royalty_source` dataset (asia-northeast1)
      read source_monthly_product_sales / source_author_conditions
  - roles/bigquery.dataEditor on the `ice_qb_source_salesforce` dataset (asia-northeast1)
      write/replace the relay table
  - roles/bigquery.dataEditor scoped to `royalty_source.source_author_conditions_ext`
      (asia-northeast1) -- table-level IAM condition if available, otherwise dataEditor
      on the dataset
  - roles/bigquery.jobUser at the project level
      required to run any query, in both US and asia-northeast1
This script does not apply any of the above; it only documents what is needed.

FIELD NAME STATUS (updated 2026-08-28 from independent BigQuery verification):
  Confirmed against real sf_Biblio__c / sf_BiblioContributor__c data:
    - sf_Biblio__c.product_code__c, sf_Biblio__c.e_publishing_code__c
    - sf_BiblioContributor__c.payee_code__c, .tax_withholding_type__c
    - sf_BiblioContributor__c.royalty_rate_1__c / .royalty_rate_2__c: when both are set
      and differ (1,060 rows checked), rate_2 > rate_1 always holds, and
      royalty_condition_amount_1__c / royalty_condition_quantity_1__c (the switch-over
      threshold) is set at the same time -- consistent with source_author_conditions'
      initial-rate-until-threshold-then-revised-rate structure. Mapped as rate_1 ->
      initial_royalty_rate, rate_2 -> revised_royalty_rate below. This is a data-pattern
      inference, not confirmed against Salesforce field documentation -- worth a
      one-line confirmation from someone who owns the Salesforce schema before this
      runs for real.
    - author_name__c / payee_name__c / revised_rate_sales_quantity__c /
      revised_rate_sales_amount__c DO NOT EXIST on sf_BiblioContributor__c (confirmed --
      querying them fails with an unknown-column error). The actual fields are
      contributor_name__c (used below for both author_name and payee_name, since
      source_author_conditions data confirms author name and payee name are always the
      same value there) and royalty_condition_quantity_1__c /
      royalty_condition_amount_1__c (the revised-rate threshold, used below for
      revised_rate_sales_quantity / revised_rate_sales_amount).
    - royalty_condition_quantity_1__c sometimes holds the literal value 9999999, which
      is very likely a "no threshold" sentinel rather than a real quantity; mapped to
      NULL via NULLIF below rather than copied verbatim.
  - Whether `ice_qb_source_salesforce` is a dataset (confirmed: yes, dataset).

source_author_conditions_ext's logical key is (product_code, payee_code), not
product_code alone -- confirmed against real data that a single product_code can have
up to ~10 source_author_conditions rows across several distinct payee_code values
(co-authored works). build_salesforce_match_sql therefore returns every matching
sf_BiblioContributor__c row per product_code (deduped only on (product_code,
payee_code), not collapsed to one row per product_code), and merge_relay_into_ext
checks for existing (product_code, payee_code) pairs rather than product_code alone.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from google.cloud import bigquery


DEFAULT_ADDED_BY = "sync_author_conditions_ext"

# royalty_condition_quantity_1__c uses this value to mean "no revised-rate threshold",
# not a literal quantity (confirmed against real data on 2026-08-28); mapped to NULL.
SALESFORCE_NO_THRESHOLD_SENTINEL = 9999999


def build_salesforce_match_sql(project_id: str, sf_dataset: str) -> str:
    return f"""
        SELECT
            b.product_code__c AS product_code
            , bc.Id AS biblio_contributor_id
            , bc.payee_code__c AS payee_code
            , bc.contributor_name__c AS author_name
            , bc.contributor_name__c AS payee_name
            , b.e_publishing_code__c AS electronic_publication_code
            , bc.royalty_rate_2__c AS revised_royalty_rate
            , bc.royalty_rate_1__c AS initial_royalty_rate
            , NULLIF(bc.royalty_condition_quantity_1__c, {SALESFORCE_NO_THRESHOLD_SENTINEL}) AS revised_rate_sales_quantity
            , NULLIF(bc.royalty_condition_amount_1__c, {SALESFORCE_NO_THRESHOLD_SENTINEL}) AS revised_rate_sales_amount
            , bc.tax_withholding_type__c AS withholding_tax_type
        FROM
            `{project_id}.{sf_dataset}.sf_Biblio__c` b
        INNER JOIN
            `{project_id}.{sf_dataset}.sf_BiblioContributor__c` bc
            ON bc.Biblio__c = b.Id
        WHERE
            b.product_code__c IN UNNEST(@product_codes)
            AND bc.payee_code__c IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY b.product_code__c, bc.payee_code__c
            ORDER BY bc.LastModifiedDate DESC
        ) = 1
    """


@dataclass(frozen=True)
class SyncResult:
    target_month: str
    unmatched_product_count: int
    sf_matched_count: int
    inserted_count: int
    unresolved_product_codes: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", default=os.getenv("GCP_PROJECT_ID", "ice-qb"))
    parser.add_argument("--source-dataset", default=os.getenv("BQ_SOURCE_DATASET", "royalty_source"))
    parser.add_argument("--source-location", default=os.getenv("SOURCE_LOCATION", "asia-northeast1"))
    parser.add_argument("--sf-dataset", default=os.getenv("SF_DATASET", "ice_qb_source"))
    parser.add_argument("--sf-location", default=os.getenv("SF_LOCATION", "US"))
    parser.add_argument("--relay-dataset", default=os.getenv("RELAY_DATASET", "ice_qb_source_salesforce"))
    parser.add_argument("--relay-table", default=os.getenv("RELAY_TABLE", "author_conditions_ext_candidates"))
    parser.add_argument("--target-month", default=os.getenv("JOB_TARGET_MONTH", ""))
    parser.add_argument("--added-by", default=os.getenv("SYNC_ADDED_BY", DEFAULT_ADDED_BY))
    parser.add_argument(
        "--unresolved-output",
        default=os.getenv("UNRESOLVED_OUTPUT_PATH", ""),
        help="Optional local CSV path for the unresolved product_code list (task 4/5 input).",
    )
    return parser.parse_args()


def validate_target_month(target_month: str) -> None:
    if len(target_month) != 6 or not target_month.isdigit():
        raise ValueError("target_month must use YYYYMM format")
    month = int(target_month[4:6])
    if month < 1 or month > 12:
        raise ValueError(f"invalid target month: {target_month}")


def get_unmatched_product_codes(
    client: bigquery.Client,
    project_id: str,
    source_dataset: str,
    location: str,
    target_month: str,
) -> list[str]:
    sql = f"""
        SELECT DISTINCT
            s.product_code
        FROM
            `{project_id}.{source_dataset}.source_monthly_product_sales` s
        WHERE
            s.target_month = @target_month
            AND s.product_code IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM `{project_id}.{source_dataset}.source_author_conditions` a
                WHERE a.product_code = s.product_code
            )
            AND NOT EXISTS (
                SELECT 1
                FROM `{project_id}.{source_dataset}.source_author_conditions_ext` e
                WHERE e.product_code = s.product_code
            )
        ORDER BY
            s.product_code
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("target_month", "STRING", target_month)]
    )
    rows = client.query(sql, job_config=job_config, location=location).result()
    return [row["product_code"] for row in rows]


def match_against_salesforce(
    client: bigquery.Client,
    project_id: str,
    sf_dataset: str,
    location: str,
    product_codes: list[str],
) -> pd.DataFrame:
    if not product_codes:
        return pd.DataFrame()
    sql = build_salesforce_match_sql(project_id, sf_dataset)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("product_codes", "STRING", product_codes)]
    )
    iterator = client.query(sql, job_config=job_config, location=location).result()
    columns = [field.name for field in iterator.schema]
    return pd.DataFrame.from_records([dict(row.items()) for row in iterator], columns=columns)


def load_relay_table(
    client: bigquery.Client,
    matched: pd.DataFrame,
    project_id: str,
    relay_dataset: str,
    relay_table: str,
    location: str,
) -> str:
    dataset = bigquery.Dataset(f"{project_id}.{relay_dataset}")
    dataset.location = location
    client.create_dataset(dataset, exists_ok=True)

    table_id = f"{project_id}.{relay_dataset}.{relay_table}"
    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
    client.load_table_from_dataframe(matched, table_id, job_config=job_config, location=location).result()
    return table_id


def merge_relay_into_ext(
    client: bigquery.Client,
    relay_table_id: str,
    project_id: str,
    source_dataset: str,
    location: str,
    added_by: str,
) -> int:
    sql = f"""
        INSERT INTO `{project_id}.{source_dataset}.source_author_conditions_ext` (
            product_code
            , electronic_publication_code
            , payee_code
            , author_name
            , payee_name
            , revised_royalty_rate
            , initial_royalty_rate
            , revised_rate_sales_quantity
            , revised_rate_sales_amount
            , withholding_tax_type
            , source_type
            , source_detail
            , added_by
            , added_at
        )
        SELECT
            r.product_code
            , r.electronic_publication_code
            , r.payee_code
            , r.author_name
            , r.payee_name
            , r.revised_royalty_rate
            , r.initial_royalty_rate
            , r.revised_rate_sales_quantity
            , r.revised_rate_sales_amount
            , r.withholding_tax_type
            , 'sf_auto'
            , r.biblio_contributor_id
            , @added_by
            , CURRENT_TIMESTAMP()
        FROM
            `{relay_table_id}` r
        WHERE
            NOT EXISTS (
                SELECT 1
                FROM `{project_id}.{source_dataset}.source_author_conditions_ext` e
                WHERE e.product_code = r.product_code AND e.payee_code = r.payee_code
            )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("added_by", "STRING", added_by)]
    )
    job = client.query(sql, job_config=job_config, location=location)
    job.result()
    return int(job.num_dml_affected_rows or 0)


def write_unresolved_output(path: str, product_codes: list[str]) -> None:
    if not path:
        return
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["product_code"])
        for product_code in product_codes:
            writer.writerow([product_code])


def write_unresolved_table(
    client: bigquery.Client,
    project_id: str,
    source_dataset: str,
    location: str,
    target_month: str,
    product_codes: list[str],
) -> None:
    """Persist the unresolved list so the admin console / Sheets path can read it.

    Rows are append-only history: a product_code resolved since it was last flagged
    (now present in source_author_conditions or source_author_conditions_ext) is marked
    resolved=TRUE rather than deleted, then this month's still-unresolved codes are
    (re)inserted as resolved=FALSE, replacing any stale unresolved rows for this month.
    """
    table_id = f"{project_id}.{source_dataset}.source_author_conditions_ext_unresolved"
    sql = f"""
        CREATE TABLE IF NOT EXISTS `{table_id}` (
            target_month STRING NOT NULL
            , product_code STRING NOT NULL
            , checked_at TIMESTAMP NOT NULL
            , resolved BOOL NOT NULL
        )
        PARTITION BY DATE(checked_at)
        CLUSTER BY target_month, resolved;

        UPDATE `{table_id}`
        SET resolved = TRUE
        WHERE
            resolved = FALSE
            AND (
                product_code IN (
                    SELECT product_code FROM `{project_id}.{source_dataset}.source_author_conditions`
                )
                OR product_code IN (
                    SELECT product_code FROM `{project_id}.{source_dataset}.source_author_conditions_ext`
                )
            );

        DELETE FROM `{table_id}`
        WHERE target_month = @target_month AND resolved = FALSE;

        INSERT INTO `{table_id}` (target_month, product_code, checked_at, resolved)
        SELECT @target_month, product_code, CURRENT_TIMESTAMP(), FALSE
        FROM UNNEST(@product_codes) AS product_code;
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_month", "STRING", target_month),
            bigquery.ArrayQueryParameter("product_codes", "STRING", product_codes),
        ]
    )
    client.query(sql, job_config=job_config, location=location).result()


def sync_author_conditions_ext(args: argparse.Namespace) -> SyncResult:
    validate_target_month(args.target_month)

    asia_client = bigquery.Client(project=args.project_id, location=args.source_location)
    us_client = bigquery.Client(project=args.project_id, location=args.sf_location)

    unmatched_product_codes = get_unmatched_product_codes(
        asia_client,
        args.project_id,
        args.source_dataset,
        args.source_location,
        args.target_month,
    )
    if not unmatched_product_codes:
        return SyncResult(args.target_month, 0, 0, 0, [])

    matched = match_against_salesforce(
        us_client,
        args.project_id,
        args.sf_dataset,
        args.sf_location,
        unmatched_product_codes,
    )

    inserted_count = 0
    if not matched.empty:
        relay_table_id = load_relay_table(
            asia_client,
            matched,
            args.project_id,
            args.relay_dataset,
            args.relay_table,
            args.source_location,
        )
        inserted_count = merge_relay_into_ext(
            asia_client,
            relay_table_id,
            args.project_id,
            args.source_dataset,
            args.source_location,
            args.added_by,
        )

    matched_product_codes = set(matched["product_code"]) if not matched.empty else set()
    unresolved_product_codes = sorted(set(unmatched_product_codes) - matched_product_codes)
    write_unresolved_table(
        asia_client,
        args.project_id,
        args.source_dataset,
        args.source_location,
        args.target_month,
        unresolved_product_codes,
    )
    write_unresolved_output(args.unresolved_output, unresolved_product_codes)

    return SyncResult(
        target_month=args.target_month,
        unmatched_product_count=len(unmatched_product_codes),
        sf_matched_count=len(matched_product_codes),
        inserted_count=inserted_count,
        unresolved_product_codes=unresolved_product_codes,
    )


def main() -> None:
    args = parse_args()
    result = sync_author_conditions_ext(args)
    print(
        json.dumps(
            {
                "target_month": result.target_month,
                "unmatched_product_count": result.unmatched_product_count,
                "sf_matched_count": result.sf_matched_count,
                "inserted_count": result.inserted_count,
                "unresolved_product_count": len(result.unresolved_product_codes),
                "unresolved_sample": result.unresolved_product_codes[:20],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
