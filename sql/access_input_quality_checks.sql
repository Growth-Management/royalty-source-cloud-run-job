DECLARE v_target_month STRING DEFAULT '{{ target_month }}';
DECLARE v_run_id STRING DEFAULT '{{ run_id }}';
DECLARE v_accounting_month STRING DEFAULT REPLACE(v_target_month, '-', '');

INSERT INTO `{{ project_id }}.{{ audit_dataset }}.source_quality_results`
    (run_id, checked_at, target_month, check_name, severity, table_name, error_count, sample_json)
WITH pod_stats AS (
    SELECT
        COUNT(*) AS row_count
        , COALESCE(SUM(quantity), 0) AS quantity_total
        , COALESCE(SUM(sales_amount), 0) AS sales_amount_total
        , COALESCE(SUM(manufacturing_cost), 0) AS manufacturing_cost_total
        , COUNTIF(source_kind = 'amazon_pod_monthly') AS amazon_row_count
        , COUNTIF(source_kind = 'pf_sales_report') AS pf_row_count
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_pod_sales`
)
, checks AS (
    SELECT
        'access_input_author_conditions_row_count_zero' AS check_name
        , 'ERROR' AS severity
        , 'access_input_author_conditions' AS table_name
        , IF(COUNT(*) = 0, 1, 0) AS error_count
        , CAST(NULL AS STRING) AS sample_json
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_author_conditions`

    UNION ALL

    SELECT
        'access_input_sales_row_count_zero', 'ERROR', 'access_input_sales', IF(COUNT(*) = 0, 1, 0), NULL
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_sales`

    UNION ALL

    SELECT
        'access_input_store_detail_row_count_zero', 'ERROR', 'access_input_store_detail', IF(COUNT(*) = 0, 1, 0), NULL
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_store_detail`

    UNION ALL

    SELECT
        'access_input_pod_sales_row_count_zero', 'ERROR', 'access_input_pod_sales', IF(COUNT(*) = 0, 1, 0), NULL
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_pod_sales`

    UNION ALL

    SELECT
        'access_input_sales_accounting_month_mismatch'
        , 'ERROR'
        , 'access_input_sales'
        , COUNTIF(accounting_month_1 != v_accounting_month OR accounting_month_2 != v_accounting_month)
        , TO_JSON_STRING(ARRAY_AGG(IF(
            accounting_month_1 != v_accounting_month OR accounting_month_2 != v_accounting_month,
            STRUCT(accounting_month_1, accounting_month_2, product_code), NULL
        ) IGNORE NULLS LIMIT 5))
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_sales`

    UNION ALL

    SELECT
        'access_input_sales_pod_mixed'
        , 'ERROR'
        , 'access_input_sales'
        , COUNTIF(TRIM(COALESCE(billing_code_1, '')) = '6191')
        , TO_JSON_STRING(ARRAY_AGG(IF(
            TRIM(COALESCE(billing_code_1, '')) = '6191',
            STRUCT(billing_code_1, billing_name_1, product_code), NULL
        ) IGNORE NULLS LIMIT 5))
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_sales`

    UNION ALL

    SELECT
        'access_input_store_detail_pod_mixed'
        , 'ERROR'
        , 'access_input_store_detail'
        , COUNTIF(TRIM(COALESCE(billing_code, '')) = '6191')
        , TO_JSON_STRING(ARRAY_AGG(IF(
            TRIM(COALESCE(billing_code, '')) = '6191',
            STRUCT(billing_code, billing_name, title), NULL
        ) IGNORE NULLS LIMIT 5))
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_store_detail`

    UNION ALL

    SELECT
        'access_input_store_detail_legacy_store_name_remaining'
        , 'ERROR'
        , 'access_input_store_detail'
        , COUNTIF(TRIM(COALESCE(store_name, '')) IN ('Amazon', 'Reader Store', 'ブッコミ', 'Apple', '紀伊國屋'))
        , TO_JSON_STRING(ARRAY_AGG(IF(
            TRIM(COALESCE(store_name, '')) IN ('Amazon', 'Reader Store', 'ブッコミ', 'Apple', '紀伊國屋'),
            STRUCT(store_name, billing_code, title), NULL
        ) IGNORE NULLS LIMIT 5))
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_store_detail`

    UNION ALL

    SELECT
        'access_input_pod_sales_unknown_source_kind'
        , 'ERROR'
        , 'access_input_pod_sales'
        , COUNTIF(source_kind NOT IN ('amazon_pod_monthly', 'pf_sales_report', 'pod_access_history'))
        , TO_JSON_STRING(ARRAY_AGG(IF(
            source_kind NOT IN ('amazon_pod_monthly', 'pf_sales_report', 'pod_access_history'),
            STRUCT(source_kind, product_code, isbn), NULL
        ) IGNORE NULLS LIMIT 5))
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_pod_sales`

    UNION ALL

    SELECT
        'access_input_pod_sales_zero_quantity_remaining'
        , 'ERROR'
        , 'access_input_pod_sales'
        , COUNTIF(COALESCE(quantity, 0) = 0)
        , TO_JSON_STRING(ARRAY_AGG(IF(
            COALESCE(quantity, 0) = 0,
            STRUCT(source_kind, product_code, isbn, quantity), NULL
        ) IGNORE NULLS LIMIT 5))
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_pod_sales`

    UNION ALL

    SELECT
        'access_input_pod_sales_target_month_mismatch'
        , 'ERROR'
        , 'access_input_pod_sales'
        , COUNTIF(sales_month != v_accounting_month)
        , TO_JSON_STRING(ARRAY_AGG(IF(
            sales_month != v_accounting_month,
            STRUCT(source_kind, product_code, isbn, sales_month), NULL
        ) IGNORE NULLS LIMIT 5))
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_pod_sales`

    UNION ALL

    SELECT
        'access_input_pod_sales_202602_row_count'
        , 'ERROR'
        , 'access_input_pod_sales'
        , IF(v_accounting_month = '202602' AND row_count != 19, 1, 0)
        , IF(v_accounting_month = '202602', TO_JSON_STRING(STRUCT(19 AS expected, row_count AS actual, row_count - 19 AS difference)), NULL)
    FROM
        pod_stats

    UNION ALL

    SELECT
        'access_input_pod_sales_202602_quantity_total'
        , 'ERROR'
        , 'access_input_pod_sales'
        , IF(v_accounting_month = '202602' AND quantity_total != 38, 1, 0)
        , IF(v_accounting_month = '202602', TO_JSON_STRING(STRUCT(38 AS expected, quantity_total AS actual, quantity_total - 38 AS difference)), NULL)
    FROM
        pod_stats

    UNION ALL

    SELECT
        'access_input_pod_sales_202602_sales_amount_total'
        , 'ERROR'
        , 'access_input_pod_sales'
        , IF(v_accounting_month = '202602' AND sales_amount_total != 51138, 1, 0)
        , IF(v_accounting_month = '202602', TO_JSON_STRING(STRUCT(CAST(51138 AS NUMERIC) AS expected, sales_amount_total AS actual, sales_amount_total - 51138 AS difference)), NULL)
    FROM
        pod_stats

    UNION ALL

    SELECT
        'access_input_pod_sales_202602_manufacturing_cost_total'
        , 'ERROR'
        , 'access_input_pod_sales'
        , IF(v_accounting_month = '202602' AND manufacturing_cost_total != 37027, 1, 0)
        , IF(v_accounting_month = '202602', TO_JSON_STRING(STRUCT(CAST(37027 AS NUMERIC) AS expected, manufacturing_cost_total AS actual, manufacturing_cost_total - 37027 AS difference)), NULL)
    FROM
        pod_stats

    UNION ALL

    SELECT
        'access_input_pod_sales_202602_amazon_row_count'
        , 'ERROR'
        , 'access_input_pod_sales'
        , IF(v_accounting_month = '202602' AND amazon_row_count != 4, 1, 0)
        , IF(v_accounting_month = '202602', TO_JSON_STRING(STRUCT(4 AS expected, amazon_row_count AS actual, amazon_row_count - 4 AS difference)), NULL)
    FROM
        pod_stats

    UNION ALL

    SELECT
        'access_input_pod_sales_202602_pf_row_count'
        , 'ERROR'
        , 'access_input_pod_sales'
        , IF(v_accounting_month = '202602' AND pf_row_count != 15, 1, 0)
        , IF(v_accounting_month = '202602', TO_JSON_STRING(STRUCT(15 AS expected, pf_row_count AS actual, pf_row_count - 15 AS difference)), NULL)
    FROM
        pod_stats

    UNION ALL

    SELECT
        'access_input_sales_amazon_941_return_adjustment_remaining'
        , 'ERROR'
        , 'access_input_sales'
        , COUNTIF(
            TRIM(COALESCE(billing_code_1, '')) = '941'
            AND COALESCE(sales_quantity_1, 0) = 0
            AND COALESCE(sales_amount_1, 0) < 0
        )
        , TO_JSON_STRING(ARRAY_AGG(IF(
            TRIM(COALESCE(billing_code_1, '')) = '941'
            AND COALESCE(sales_quantity_1, 0) = 0
            AND COALESCE(sales_amount_1, 0) < 0,
            STRUCT(billing_code_1, product_code, sales_quantity_1, sales_amount_1), NULL
        ) IGNORE NULLS LIMIT 5))
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_sales`

    UNION ALL

    SELECT
        'access_input_store_detail_202506_manual_adjustment_count'
        , 'ERROR'
        , 'access_input_store_detail'
        , IF(
            v_accounting_month = '202506',
            IF(COUNTIF(source_file_name = 'legacy_manual_adjustment') = 1, 0, 1),
            COUNTIF(source_file_name = 'legacy_manual_adjustment')
        )
        , TO_JSON_STRING(ARRAY_AGG(IF(
            source_file_name = 'legacy_manual_adjustment',
            STRUCT(accounting_month_1, billing_code, store_name, product_key), NULL
        ) IGNORE NULLS LIMIT 5))
    FROM
        `{{ project_id }}.{{ source_dataset }}.access_input_store_detail`
)
SELECT
    v_run_id
    , CURRENT_TIMESTAMP()
    , v_target_month
    , check_name
    , severity
    , table_name
    , error_count
    , sample_json
FROM
    checks
WHERE
    error_count > 0;

SELECT
    COALESCE(SUM(error_count), 0) AS total_error_count
FROM
    `{{ project_id }}.{{ audit_dataset }}.source_quality_results`
WHERE
    run_id = v_run_id
    AND severity = 'ERROR';
