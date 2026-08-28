-- Extension master for source_author_conditions.
--
-- source_author_conditions is frozen (Excel-origin, single load on 2026-07-22, not
-- maintained since). New product_code -> electronic_publication_code / payee mappings
-- discovered after the freeze are added here instead of touching the frozen table, via:
--   - monthly Salesforce auto-sync (source_type = 'sf_auto', see
--     scripts/sync_author_conditions_ext.py)
--   - manual entry via Google Sheets or the royalty-source-admin console
--     (source_type = 'manual')
--
-- This file is NOT wired into app/pipeline.py and is not executed automatically by the
-- Cloud Run Job. Apply it once manually (bq query / console) before sql/access_input_build.sql
-- is run for a month that relies on source_author_conditions_ext lookups.
--
-- Logical key is (product_code, payee_code), NOT product_code alone: source_author_conditions
-- itself has products with up to 10 rows / multiple distinct payee_code values (co-authored
-- works with several payees), confirmed against real BigQuery data on 2026-08-28. A
-- product_code-only key would silently drop every payee but one on a co-authored work. This
-- does not affect the electronic_publication_code lookup in access_input_build.sql, which
-- dedups per product_code on purpose -- electronic_publication_code is confirmed constant
-- across all rows of a given product_code even when payee_code varies.

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ source_dataset }}.source_author_conditions_ext` (
    product_code STRING NOT NULL
    , electronic_publication_code STRING
    , payee_code STRING NOT NULL
    , author_name STRING
    , payee_name STRING
    , revised_royalty_rate NUMERIC
    , initial_royalty_rate NUMERIC
    , revised_rate_sales_quantity INT64
    , revised_rate_sales_amount NUMERIC
    , withholding_tax_type STRING
    , source_type STRING NOT NULL
    , source_detail STRING
    , added_by STRING
    , added_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(added_at)
CLUSTER BY product_code, payee_code;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ source_dataset }}.source_author_conditions_ext_staging` (
    product_code STRING
    , electronic_publication_code STRING
    , payee_code STRING
    , author_name STRING
    , payee_name STRING
    , revised_royalty_rate NUMERIC
    , initial_royalty_rate NUMERIC
    , revised_rate_sales_quantity INT64
    , revised_rate_sales_amount NUMERIC
    , withholding_tax_type STRING
    , source_type STRING
    , source_detail STRING
    , added_by STRING
    , added_at TIMESTAMP
    , validated BOOL
)
PARTITION BY DATE(added_at)
CLUSTER BY product_code, validated;

-- Written by scripts/sync_author_conditions_ext.py: product_code values seen in a
-- month's sales with no source_author_conditions / source_author_conditions_ext match
-- and no Salesforce auto-match either. Read by royalty-source-admin
-- pages/author_conditions_ext.py and by whoever populates the manual-entry Sheet.
CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ source_dataset }}.source_author_conditions_ext_unresolved` (
    target_month STRING NOT NULL
    , product_code STRING NOT NULL
    , checked_at TIMESTAMP NOT NULL
    , resolved BOOL NOT NULL
)
PARTITION BY DATE(checked_at)
CLUSTER BY target_month, resolved;
