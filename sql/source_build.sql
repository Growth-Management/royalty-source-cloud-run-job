DECLARE v_target_month STRING DEFAULT '{{ target_month }}';

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.source_product_master` AS
SELECT
  target_month, row_number,
  NULLIF(TRIM(product_code), '') AS product_code,
  NULLIF(TRIM(base_isbn), '') AS base_isbn,
  NULLIF(TRIM(electronic_publication_code), '') AS electronic_publication_code,
  NULLIF(TRIM(planning_editor), '') AS planning_editor,
  COALESCE(SAFE.PARSE_DATE('%Y/%m/%d', sales_start_date), SAFE.PARSE_DATE('%Y-%m-%d', sales_start_date), SAFE.PARSE_DATE('%Y%m%d', sales_start_date)) AS sales_start_date,
  NULLIF(TRIM(title), '') AS title,
  NULLIF(TRIM(subtitle), '') AS subtitle,
  SAFE_CAST(REGEXP_REPLACE(price, r'[,￥¥ ]', '') AS NUMERIC) AS price,
  NULLIF(TRIM(isbn), '') AS isbn,
  NULLIF(TRIM(author), '') AS author,
  source_file_id, source_file_name, source_sheet_name, loaded_at
FROM `{{ project_id }}.{{ raw_dataset }}.raw_product_master`
WHERE target_month = v_target_month
  AND NULLIF(TRIM(product_code), '') IS NOT NULL
  AND NULLIF(TRIM(title), '') IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY source_file_id, source_sheet_name, row_number ORDER BY loaded_at DESC) = 1;

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.source_author_conditions` AS
SELECT
  target_month, row_number,
  NULLIF(TRIM(product_code), '') AS product_code,
  NULLIF(TRIM(electronic_publication_code), '') AS electronic_publication_code,
  NULLIF(TRIM(author_identifier_id), '') AS author_identifier_id,
  NULLIF(TRIM(title), '') AS title,
  NULLIF(TRIM(planning_editor), '') AS planning_editor,
  NULLIF(TRIM(author_category), '') AS author_category,
  NULLIF(TRIM(payee_code), '') AS payee_code,
  NULLIF(TRIM(author_name), '') AS author_name,
  NULLIF(TRIM(payee_name), '') AS payee_name,
  SAFE_CAST(REGEXP_REPLACE(initial_royalty_rate, r'[%％, ]', '') AS NUMERIC) AS initial_royalty_rate,
  SAFE_CAST(REGEXP_REPLACE(revised_royalty_rate, r'[%％, ]', '') AS NUMERIC) AS revised_royalty_rate,
  SAFE_CAST(REGEXP_REPLACE(revised_rate_sales_quantity, r'[, ]', '') AS INT64) AS revised_rate_sales_quantity,
  SAFE_CAST(REGEXP_REPLACE(revised_rate_sales_amount, r'[,￥¥ ]', '') AS NUMERIC) AS revised_rate_sales_amount,
  SAFE_CAST(REGEXP_REPLACE(payment_hold_limit_amount, r'[,￥¥ ]', '') AS NUMERIC) AS payment_hold_limit_amount,
  NULLIF(TRIM(withholding_tax_type), '') AS withholding_tax_type,
  source_file_id, source_file_name, source_sheet_name, loaded_at
FROM `{{ project_id }}.{{ raw_dataset }}.raw_author_conditions`
WHERE target_month = v_target_month
  AND NULLIF(TRIM(product_code), '') IS NOT NULL
  AND NULLIF(TRIM(author_name), '') IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY source_file_id, source_sheet_name, row_number ORDER BY loaded_at DESC) = 1;

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.source_ep_statement_detail` AS
SELECT
  target_month, row_number,
  NULLIF(TRIM(accounting_month), '') AS accounting_month,
  NULLIF(TRIM(billing_code), '') AS billing_code,
  NULLIF(TRIM(billing_name), '') AS billing_name,
  NULLIF(TRIM(electronic_publication_code), '') AS electronic_publication_code,
  NULLIF(TRIM(book_title), '') AS book_title,
  SAFE_CAST(REGEXP_REPLACE(list_price, r'[,￥¥ ]', '') AS NUMERIC) AS list_price,
  NULLIF(TRIM(store_name), '') AS store_name,
  SAFE_CAST(REGEXP_REPLACE(sales_quantity, r'[, ]', '') AS INT64) AS sales_quantity,
  NULLIF(TRIM(value_code), '') AS value_code,
  source_file_id, source_file_name, source_sheet_name, loaded_at
FROM `{{ project_id }}.{{ raw_dataset }}.raw_ep_statement_detail`
WHERE target_month = v_target_month
  AND NULLIF(TRIM(electronic_publication_code), '') IS NOT NULL
  AND NULLIF(TRIM(store_name), '') IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY source_file_id, source_sheet_name, row_number ORDER BY loaded_at DESC) = 1;

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` AS
WITH latest_raw AS (
  SELECT *
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_monthly_product_sales`
  WHERE target_month = v_target_month
  QUALIFY ROW_NUMBER() OVER (PARTITION BY source_file_id, source_sheet_name, row_number ORDER BY loaded_at DESC) = 1
)
SELECT
  s.target_month, s.row_number,
  NULLIF(TRIM(s.accounting_month), '') AS accounting_month,
  NULLIF(TRIM(s.product_code), '') AS product_code,
  CONCAT('01-', NULLIF(TRIM(s.product_code), '')) AS product_key,
  NULLIF(TRIM(s.product_name), '') AS product_name,
  NULLIF(TRIM(s.sales_company_code), '') AS sales_company_code,
  NULLIF(TRIM(s.sales_company_name), '') AS sales_company_name,
  NULLIF(TRIM(s.sales_department_code), '') AS sales_department_code,
  NULLIF(TRIM(s.sales_department_name), '') AS sales_department_name,
  NULLIF(TRIM(s.product_type_code), '') AS product_type_code,
  NULLIF(TRIM(s.product_type_name), '') AS product_type_name,
  NULLIF(TRIM(s.bibliographic_title), '') AS bibliographic_title,
  NULLIF(TRIM(s.billing_code), '') AS billing_code,
  NULLIF(TRIM(s.billing_name), '') AS billing_name,
  NULLIF(TRIM(s.ep_statement_code), '') AS ep_statement_code,
  SAFE_CAST(REGEXP_REPLACE(s.list_price, r'[,￥¥ ]', '') AS NUMERIC) AS list_price,
  SAFE_CAST(REGEXP_REPLACE(s.sales_quantity, r'[, ]', '') AS INT64) AS sales_quantity,
  SAFE_CAST(REGEXP_REPLACE(s.sales_amount, r'[,￥¥ ]', '') AS NUMERIC) AS sales_amount,
  SAFE_CAST(REGEXP_REPLACE(s.tax_amount, r'[,￥¥ ]', '') AS NUMERIC) AS tax_amount,
  NULLIF(TRIM(s.tax_type), '') AS tax_type,
  NULLIF(TRIM(s.electronic_publication_code), '') AS electronic_publication_code,
  NULLIF(TRIM(s.partner_company_code), '') AS partner_company_code,
  NULLIF(TRIM(s.partner_company_name), '') AS partner_company_name,
  s.source_file_id, s.source_file_name, s.source_sheet_name, s.loaded_at
FROM latest_raw s
WHERE NULLIF(TRIM(s.product_code), '') IS NOT NULL;