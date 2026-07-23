DECLARE v_target_month STRING DEFAULT '{{ target_month }}';
DECLARE v_accounting_month STRING DEFAULT REPLACE(v_target_month, '-', '');

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.access_input_author_conditions` AS
SELECT
  IF(product_code IS NULL, NULL, CONCAT('01-', product_code)) AS product_key,
  product_code,
  electronic_publication_code,
  author_identifier_id,
  title,
  planning_editor,
  CAST(NULL AS STRING) AS author_number,
  author_category,
  payee_code,
  author_name,
  CAST(NULL AS STRING) AS name_kana,
  payee_name,
  CAST(NULL AS STRING) AS author_name_kana,
  CAST(NULL AS STRING) AS payment_amount,
  CAST(initial_royalty_rate AS STRING) AS initial_royalty_rate,
  CAST(revised_royalty_rate AS STRING) AS revised_royalty_rate,
  CAST(revised_rate_sales_quantity AS STRING) AS revised_rate_sales_quantity,
  CAST(revised_rate_sales_amount AS STRING) AS revised_rate_sales_amount,
  CAST(payment_hold_limit_amount AS STRING) AS payment_hold_limit_amount,
  withholding_tax_type,
  CAST(NULL AS STRING) AS royalty_information_note,
  CAST(NULL AS STRING) AS product_type,
  CAST(NULL AS STRING) AS sales_start_date
FROM `{{ project_id }}.{{ source_dataset }}.source_author_conditions`
WHERE target_month = v_target_month
  AND COALESCE(product_code, electronic_publication_code, author_identifier_id, author_name) IS NOT NULL
ORDER BY row_number;

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
),
base_rows AS (
  SELECT
    v_accounting_month AS accounting_month_1,
    d.billing_code,
    d.billing_name,
    a.electronic_publication_code,
    d.book_title AS title,
    d.list_price,
    v_accounting_month AS accounting_month_2,
    CASE TRIM(d.store_name)
      WHEN 'Amazon' THEN 'Kindle'
      WHEN 'Reader Store' THEN 'SonyReaderStore'
      WHEN 'ブッコミ' THEN 'BookLive'
      WHEN 'Apple' THEN 'iBookstore'
      WHEN '紀伊國屋' THEN '紀伊國屋BookWebPlus'
      ELSE d.store_name
    END AS store_name,
    d.sales_quantity,
    d.value_code AS product_key,
    d.row_number AS source_row_number,
    d.source_file_id,
    d.source_file_name,
    d.loaded_at
  FROM `{{ project_id }}.{{ source_dataset }}.source_ep_statement_detail` d
  LEFT JOIN author_lookup a
    ON d.value_code = a.product_key
  WHERE d.target_month = v_target_month
    -- Amazon POD is exported to the separate POD workbook.
    AND TRIM(COALESCE(d.billing_code, '')) != '6191'
),
-- This row exists only in the legacy 2025-06 workbook and is not present in
-- the monthly source sheet. Keep it isolated as an auditable one-off correction.
legacy_manual_adjustments AS (
  SELECT
    '202506' AS accounting_month_1,
    '6101' AS billing_code,
    'シャープ株式会社' AS billing_name,
    '8443943011000000100e' AS electronic_publication_code,
    '怖いくらい通じる! 魔法のカタカナ英語 (1) 日常生活編' AS title,
    CAST(0 AS NUMERIC) AS list_price,
    '202505' AS accounting_month_2,
    'GALAPAGOS' AS store_name,
    CAST(0 AS NUMERIC) AS sales_quantity,
    '01-3715106045' AS product_key,
    CAST(NULL AS INT64) AS source_row_number,
    CAST(NULL AS STRING) AS source_file_id,
    'legacy_manual_adjustment' AS source_file_name,
    CURRENT_TIMESTAMP() AS loaded_at
  WHERE v_accounting_month = '202506'
)
SELECT * FROM base_rows
UNION ALL
SELECT * FROM legacy_manual_adjustments;
