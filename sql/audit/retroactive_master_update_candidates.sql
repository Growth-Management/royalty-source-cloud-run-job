-- RETROACTIVE_MASTER_UPDATE candidate monitor (READ-ONLY).
--
-- Lists past-month production wholesale rows whose digital_pub_code was '#N/A'
-- at monthly-processing time, but whose product now has BOTH:
--   1. a row in the current production catalog master, and
--   2. one or more rows in the current production author-condition master.
--
-- The monitoring baseline is ice_qb_source_p1, not royalty_cumulative/royalty_source.
-- royalty_cumulative only contains months promoted through the new pipeline and therefore
-- is not a complete historical source for this check. The current production masters are
-- the approved source of truth for the "can this row be calculated now?" question.
--
-- This query only reports candidates. It does not correct anything and must not be turned
-- into an INSERT/UPDATE against ice_qb_source_p1, royalty_cumulative, or any correction
-- table. Correction is an operator decision (see docs/source_diff_worker_phase1.md).
--
-- Not executed by app/pipeline.py. Run manually with placeholders replaced, e.g.
--   project_id=ice-qb, production_dataset=ice_qb_source_p1,
--   from_month=202603, to_month=202607.
-- All referenced tables are in the production dataset (US).
--
-- Known case (verified during migration): 202604 product_code 2325411768 / 2325411784,
-- author conditions registered 5/18-5/19, Access re-export added 22,712 JPY.

WITH na_rows AS (
    SELECT
        CAST(year_month AS STRING) AS target_month
        , product_code
        , COUNT(*) AS na_row_count
        , SUM(COALESCE(dl_quantity, 0)) AS dl_quantity
        , SUM(COALESCE(license_fee, 0)) AS license_fee
    FROM
        `{{ project_id }}.{{ production_dataset }}.wholesale_sales_report`
    WHERE
        year_month BETWEEN CAST('{{ from_month }}' AS INT64) AND CAST('{{ to_month }}' AS INT64)
        AND digital_pub_code = '#N/A'
    GROUP BY ALL
)
, current_product_master AS (
    SELECT DISTINCT
        product_code
    FROM
        `{{ project_id }}.{{ production_dataset }}.catalog_bibliographic_master`
    WHERE
        product_code IS NOT NULL
)
, author_condition_summary AS (
    SELECT
        product_code
        , COUNT(*) AS author_condition_rows
        , COUNTIF(payee_code IS NOT NULL AND TRIM(payee_code) != '') AS payee_rows
        , STRING_AGG(DISTINCT digital_pub_code, ', ') AS current_digital_pub_codes
    FROM
        `{{ project_id }}.{{ production_dataset }}.author_condition_list`
    WHERE
        product_code IS NOT NULL
    GROUP BY
        product_code
)
SELECT
    'RETROACTIVE_MASTER_UPDATE' AS classification
    , n.target_month
    , n.product_code
    , n.na_row_count
    , n.dl_quantity
    , n.license_fee
    , a.author_condition_rows
    , a.payee_rows
    , a.current_digital_pub_codes
FROM
    na_rows n
INNER JOIN
    current_product_master pm
    USING (product_code)
INNER JOIN
    author_condition_summary a
    USING (product_code)
WHERE
    a.author_condition_rows > 0
ORDER BY
    n.target_month
    , n.product_code
