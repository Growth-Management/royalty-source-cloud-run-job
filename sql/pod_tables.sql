CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ staging_dataset }}.staging_amazon_pod_monthly` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  raw_publisher STRING, raw_product_code STRING, raw_isbn STRING, raw_title STRING, raw_unit_price STRING, raw_rate STRING,
  raw_quantity STRING, raw_net_amount STRING, raw_tax STRING, raw_sales_amount STRING, raw_manufacturing_cost STRING,
  raw_factor_15 STRING, raw_factor_108 STRING, raw_pages STRING, raw_dl_month STRING, raw_sales_month STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ raw_dataset }}.raw_amazon_pod_monthly` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  publisher STRING, product_code STRING, isbn STRING, title STRING, unit_price STRING, rate STRING,
  quantity STRING, net_amount STRING, tax STRING, sales_amount STRING, manufacturing_cost STRING,
  factor_15 STRING, factor_108 STRING, pages STRING, dl_month STRING, sales_month STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ staging_dataset }}.staging_pf_sales_report` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  raw_category STRING, raw_publisher_id STRING, raw_isbn STRING, raw_title STRING, raw_sales_month STRING, raw_store STRING,
  raw_unit_price STRING, raw_quantity STRING, raw_distribution_rate STRING, raw_sales_amount STRING, raw_sales_fee STRING,
  raw_printing_unit_cost STRING, raw_printing_cost STRING, raw_exchange_rate STRING, raw_payment_status STRING, raw_payment_amount STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ raw_dataset }}.raw_pf_sales_report` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  category STRING, publisher_id STRING, isbn STRING, title STRING, sales_month STRING, store STRING,
  unit_price STRING, quantity STRING, distribution_rate STRING, sales_amount STRING, sales_fee STRING,
  printing_unit_cost STRING, printing_cost STRING, exchange_rate STRING, payment_status STRING, payment_amount STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ staging_dataset }}.staging_pod_access_history` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  raw_publisher STRING, raw_product_code STRING, raw_isbn STRING, raw_title STRING, raw_unit_price STRING, raw_rate STRING,
  raw_quantity STRING, raw_net_amount STRING, raw_tax STRING, raw_sales_amount STRING, raw_manufacturing_cost STRING,
  raw_factor_15 STRING, raw_factor_108 STRING, raw_pages STRING, raw_dl_month STRING, raw_sales_month STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;

CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ raw_dataset }}.raw_pod_access_history` (
  row_number INT64, target_month STRING, loaded_at TIMESTAMP, source_file_id STRING, source_file_name STRING, source_sheet_name STRING,
  publisher STRING, product_code STRING, isbn STRING, title STRING, unit_price STRING, rate STRING,
  quantity STRING, net_amount STRING, tax STRING, sales_amount STRING, manufacturing_cost STRING,
  factor_15 STRING, factor_108 STRING, pages STRING, dl_month STRING, sales_month STRING
) PARTITION BY DATE(loaded_at) CLUSTER BY target_month, source_file_name;

-- One-time initial history copied from the legacy Access-derived BigQuery table
-- ice_qb_source_p1.pod_sales_summary (US) into the royalty processing region.
CREATE TABLE IF NOT EXISTS `{{ project_id }}.{{ source_dataset }}.pod_access_history_seed` (
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
) CLUSTER BY target_month, source_kind;
