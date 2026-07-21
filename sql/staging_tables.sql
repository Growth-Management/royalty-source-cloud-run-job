CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ staging_dataset }}.staging_ingest` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP,
  source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  row_payload_json STRING,
  col_001 STRING, col_002 STRING, col_003 STRING, col_004 STRING, col_005 STRING,
  col_006 STRING, col_007 STRING, col_008 STRING, col_009 STRING, col_010 STRING,
  col_011 STRING, col_012 STRING, col_013 STRING, col_014 STRING, col_015 STRING,
  col_016 STRING, col_017 STRING, col_018 STRING, col_019 STRING, col_020 STRING,
  col_021 STRING, col_022 STRING, col_023 STRING, col_024 STRING, col_025 STRING,
  col_026 STRING, col_027 STRING, col_028 STRING, col_029 STRING, col_030 STRING,
  col_031 STRING, col_032 STRING, col_033 STRING, col_034 STRING, col_035 STRING,
  col_036 STRING, col_037 STRING, col_038 STRING, col_039 STRING, col_040 STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name, source_sheet_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ staging_dataset }}.staging_product_master` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  raw_product_code STRING, raw_base_isbn STRING, raw_electronic_publication_code STRING, raw_planning_editor STRING,
  raw_sales_start_date STRING, raw_title STRING, raw_subtitle STRING, raw_price STRING, raw_isbn STRING, raw_author STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ staging_dataset }}.staging_author_conditions` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  raw_product_code STRING, raw_electronic_publication_code STRING, raw_author_identifier_id STRING, raw_title STRING, raw_planning_editor STRING,
  raw_author_category STRING, raw_payee_code STRING, raw_author_name STRING, raw_payee_name STRING, raw_initial_royalty_rate STRING,
  raw_revised_royalty_rate STRING, raw_revised_rate_sales_quantity STRING, raw_revised_rate_sales_amount STRING,
  raw_payment_hold_limit_amount STRING, raw_withholding_tax_type STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ staging_dataset }}.staging_ep_statement_detail` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  raw_accounting_month STRING, raw_billing_code STRING, raw_billing_name STRING, raw_electronic_publication_code STRING,
  raw_book_title STRING, raw_list_price STRING, raw_store_name STRING, raw_sales_quantity STRING, raw_value_code STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ staging_dataset }}.staging_monthly_product_sales` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  raw_accounting_month STRING, raw_product_code STRING, raw_product_name STRING, raw_sales_company_code STRING, raw_sales_company_name STRING,
  raw_sales_department_code STRING, raw_sales_department_name STRING, raw_product_type_code STRING, raw_product_type_name STRING,
  raw_bibliographic_title STRING, raw_billing_code STRING, raw_billing_name STRING, raw_ep_statement_code STRING, raw_list_price STRING,
  raw_sales_quantity STRING, raw_sales_amount STRING, raw_tax_amount STRING, raw_tax_type STRING, raw_electronic_publication_code STRING,
  raw_partner_company_code STRING, raw_partner_company_name STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;
