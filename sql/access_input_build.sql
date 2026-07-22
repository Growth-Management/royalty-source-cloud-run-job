DECLARE v_target_month STRING DEFAULT '{{ target_month }}';
DECLARE v_accounting_month STRING DEFAULT REPLACE(v_target_month, '-', '');

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.access_input_sales` AS
WITH author_lookup AS (
  SELECT
    CONCAT('01-', product_code) AS product_key,
    electronic_publication_code
  FROM `{{ project_id }}.{{ source_dataset }}.source_author_conditions`
  WHERE target_month = v_target_month
    AND product_code IS NOT NULL
    AND electronic_publication_code IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY CONCAT('01-', product_code)
    ORDER BY row_number
  ) = 1
)
SELECT
  v_accounting_month AS accounting_month_1,
  v_accounting_month AS accounting_month_2,
  s.billing_code AS billing_code_1,
  s.billing_name AS billing_name_1,
  s.billing_code AS billing_code_2,
  s.billing_name AS billing_name_2,
  s.product_code,
  s.product_key,
  a.electronic_publication_code,
  s.bibliographic_title AS title,
  s.list_price AS list_price_1,
  CAST(NULL AS STRING) AS auxiliary_a,
  s.list_price AS list_price_2,
  s.sales_quantity AS sales_quantity_1,
  s.sales_amount AS sales_amount_1,
  s.list_price AS list_price_3,
  s.sales_quantity AS sales_quantity_2,
  s.list_price AS list_price_4,
  s.sales_amount AS sales_amount_2,
  CAST(NULL AS STRING) AS device,
  CAST(NULL AS NUMERIC) AS exchange_rate_1,
  CAST(NULL AS NUMERIC) AS exchange_rate_2,
  s.row_number AS source_row_number,
  s.source_file_id,
  s.source_file_name,
  s.loaded_at
FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` s
LEFT JOIN author_lookup a USING (product_key)
WHERE s.target_month = v_target_month;

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.access_input_store_detail` AS
WITH author_lookup AS (
  SELECT
    CONCAT('01-', product_code) AS product_key,
    electronic_publication_code
  FROM `{{ project_id }}.{{ source_dataset }}.source_author_conditions`
  WHERE target_month = v_target_month
    AND product_code IS NOT NULL
    AND electronic_publication_code IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY CONCAT('01-', product_code)
    ORDER BY row_number
  ) = 1
)
SELECT
  v_accounting_month AS accounting_month_1,
  d.billing_code,
  d.billing_name,
  a.electronic_publication_code,
  d.book_title AS title,
  d.list_price,
  v_accounting_month AS accounting_month_2,
  d.store_name,
  d.sales_quantity,
  d.value_code AS product_key,
  d.row_number AS source_row_number,
  d.source_file_id,
  d.source_file_name,
  d.loaded_at
FROM `{{ project_id }}.{{ source_dataset }}.source_ep_statement_detail` d
LEFT JOIN author_lookup a
  ON d.value_code = a.product_key
WHERE d.target_month = v_target_month;