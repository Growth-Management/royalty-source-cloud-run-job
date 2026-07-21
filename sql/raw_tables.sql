CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ raw_dataset }}.raw_ingest` (
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

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ raw_dataset }}.raw_product_master` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  product_code STRING, base_isbn STRING, electronic_publication_code STRING, planning_editor STRING, sales_start_date STRING,
  title STRING, subtitle STRING, price STRING, isbn STRING, author STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, product_code;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ raw_dataset }}.raw_author_conditions` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  product_code STRING, electronic_publication_code STRING, author_identifier_id STRING, title STRING, planning_editor STRING,
  author_category STRING, payee_code STRING, author_name STRING, payee_name STRING, initial_royalty_rate STRING,
  revised_royalty_rate STRING, revised_rate_sales_quantity STRING, revised_rate_sales_amount STRING, payment_hold_limit_amount STRING,
  withholding_tax_type STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, product_code, electronic_publication_code;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ raw_dataset }}.raw_ep_statement_detail` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  accounting_month STRING, billing_code STRING, billing_name STRING, electronic_publication_code STRING, book_title STRING,
  list_price STRING, store_name STRING, sales_quantity STRING, value_code STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, electronic_publication_code, store_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ raw_dataset }}.raw_monthly_product_sales` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  accounting_month STRING, product_code STRING, product_name STRING, sales_company_code STRING, sales_company_name STRING,
  sales_department_code STRING, sales_department_name STRING, product_type_code STRING, product_type_name STRING, bibliographic_title STRING,
  billing_code STRING, billing_name STRING, ep_statement_code STRING, list_price STRING, sales_quantity STRING, sales_amount STRING,
  tax_amount STRING, tax_type STRING, electronic_publication_code STRING, partner_company_code STRING, partner_company_name STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, product_code, electronic_publication_code;
