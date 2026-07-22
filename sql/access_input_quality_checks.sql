DECLARE v_target_month STRING DEFAULT '{{ target_month }}';
DECLARE v_run_id STRING DEFAULT '{{ run_id }}';
DECLARE v_accounting_month STRING DEFAULT REPLACE(v_target_month, '-', '');

INSERT INTO `{{ project_id }}.{{ audit_dataset }}.source_quality_results`
  (run_id, checked_at, target_month, check_name, severity, table_name, error_count, sample_json)
WITH checks AS (
  SELECT
    'access_input_author_conditions_row_count_zero' AS check_name,
    'ERROR' AS severity,
    'access_input_author_conditions' AS table_name,
    IF(COUNT(*) = 0, 1, 0) AS error_count,
    CAST(NULL AS STRING) AS sample_json
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_author_conditions`

  UNION ALL

  SELECT
    'access_input_sales_row_count_zero',
    'ERROR',
    'access_input_sales',
    IF(COUNT(*) = 0, 1, 0),
    NULL
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_sales`

  UNION ALL

  SELECT
    'access_input_store_detail_row_count_zero',
    'ERROR',
    'access_input_store_detail',
    IF(COUNT(*) = 0, 1, 0),
    NULL
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_store_detail`

  UNION ALL

  SELECT
    'access_input_pod_sales_row_count_zero',
    'ERROR',
    'access_input_pod_sales',
    IF(COUNT(*) = 0, 1, 0),
    NULL
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_pod_sales`

  UNION ALL

  SELECT
    'access_input_sales_accounting_month_mismatch',
    'ERROR',
    'access_input_sales',
    COUNTIF(accounting_month_1 != v_accounting_month OR accounting_month_2 != v_accounting_month),
    TO_JSON_STRING(ARRAY_AGG(IF(accounting_month_1 != v_accounting_month OR accounting_month_2 != v_accounting_month,
      STRUCT(accounting_month_1, accounting_month_2, product_code), NULL) IGNORE NULLS LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_sales`

  UNION ALL

  SELECT
    'access_input_sales_pod_mixed',
    'ERROR',
    'access_input_sales',
    COUNTIF(TRIM(COALESCE(billing_code_1, '')) = '6191'),
    TO_JSON_STRING(ARRAY_AGG(IF(TRIM(COALESCE(billing_code_1, '')) = '6191',
      STRUCT(billing_code_1, billing_name_1, product_code), NULL) IGNORE NULLS LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_sales`

  UNION ALL

  SELECT
    'access_input_store_detail_pod_mixed',
    'ERROR',
    'access_input_store_detail',
    COUNTIF(TRIM(COALESCE(billing_code, '')) = '6191'),
    TO_JSON_STRING(ARRAY_AGG(IF(TRIM(COALESCE(billing_code, '')) = '6191',
      STRUCT(billing_code, billing_name, title), NULL) IGNORE NULLS LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_store_detail`

  UNION ALL

  SELECT
    'access_input_store_detail_legacy_store_name_remaining',
    'ERROR',
    'access_input_store_detail',
    COUNTIF(TRIM(COALESCE(store_name, '')) IN ('Amazon', 'Reader Store', 'ブッコミ', 'Apple', '紀伊國屋')),
    TO_JSON_STRING(ARRAY_AGG(IF(TRIM(COALESCE(store_name, '')) IN ('Amazon', 'Reader Store', 'ブッコミ', 'Apple', '紀伊國屋'),
      STRUCT(store_name, billing_code, title), NULL) IGNORE NULLS LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_store_detail`

  UNION ALL

  SELECT
    'access_input_pod_sales_non_pod_mixed',
    'ERROR',
    'access_input_pod_sales',
    COUNTIF(
      TRIM(COALESCE(product_type_name, '')) != 'POD'
      AND TRIM(COALESCE(billing_code, '')) != '6191'
    ),
    TO_JSON_STRING(ARRAY_AGG(IF(
      TRIM(COALESCE(product_type_name, '')) != 'POD'
      AND TRIM(COALESCE(billing_code, '')) != '6191',
      STRUCT(product_type_name, billing_code, product_code), NULL) IGNORE NULLS LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_pod_sales`

  UNION ALL

  SELECT
    'access_input_sales_amazon_941_return_adjustment_remaining',
    'ERROR',
    'access_input_sales',
    COUNTIF(
      TRIM(COALESCE(billing_code_1, '')) = '941'
      AND COALESCE(sales_quantity_1, 0) = 0
      AND COALESCE(sales_amount_1, 0) < 0
    ),
    TO_JSON_STRING(ARRAY_AGG(IF(
      TRIM(COALESCE(billing_code_1, '')) = '941'
      AND COALESCE(sales_quantity_1, 0) = 0
      AND COALESCE(sales_amount_1, 0) < 0,
      STRUCT(billing_code_1, product_code, sales_quantity_1, sales_amount_1), NULL) IGNORE NULLS LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_sales`

  UNION ALL

  SELECT
    'access_input_store_detail_202506_manual_adjustment_count',
    'ERROR',
    'access_input_store_detail',
    IF(v_target_month = '2025-06', ABS(COUNTIF(source_file_name = 'legacy_manual_adjustment') - 1), COUNTIF(source_file_name = 'legacy_manual_adjustment')),
    TO_JSON_STRING(ARRAY_AGG(IF(source_file_name = 'legacy_manual_adjustment',
      STRUCT(accounting_month_1, billing_code, store_name, product_key), NULL) IGNORE NULLS LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.access_input_store_detail`
)
SELECT
  v_run_id,
  CURRENT_TIMESTAMP(),
  v_target_month,
  check_name,
  severity,
  table_name,
  error_count,
  sample_json
FROM checks
WHERE error_count > 0;

SELECT COALESCE(SUM(error_count), 0) AS total_error_count
FROM `{{ project_id }}.{{ audit_dataset }}.source_quality_results`
WHERE run_id = v_run_id AND severity = 'ERROR';
