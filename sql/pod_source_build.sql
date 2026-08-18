DECLARE v_target_month STRING DEFAULT '{{ target_month }}';
DECLARE v_dl_month STRING DEFAULT FORMAT_DATE('%Y%m', DATE_SUB(PARSE_DATE('%Y%m', v_target_month), INTERVAL 1 MONTH));

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.source_pod_sales_report` AS
WITH amazon_latest_file AS (
    SELECT
        source_file_id
    FROM
        `{{ project_id }}.{{ raw_dataset }}.raw_amazon_pod_monthly`
    WHERE
        target_month = v_target_month
        AND NOT REGEXP_CONTAINS(COALESCE(source_sheet_name, ''), r'転記用')
    GROUP BY
        source_file_id
    ORDER BY
        MAX(loaded_at) DESC
    LIMIT 1
)
, amazon_raw AS (
    SELECT
        r.*
    FROM
        `{{ project_id }}.{{ raw_dataset }}.raw_amazon_pod_monthly` r
    INNER JOIN
        amazon_latest_file f
        USING (source_file_id)
    WHERE
        r.target_month = v_target_month
        AND NOT REGEXP_CONTAINS(COALESCE(r.source_sheet_name, ''), r'転記用')
    QUALIFY
        ROW_NUMBER() OVER (
            PARTITION BY r.source_file_id, r.source_sheet_name, r.row_number
            ORDER BY r.loaded_at DESC
        ) = 1
)
, pf_latest_file AS (
    SELECT
        source_file_id
    FROM
        `{{ project_id }}.{{ raw_dataset }}.raw_pf_sales_report`
    WHERE
        target_month = v_target_month
    GROUP BY
        source_file_id
    ORDER BY
        MAX(loaded_at) DESC
    LIMIT 1
)
, pf_raw AS (
    SELECT
        r.*
    FROM
        `{{ project_id }}.{{ raw_dataset }}.raw_pf_sales_report` r
    INNER JOIN
        pf_latest_file f
        USING (source_file_id)
    WHERE
        r.target_month = v_target_month
    QUALIFY
        ROW_NUMBER() OVER (
            PARTITION BY r.source_file_id, r.row_number
            ORDER BY r.loaded_at DESC
        ) = 1
)
, amazon AS (
    SELECT
        v_target_month AS target_month
        , COALESCE(NULLIF(TRIM(publisher), ''), 'ICE') AS publisher
        , TRIM(product_code) AS product_code
        , CASE
            WHEN REGEXP_CONTAINS(UPPER(TRIM(isbn)), r'E[+-]?\d+') THEN FORMAT('%.0f', SAFE_CAST(isbn AS FLOAT64))
            ELSE REGEXP_REPLACE(TRIM(isbn), r'[^0-9Xx]', '')
        END AS isbn
        , TRIM(title) AS title
        , SAFE_CAST(REGEXP_REPLACE(unit_price, r'[^0-9.\-]', '') AS NUMERIC) AS unit_price
        , TRIM(rate) AS rate
        , SAFE_CAST(REGEXP_REPLACE(quantity, r'[^0-9\-]', '') AS INT64) AS quantity
        , SAFE_CAST(REGEXP_REPLACE(net_amount, r'[^0-9.\-]', '') AS NUMERIC) AS net_amount
        , SAFE_CAST(REGEXP_REPLACE(tax, r'[^0-9.\-]', '') AS NUMERIC) AS tax
        , SAFE_CAST(REGEXP_REPLACE(sales_amount, r'[^0-9.\-]', '') AS NUMERIC) AS sales_amount
        , SAFE_CAST(REGEXP_REPLACE(manufacturing_cost, r'[^0-9.\-]', '') AS NUMERIC) AS manufacturing_cost
        , SAFE_CAST(REGEXP_REPLACE(factor_15, r'[^0-9.\-]', '') AS NUMERIC) AS factor_15
        , SAFE_CAST(REGEXP_REPLACE(factor_108, r'[^0-9.\-]', '') AS NUMERIC) AS factor_108
        , SAFE_CAST(REGEXP_REPLACE(pages, r'[^0-9\-]', '') AS INT64) AS pages
        , v_dl_month AS dl_month
        , v_target_month AS sales_month
        , 'amazon_pod_monthly' AS source_kind
        , row_number AS source_row_number
        , source_file_id
        , source_file_name
        , loaded_at
    FROM
        amazon_raw
)
, pf AS (
    SELECT
        v_target_month AS target_month
        , 'ICE' AS publisher
        , '' AS product_code
        , CASE
            WHEN REGEXP_CONTAINS(UPPER(TRIM(isbn)), r'E[+-]?\d+') THEN FORMAT('%.0f', SAFE_CAST(isbn AS FLOAT64))
            ELSE REGEXP_REPLACE(TRIM(isbn), r'[^0-9Xx]', '')
        END AS isbn
        , TRIM(title) AS title
        , SAFE_CAST(REGEXP_REPLACE(unit_price, r'[^0-9.\-]', '') AS NUMERIC) AS unit_price
        , '' AS rate
        , SAFE_CAST(REGEXP_REPLACE(quantity, r'[^0-9\-]', '') AS INT64) AS quantity
        , SAFE_CAST(REGEXP_REPLACE(sales_amount, r'[^0-9.\-]', '') AS NUMERIC) AS net_amount
        , CAST(0 AS NUMERIC) AS tax
        , SAFE_CAST(REGEXP_REPLACE(sales_amount, r'[^0-9.\-]', '') AS NUMERIC) AS sales_amount
        , COALESCE(SAFE_CAST(REGEXP_REPLACE(sales_fee, r'[^0-9.\-]', '') AS NUMERIC), 0)
            + COALESCE(SAFE_CAST(REGEXP_REPLACE(printing_cost, r'[^0-9.\-]', '') AS NUMERIC), 0) AS manufacturing_cost
        , CAST(1.5 AS NUMERIC) AS factor_15
        , CAST(108 AS NUMERIC) AS factor_108
        , CAST(NULL AS INT64) AS pages
        , v_dl_month AS dl_month
        , v_target_month AS sales_month
        , 'pf_sales_report' AS source_kind
        , row_number AS source_row_number
        , source_file_id
        , source_file_name
        , loaded_at
    FROM
        pf_raw
    WHERE
        COALESCE(SAFE_CAST(REGEXP_REPLACE(quantity, r'[^0-9\-]', '') AS INT64), 0) != 0
)
SELECT
    *
FROM
    amazon
WHERE
    COALESCE(quantity, 0) != 0
UNION ALL
SELECT
    *
FROM
    pf;
