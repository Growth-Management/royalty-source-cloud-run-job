-- RETROACTIVE_MASTER_UPDATE candidate monitor (READ-ONLY).
--
-- Lists past-month wholesale rows that were published with
-- electronic_publication_code = '#N/A' because the author condition was missing at
-- monthly-processing time, but whose product now has BOTH a product master row and an
-- author condition (frozen source_author_conditions or source_author_conditions_ext).
--
-- This query only reports candidates. It does not correct anything and must not be turned
-- into an INSERT/UPDATE against royalty_cumulative, ice_qb_source_p1, or any correction
-- table. Correction is an operator decision (see docs/source_diff_worker_phase1.md).
--
-- Not executed by app/pipeline.py. Run manually with the placeholders replaced, e.g.
--   project_id=ice-qb, cumulative_dataset=royalty_cumulative, source_dataset=royalty_source,
--   from_month=202603, to_month=202607.
-- All referenced datasets are in asia-northeast1 (ice_qb_source_p1 in US is not joined).
--
-- Known case (verified during migration): 202604 product_code 2325411768 / 2325411784,
-- author conditions registered 5/18-5/19, Access re-export added 22,712 JPY.

WITH na_rows AS (
    SELECT
        target_month
        , product_code
        , COUNT(*) AS na_row_count
        , SUM(SAFE_CAST(sales_quantity_1 AS NUMERIC)) AS sales_quantity
        , SUM(SAFE_CAST(sales_amount_1 AS NUMERIC)) AS license_fee
    FROM
        `{{ project_id }}.{{ cumulative_dataset }}.sales`
    WHERE
        target_month BETWEEN '{{ from_month }}' AND '{{ to_month }}'
        AND electronic_publication_code = '#N/A'
    GROUP BY
        target_month
        , product_code
)
, current_product_master AS (
    SELECT DISTINCT
        product_code
    FROM
        `{{ project_id }}.{{ source_dataset }}.source_product_master`
    WHERE
        product_code IS NOT NULL
)
, current_author_conditions AS (
    SELECT
        product_code
        , 'source_author_conditions' AS condition_source
        , payee_code
        , electronic_publication_code
        , CAST(NULL AS TIMESTAMP) AS added_at
    FROM
        `{{ project_id }}.{{ source_dataset }}.source_author_conditions`
    WHERE
        product_code IS NOT NULL
        AND electronic_publication_code IS NOT NULL

    UNION ALL

    SELECT
        product_code
        , CONCAT('source_author_conditions_ext:', source_type) AS condition_source
        , payee_code
        , electronic_publication_code
        , added_at
    FROM
        `{{ project_id }}.{{ source_dataset }}.source_author_conditions_ext`
    WHERE
        product_code IS NOT NULL
        AND electronic_publication_code IS NOT NULL
)
, author_condition_summary AS (
    SELECT
        product_code
        , COUNT(DISTINCT payee_code) AS payee_count
        , STRING_AGG(DISTINCT condition_source, ', ') AS condition_sources
        , STRING_AGG(DISTINCT electronic_publication_code, ', ') AS current_electronic_publication_codes
        , MIN(added_at) AS first_ext_added_at
    FROM
        current_author_conditions
    GROUP BY
        product_code
)
SELECT
    'RETROACTIVE_MASTER_UPDATE' AS classification
    , n.target_month
    , n.product_code
    , n.na_row_count
    , n.sales_quantity
    , n.license_fee
    , a.payee_count
    , a.condition_sources
    , a.current_electronic_publication_codes
    , a.first_ext_added_at
FROM
    na_rows n
INNER JOIN
    current_product_master pm
    USING (product_code)
INNER JOIN
    author_condition_summary a
    USING (product_code)
ORDER BY
    n.target_month
    , n.product_code
