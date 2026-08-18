DECLARE v_target_month STRING DEFAULT '{{ target_month }}';

-- Product master and author conditions are maintained as existing BigQuery
-- reference tables in royalty_source. Do not rebuild them from
-- ice_qb_aggregation here because that dataset is in a different location.

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.source_ep_statement_detail` AS
WITH latest_file AS (
    SELECT
        source_file_id
    FROM
        `{{ project_id }}.{{ raw_dataset }}.raw_ep_statement_detail`
    WHERE
        target_month = v_target_month
    GROUP BY
        source_file_id
    ORDER BY
        MAX(loaded_at) DESC
    LIMIT 1
)
, latest_raw AS (
    SELECT
        r.*
    FROM
        `{{ project_id }}.{{ raw_dataset }}.raw_ep_statement_detail` r
    INNER JOIN
        latest_file f
        USING (source_file_id)
    WHERE
        r.target_month = v_target_month
    QUALIFY
        ROW_NUMBER() OVER (
            PARTITION BY r.source_file_id, r.source_sheet_name, r.row_number
            ORDER BY r.loaded_at DESC
        ) = 1
)
SELECT
    target_month
    , row_number
    , NULLIF(TRIM(accounting_month), '') AS accounting_month
    , NULLIF(TRIM(billing_code), '') AS billing_code
    , NULLIF(TRIM(billing_name), '') AS billing_name
    , NULLIF(TRIM(electronic_publication_code), '') AS electronic_publication_code
    , NULLIF(TRIM(book_title), '') AS book_title
    , SAFE_CAST(REGEXP_REPLACE(list_price, r'[,￥¥ ]', '') AS NUMERIC) AS list_price
    , NULLIF(TRIM(store_name), '') AS store_name
    , SAFE_CAST(REGEXP_REPLACE(sales_quantity, r'[, ]', '') AS INT64) AS sales_quantity
    , NULLIF(TRIM(value_code), '') AS value_code
    , source_file_id
    , source_file_name
    , source_sheet_name
    , loaded_at
FROM
    latest_raw
WHERE
    COALESCE(
        NULLIF(TRIM(electronic_publication_code), '')
        , NULLIF(TRIM(book_title), '')
        , NULLIF(TRIM(store_name), '')
        , NULLIF(TRIM(value_code), '')
    ) IS NOT NULL;

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` AS
WITH latest_file AS (
    SELECT
        source_file_id
    FROM
        `{{ project_id }}.{{ raw_dataset }}.raw_monthly_product_sales`
    WHERE
        target_month = v_target_month
    GROUP BY
        source_file_id
    ORDER BY
        MAX(loaded_at) DESC
    LIMIT 1
)
, latest_raw AS (
    SELECT
        r.*
    FROM
        `{{ project_id }}.{{ raw_dataset }}.raw_monthly_product_sales` r
    INNER JOIN
        latest_file f
        USING (source_file_id)
    WHERE
        r.target_month = v_target_month
    QUALIFY
        ROW_NUMBER() OVER (
            PARTITION BY r.source_file_id, r.source_sheet_name, r.row_number
            ORDER BY r.loaded_at DESC
        ) = 1
)
SELECT
    s.target_month
    , s.row_number
    , NULLIF(TRIM(s.accounting_month), '') AS accounting_month
    , NULLIF(TRIM(s.product_code), '') AS product_code
    , CONCAT('01-', NULLIF(TRIM(s.product_code), '')) AS product_key
    , NULLIF(TRIM(s.product_name), '') AS product_name
    , NULLIF(TRIM(s.sales_company_code), '') AS sales_company_code
    , NULLIF(TRIM(s.sales_company_name), '') AS sales_company_name
    , NULLIF(TRIM(s.sales_department_code), '') AS sales_department_code
    , NULLIF(TRIM(s.sales_department_name), '') AS sales_department_name
    , NULLIF(TRIM(s.product_type_code), '') AS product_type_code
    , NULLIF(TRIM(s.product_type_name), '') AS product_type_name
    , NULLIF(TRIM(s.bibliographic_title), '') AS bibliographic_title
    , NULLIF(TRIM(s.billing_code), '') AS billing_code
    , NULLIF(TRIM(s.billing_name), '') AS billing_name
    , NULLIF(TRIM(s.ep_statement_code), '') AS ep_statement_code
    , SAFE_CAST(REGEXP_REPLACE(s.list_price, r'[,￥¥ ]', '') AS NUMERIC) AS list_price
    , SAFE_CAST(REGEXP_REPLACE(s.sales_quantity, r'[, ]', '') AS INT64) AS sales_quantity
    , SAFE_CAST(REGEXP_REPLACE(s.sales_amount, r'[,￥¥ ]', '') AS NUMERIC) AS sales_amount
    , SAFE_CAST(REGEXP_REPLACE(s.tax_amount, r'[,￥¥ ]', '') AS NUMERIC) AS tax_amount
    , NULLIF(TRIM(s.tax_type), '') AS tax_type
    , NULLIF(TRIM(s.electronic_publication_code), '') AS electronic_publication_code
    , NULLIF(TRIM(s.partner_company_code), '') AS partner_company_code
    , NULLIF(TRIM(s.partner_company_name), '') AS partner_company_name
    , s.source_file_id
    , s.source_file_name
    , s.source_sheet_name
    , s.loaded_at
FROM
    latest_raw s
WHERE
    COALESCE(
        NULLIF(TRIM(s.product_code), '')
        , NULLIF(TRIM(s.product_name), '')
        , NULLIF(TRIM(s.electronic_publication_code), '')
    ) IS NOT NULL;
