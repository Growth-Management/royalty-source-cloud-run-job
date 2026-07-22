DECLARE v_target_month STRING DEFAULT '{{ target_month }}';
DECLARE v_accounting_month STRING DEFAULT REPLACE(v_target_month, '-', '');

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.access_input_author_conditions` AS
WITH latest_rows AS (
  SELECT *
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_ingest`
  WHERE target_month = v_target_month
    AND STARTS_WITH(source_file_name, '【著者条件一覧】')
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY source_file_id, source_sheet_name, row_number
    ORDER BY loaded_at DESC
  ) = 1
)
SELECT
  IF(NULLIF(TRIM(col_001), '') IS NULL, NULL, CONCAT('01-', TRIM(col_001))) AS product_key,
  NULLIF(TRIM(col_001), '') AS product_code,
  NULLIF(TRIM(col_002), '') AS electronic_publication_code,
  NULLIF(TRIM(col_003), '') AS author_identifier_id,
  NULLIF(TRIM(col_004), '') AS title,
  NULLIF(TRIM(col_005), '') AS planning_editor,
  NULLIF(TRIM(col_006), '') AS author_number,
  NULLIF(TRIM(col_007), '') AS author_category,
  NULLIF(TRIM(col_008), '') AS payee_code,
  NULLIF(TRIM(col_009), '') AS author_name,
  NULLIF(TRIM(col_010), '') AS name_kana,
  NULLIF(TRIM(col_011), '') AS payee_name,
  NULLIF(TRIM(col_012), '') AS author_name_kana,
  NULLIF(TRIM(col_013), '') AS payment_amount,
  NULLIF(TRIM(col_014), '') AS initial_royalty_rate,
  NULLIF(TRIM(col_015), '') AS revised_royalty_rate,
  NULLIF(TRIM(col_016), '') AS revised_rate_sales_quantity,
  NULLIF(TRIM(col_017), '') AS revised_rate_sales_amount,
  NULLIF(TRIM(col_018), '') AS payment_hold_limit_amount,
  NULLIF(TRIM(col_019), '') AS withholding_tax_type,
  NULLIF(TRIM(col_020), '') AS royalty_information_note,
  NULLIF(TRIM(col_021), '') AS product_type,
  NULLIF(TRIM(col_022), '') AS sales_start_date
FROM latest_rows
WHERE row_number > 1
  AND COALESCE(
    NULLIF(TRIM(col_001), ''),
    NULLIF(TRIM(col_002), ''),
    NULLIF(TRIM(col_003), ''),
    NULLIF(TRIM(col_004), '')
  ) IS NOT NULL
ORDER BY row_number;

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.access_input_sales` AS
WITH author_lookup AS (
  SELECT
    CONCAT('01-', TRIM(col_001)) AS product_key,
    NULLIF(TRIM(col_002), '') AS electronic_publication_code
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_ingest`
  WHERE target_month = v_target_month
    AND STARTS_WITH(source_file_name, '【著者条件一覧】')
    AND row_number > 1
    AND NULLIF(TRIM(col_001), '') IS NOT NULL
    AND NULLIF(TRIM(col_002), '') IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY CONCAT('01-', TRIM(col_001))
    ORDER BY loaded_at DESC, row_number
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
WHERE s.target_month = v_target_month
  -- POD sales are exported to the separate Amazon POD workbook.
  AND COALESCE(TRIM(s.product_type_name), '') != 'POD'
  -- The legacy workbook manually removes Amazon partner 941 return adjustments.
  AND NOT (
    TRIM(COALESCE(s.partner_company_code, '')) = '941'
    AND COALESCE(s.sales_quantity, 0) = 0
    AND COALESCE(s.sales_amount, 0) < 0
  );

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.access_input_store_detail` AS
WITH author_lookup AS (
  SELECT
    CONCAT('01-', TRIM(col_001)) AS product_key,
    NULLIF(TRIM(col_002), '') AS electronic_publication_code
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_ingest`
  WHERE target_month = v_target_month
    AND STARTS_WITH(source_file_name, '【著者条件一覧】')
    AND row_number > 1
    AND NULLIF(TRIM(col_001), '') IS NOT NULL
    AND NULLIF(TRIM(col_002), '') IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY CONCAT('01-', TRIM(col_001))
    ORDER BY loaded_at DESC, row_number
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