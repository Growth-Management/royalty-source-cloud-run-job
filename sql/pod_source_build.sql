DECLARE v_target_month STRING DEFAULT '{{ target_month }}';
DECLARE v_dl_month STRING DEFAULT FORMAT_DATE('%Y%m', DATE_SUB(PARSE_DATE('%Y%m', v_target_month), INTERVAL 1 MONTH));

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.source_pod_sales_report` AS
WITH seed AS (
    SELECT
        target_month
        , publisher
        , product_code
        , isbn
        , title
        , unit_price
        , rate
        , quantity
        , net_amount
        , tax
        , sales_amount
        , manufacturing_cost
        , factor_15
        , factor_108
        , pages
        , dl_month
        , sales_month
        , source_kind
        , source_row_number
        , source_file_id
        , source_file_name
        , loaded_at
    FROM
        `{{ project_id }}.{{ source_dataset }}.pod_access_history_seed`
    WHERE
        target_month = v_target_month
)
, seed_state AS (
    SELECT
        COUNT(*) > 0 AS has_seed
    FROM
        seed
)
, amazon_latest_file AS (
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
, product_master_normalized AS (
    SELECT
        product_code
        , CASE
            WHEN REGEXP_CONTAINS(UPPER(TRIM(isbn)), r'E[+-]?\d+') THEN FORMAT('%.0f', SAFE_CAST(isbn AS FLOAT64))
            ELSE REGEXP_REPLACE(REGEXP_REPLACE(TRIM(isbn), r'\.0$', ''), r'[^0-9Xx]', '')
        END AS normalized_isbn
        , CASE
            WHEN REGEXP_CONTAINS(UPPER(TRIM(base_isbn)), r'E[+-]?\d+') THEN FORMAT('%.0f', SAFE_CAST(base_isbn AS FLOAT64))
            ELSE REGEXP_REPLACE(REGEXP_REPLACE(TRIM(base_isbn), r'\.0$', ''), r'[^0-9Xx]', '')
        END AS normalized_base_isbn
        , TRIM(title) AS master_title
        , REGEXP_REPLACE(
            NORMALIZE_AND_CASEFOLD(TRIM(title))
            , r'[\p{P}\p{Z}\p{S}]'
            , ''
        ) AS normalized_title
        , NULLIF(TRIM(electronic_publication_code), '') AS electronic_publication_code
    FROM
        `{{ project_id }}.{{ source_dataset }}.source_product_master`
)
, product_master_title_isbn AS (
    SELECT
        normalized_title
        , ANY_VALUE(NULLIF(normalized_base_isbn, '')) AS normalized_title_isbn
    FROM
        product_master_normalized
    WHERE
        NULLIF(normalized_base_isbn, '') IS NOT NULL
    GROUP BY
        normalized_title
    HAVING
        COUNT(DISTINCT normalized_base_isbn) = 1
)
, product_master_physical AS (
    SELECT
        p.product_code
        , COALESCE(NULLIF(p.normalized_isbn, ''), t.normalized_title_isbn) AS normalized_isbn
        , p.master_title
        , p.normalized_title
    FROM
        product_master_normalized p
    LEFT JOIN
        product_master_title_isbn t
        USING (normalized_title)
    WHERE
        p.electronic_publication_code IS NULL
        AND NULLIF(p.normalized_base_isbn, '') IS NULL
        AND NULLIF(TRIM(p.product_code), '') IS NOT NULL
)
, amazon AS (
    SELECT
        v_target_month AS target_month
        , COALESCE(NULLIF(TRIM(publisher), ''), 'ICE') AS publisher
        , TRIM(product_code) AS product_code
        , CASE
            WHEN REGEXP_CONTAINS(UPPER(TRIM(isbn)), r'E[+-]?\d+') THEN FORMAT('%.0f', SAFE_CAST(isbn AS FLOAT64))
            ELSE REGEXP_REPLACE(REGEXP_REPLACE(TRIM(isbn), r'\.0$', ''), r'[^0-9Xx]', '')
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
            ELSE REGEXP_REPLACE(REGEXP_REPLACE(TRIM(isbn), r'\.0$', ''), r'[^0-9Xx]', '')
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
, monthly_unenriched AS (
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
        pf
)
, monthly_match_candidates AS (
    SELECT
        m.*
        , pm.product_code AS master_product_code
        , pm.normalized_isbn AS master_isbn
        , pm.master_title
        , COUNTIF(
            pm.product_code IS NOT NULL
            AND NULLIF(m.isbn, '') IS NOT NULL
            AND m.isbn = pm.normalized_isbn
        ) OVER source_row AS exact_isbn_match_count
        , COUNTIF(
            pm.product_code IS NOT NULL
            AND REGEXP_REPLACE(
                NORMALIZE_AND_CASEFOLD(TRIM(m.title))
                , r'[\p{P}\p{Z}\p{S}]'
                , ''
            ) = pm.normalized_title
        ) OVER source_row AS normalized_title_match_count
        , ROW_NUMBER() OVER (
            source_row
            ORDER BY
                IF(
                    NULLIF(m.isbn, '') IS NOT NULL
                    AND m.isbn = pm.normalized_isbn
                    , 2
                    , 1
                ) DESC
                , pm.product_code
        ) AS match_rank
    FROM
        monthly_unenriched m
    LEFT JOIN
        product_master_physical pm
        ON NULLIF(TRIM(m.product_code), '') IS NULL
        AND (
            (
                NULLIF(m.isbn, '') IS NOT NULL
                AND m.isbn = pm.normalized_isbn
            )
            OR (
                NULLIF(
                    REGEXP_REPLACE(
                        NORMALIZE_AND_CASEFOLD(TRIM(m.title))
                        , r'[\p{P}\p{Z}\p{S}]'
                        , ''
                    )
                    , ''
                ) IS NOT NULL
                AND REGEXP_REPLACE(
                    NORMALIZE_AND_CASEFOLD(TRIM(m.title))
                    , r'[\p{P}\p{Z}\p{S}]'
                    , ''
                ) = pm.normalized_title
            )
        )
    WINDOW source_row AS (
        PARTITION BY m.source_kind, m.source_file_id, m.source_row_number
    )
)
, monthly_enriched AS (
    SELECT
        target_month
        , publisher
        , CASE
            WHEN NULLIF(TRIM(product_code), '') IS NOT NULL THEN TRIM(product_code)
            WHEN exact_isbn_match_count = 1 THEN master_product_code
            WHEN exact_isbn_match_count = 0 AND normalized_title_match_count = 1 THEN master_product_code
            ELSE ''
        END AS product_code
        , CASE
            WHEN exact_isbn_match_count = 1 THEN COALESCE(NULLIF(master_isbn, ''), isbn)
            WHEN exact_isbn_match_count = 0 AND normalized_title_match_count = 1
                THEN COALESCE(NULLIF(master_isbn, ''), isbn)
            ELSE isbn
        END AS isbn
        , CASE
            WHEN exact_isbn_match_count = 1 THEN COALESCE(NULLIF(master_title, ''), title)
            WHEN exact_isbn_match_count = 0 AND normalized_title_match_count = 1
                THEN COALESCE(NULLIF(master_title, ''), title)
            ELSE title
        END AS title
        , unit_price
        , rate
        , quantity
        , net_amount
        , tax
        , sales_amount
        , manufacturing_cost
        , factor_15
        , factor_108
        , pages
        , dl_month
        , sales_month
        , source_kind
        , source_row_number
        , source_file_id
        , source_file_name
        , loaded_at
    FROM
        monthly_match_candidates
    WHERE
        match_rank = 1
)
SELECT
    *
FROM
    seed
UNION ALL
SELECT
    m.*
FROM
    monthly_enriched m
CROSS JOIN
    seed_state s
WHERE
    NOT s.has_seed;
