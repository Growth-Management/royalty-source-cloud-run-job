DECLARE v_target_month STRING DEFAULT '{{ target_month }}';
DECLARE v_run_id STRING DEFAULT '{{ run_id }}';
DECLARE v_tolerance_rate NUMERIC DEFAULT {{ tolerance_rate }};

DELETE FROM `{{ project_id }}.{{ audit_dataset }}.source_quality_results` WHERE run_id = v_run_id;

INSERT INTO `{{ project_id }}.{{ audit_dataset }}.source_quality_results`
  (run_id, checked_at, target_month, check_name, severity, table_name, error_count, sample_json)
WITH
-- Mirrors the "latest file for target_month, deduped" selection in source_build.sql,
-- so cast-failure checks below see exactly the rows that fed the current source_* build.
ep_latest_file AS (
  SELECT source_file_id
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_ep_statement_detail`
  WHERE target_month = v_target_month
  GROUP BY source_file_id
  ORDER BY MAX(loaded_at) DESC
  LIMIT 1
)
, ep_latest_raw AS (
  SELECT r.*
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_ep_statement_detail` r
  INNER JOIN ep_latest_file f USING (source_file_id)
  WHERE r.target_month = v_target_month
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY r.source_file_id, r.source_sheet_name, r.row_number
    ORDER BY r.loaded_at DESC
  ) = 1
)
, sales_latest_file AS (
  SELECT source_file_id
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_monthly_product_sales`
  WHERE target_month = v_target_month
  GROUP BY source_file_id
  ORDER BY MAX(loaded_at) DESC
  LIMIT 1
)
, sales_latest_raw AS (
  SELECT r.*
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_monthly_product_sales` r
  INNER JOIN sales_latest_file f USING (source_file_id)
  WHERE r.target_month = v_target_month
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY r.source_file_id, r.source_sheet_name, r.row_number
    ORDER BY r.loaded_at DESC
  ) = 1
)
-- Mirrors the raw selection + seed bypass in pod_source_build.sql, so amazon/pf cast checks
-- below only look at rows that actually feed source_pod_sales_report for this target_month.
, pod_seed_state AS (
  SELECT COUNT(*) > 0 AS has_seed
  FROM `{{ project_id }}.{{ source_dataset }}.pod_access_history_seed`
  WHERE target_month = v_target_month
)
, amazon_latest_file AS (
  SELECT source_file_id
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_amazon_pod_monthly`
  WHERE target_month = v_target_month
    AND NOT REGEXP_CONTAINS(COALESCE(source_sheet_name, ''), r'転記用')
  GROUP BY source_file_id
  ORDER BY MAX(loaded_at) DESC
  LIMIT 1
)
, amazon_latest_raw AS (
  SELECT r.*
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_amazon_pod_monthly` r
  INNER JOIN amazon_latest_file f USING (source_file_id)
  CROSS JOIN pod_seed_state s
  WHERE r.target_month = v_target_month
    AND NOT REGEXP_CONTAINS(COALESCE(r.source_sheet_name, ''), r'転記用')
    AND NOT s.has_seed
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY r.source_file_id, r.source_sheet_name, r.row_number
    ORDER BY r.loaded_at DESC
  ) = 1
)
, pf_latest_file AS (
  SELECT source_file_id
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_pf_sales_report`
  WHERE target_month = v_target_month
  GROUP BY source_file_id
  ORDER BY MAX(loaded_at) DESC
  LIMIT 1
)
, pf_latest_raw AS (
  SELECT r.*
  FROM `{{ project_id }}.{{ raw_dataset }}.raw_pf_sales_report` r
  INNER JOIN pf_latest_file f USING (source_file_id)
  CROSS JOIN pod_seed_state s
  WHERE r.target_month = v_target_month
    AND NOT s.has_seed
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY r.source_file_id, r.row_number
    ORDER BY r.loaded_at DESC
  ) = 1
)
, checks AS (
  SELECT 'source_product_master_required_null' check_name, 'ERROR' severity, 'source_product_master' table_name,
    COUNTIF(product_code IS NULL OR title IS NULL) error_count,
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, title) LIMIT 5)) sample_json
  FROM `{{ project_id }}.{{ source_dataset }}.source_product_master`
  UNION ALL
  SELECT 'source_author_conditions_required_null', 'ERROR', 'source_author_conditions',
    COUNTIF(product_code IS NULL OR author_name IS NULL),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, author_name) LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.source_author_conditions`
  UNION ALL
  SELECT 'source_ep_statement_detail_required_null', 'ERROR', 'source_ep_statement_detail',
    COUNTIF(electronic_publication_code IS NULL OR store_name IS NULL OR target_month IS NULL),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(electronic_publication_code, store_name) LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.source_ep_statement_detail` WHERE target_month = v_target_month
  UNION ALL
  SELECT 'source_monthly_product_sales_required_null', 'ERROR', 'source_monthly_product_sales',
    COUNTIF(product_code IS NULL OR target_month IS NULL),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, product_name) LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` WHERE target_month = v_target_month
  UNION ALL
  SELECT 'monthly_sales_product_master_unmatched', 'WARNING', 'source_monthly_product_sales',
    IF(SAFE_DIVIDE(COUNTIF(pm.product_code IS NULL), COUNT(*)) > v_tolerance_rate, COUNTIF(pm.product_code IS NULL), 0),
    TO_JSON_STRING(STRUCT(
      COUNTIF(pm.product_code IS NULL) AS unmatched_count,
      COUNT(*) AS total_count,
      SAFE_DIVIDE(COUNTIF(pm.product_code IS NULL), COUNT(*)) AS unmatched_ratio,
      v_tolerance_rate AS tolerance_rate,
      ARRAY_AGG(IF(pm.product_code IS NULL, STRUCT(s.product_code, s.product_name), NULL) IGNORE NULLS LIMIT 5) AS sample
    ))
  FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` s
  LEFT JOIN `{{ project_id }}.{{ source_dataset }}.source_product_master` pm
    ON s.product_code = pm.product_code
  WHERE s.target_month = v_target_month
  UNION ALL
  SELECT 'monthly_sales_author_conditions_unmatched', 'WARNING', 'source_monthly_product_sales',
    IF(SAFE_DIVIDE(COUNTIF(ac.product_code IS NULL), COUNT(*)) > v_tolerance_rate, COUNTIF(ac.product_code IS NULL), 0),
    TO_JSON_STRING(STRUCT(
      COUNTIF(ac.product_code IS NULL) AS unmatched_count,
      COUNT(*) AS total_count,
      SAFE_DIVIDE(COUNTIF(ac.product_code IS NULL), COUNT(*)) AS unmatched_ratio,
      v_tolerance_rate AS tolerance_rate,
      ARRAY_AGG(IF(ac.product_code IS NULL, STRUCT(s.product_code, s.electronic_publication_code), NULL) IGNORE NULLS LIMIT 5) AS sample
    ))
  FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` s
  LEFT JOIN `{{ project_id }}.{{ source_dataset }}.source_author_conditions` ac
    ON s.product_code = ac.product_code
  WHERE s.target_month = v_target_month
  UNION ALL
  SELECT 'electronic_publication_code_missing_after_completion', 'WARNING', 'source_monthly_product_sales', COUNTIF(electronic_publication_code IS NULL),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, product_name) LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` WHERE target_month = v_target_month
  UNION ALL
  SELECT 'source_product_master_duplicate_key', 'ERROR', 'source_product_master', COUNT(*),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, duplicate_count) LIMIT 5))
  FROM (SELECT product_code, COUNT(*) duplicate_count FROM `{{ project_id }}.{{ source_dataset }}.source_product_master` GROUP BY product_code HAVING COUNT(*) > 1)
  UNION ALL
  SELECT 'source_author_conditions_duplicate_key', 'ERROR', 'source_author_conditions', COUNT(*),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, author_identifier_id, duplicate_count) LIMIT 5))
  FROM (SELECT product_code, author_identifier_id, COUNT(*) duplicate_count FROM `{{ project_id }}.{{ source_dataset }}.source_author_conditions` GROUP BY product_code, author_identifier_id HAVING COUNT(*) > 1)
  UNION ALL
  SELECT CONCAT(table_name, '_row_count_zero'), 'ERROR', table_name, IF(row_count = 0, 1, 0), NULL
  FROM (
    SELECT 'source_product_master' table_name, COUNT(*) row_count FROM `{{ project_id }}.{{ source_dataset }}.source_product_master`
    UNION ALL SELECT 'source_author_conditions', COUNT(*) FROM `{{ project_id }}.{{ source_dataset }}.source_author_conditions`
    UNION ALL SELECT 'source_ep_statement_detail', COUNT(*) FROM `{{ project_id }}.{{ source_dataset }}.source_ep_statement_detail` WHERE target_month = v_target_month
    UNION ALL SELECT 'source_monthly_product_sales', COUNT(*) FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` WHERE target_month = v_target_month
  )
  UNION ALL
  SELECT 'source_ep_statement_detail_accounting_month_mismatch', 'WARNING', 'source_ep_statement_detail',
    COUNTIF(accounting_month IS NOT NULL AND accounting_month != v_target_month),
    TO_JSON_STRING(ARRAY_AGG(IF(
      accounting_month IS NOT NULL AND accounting_month != v_target_month,
      STRUCT(accounting_month, store_name, value_code), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.source_ep_statement_detail`
  WHERE target_month = v_target_month
  UNION ALL
  SELECT 'source_ep_statement_detail_list_price_cast_failed', 'WARNING', 'source_ep_statement_detail',
    COUNTIF(NULLIF(TRIM(list_price), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(list_price, r'[,￥¥ ]', '') AS NUMERIC) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(list_price), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(list_price, r'[,￥¥ ]', '') AS NUMERIC) IS NULL,
      STRUCT(source_file_name, row_number, list_price), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM ep_latest_raw
  UNION ALL
  SELECT 'source_ep_statement_detail_sales_quantity_cast_failed', 'WARNING', 'source_ep_statement_detail',
    COUNTIF(NULLIF(TRIM(sales_quantity), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_quantity, r'[, ]', '') AS INT64) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(sales_quantity), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_quantity, r'[, ]', '') AS INT64) IS NULL,
      STRUCT(source_file_name, row_number, sales_quantity), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM ep_latest_raw
  UNION ALL
  SELECT 'source_monthly_product_sales_list_price_cast_failed', 'WARNING', 'source_monthly_product_sales',
    COUNTIF(NULLIF(TRIM(list_price), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(list_price, r'[,￥¥ ]', '') AS NUMERIC) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(list_price), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(list_price, r'[,￥¥ ]', '') AS NUMERIC) IS NULL,
      STRUCT(source_file_name, row_number, list_price), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM sales_latest_raw
  UNION ALL
  SELECT 'source_monthly_product_sales_sales_quantity_cast_failed', 'WARNING', 'source_monthly_product_sales',
    COUNTIF(NULLIF(TRIM(sales_quantity), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_quantity, r'[, ]', '') AS INT64) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(sales_quantity), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_quantity, r'[, ]', '') AS INT64) IS NULL,
      STRUCT(source_file_name, row_number, sales_quantity), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM sales_latest_raw
  UNION ALL
  SELECT 'source_monthly_product_sales_sales_amount_cast_failed', 'WARNING', 'source_monthly_product_sales',
    COUNTIF(NULLIF(TRIM(sales_amount), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_amount, r'[,￥¥ ]', '') AS NUMERIC) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(sales_amount), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_amount, r'[,￥¥ ]', '') AS NUMERIC) IS NULL,
      STRUCT(source_file_name, row_number, sales_amount), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM sales_latest_raw
  UNION ALL
  SELECT 'source_monthly_product_sales_tax_amount_cast_failed', 'WARNING', 'source_monthly_product_sales',
    COUNTIF(NULLIF(TRIM(tax_amount), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(tax_amount, r'[,￥¥ ]', '') AS NUMERIC) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(tax_amount), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(tax_amount, r'[,￥¥ ]', '') AS NUMERIC) IS NULL,
      STRUCT(source_file_name, row_number, tax_amount), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM sales_latest_raw
  UNION ALL
  SELECT 'source_pod_sales_report_amazon_quantity_cast_failed', 'WARNING', 'source_pod_sales_report',
    COUNTIF(NULLIF(TRIM(quantity), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(quantity, r'[^0-9\-]', '') AS INT64) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(quantity), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(quantity, r'[^0-9\-]', '') AS INT64) IS NULL,
      STRUCT(source_file_name, row_number, quantity), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM amazon_latest_raw
  UNION ALL
  SELECT 'source_pod_sales_report_amazon_sales_amount_cast_failed', 'WARNING', 'source_pod_sales_report',
    COUNTIF(NULLIF(TRIM(sales_amount), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_amount, r'[^0-9.\-]', '') AS NUMERIC) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(sales_amount), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_amount, r'[^0-9.\-]', '') AS NUMERIC) IS NULL,
      STRUCT(source_file_name, row_number, sales_amount), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM amazon_latest_raw
  UNION ALL
  SELECT 'source_pod_sales_report_amazon_manufacturing_cost_cast_failed', 'WARNING', 'source_pod_sales_report',
    COUNTIF(NULLIF(TRIM(manufacturing_cost), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(manufacturing_cost, r'[^0-9.\-]', '') AS NUMERIC) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(manufacturing_cost), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(manufacturing_cost, r'[^0-9.\-]', '') AS NUMERIC) IS NULL,
      STRUCT(source_file_name, row_number, manufacturing_cost), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM amazon_latest_raw
  UNION ALL
  SELECT 'source_pod_sales_report_pf_quantity_cast_failed', 'WARNING', 'source_pod_sales_report',
    COUNTIF(NULLIF(TRIM(quantity), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(quantity, r'[^0-9\-]', '') AS INT64) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(quantity), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(quantity, r'[^0-9\-]', '') AS INT64) IS NULL,
      STRUCT(source_file_name, row_number, quantity), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM pf_latest_raw
  UNION ALL
  SELECT 'source_pod_sales_report_pf_sales_amount_cast_failed', 'WARNING', 'source_pod_sales_report',
    COUNTIF(NULLIF(TRIM(sales_amount), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_amount, r'[^0-9.\-]', '') AS NUMERIC) IS NULL),
    TO_JSON_STRING(ARRAY_AGG(IF(
      NULLIF(TRIM(sales_amount), '') IS NOT NULL AND SAFE_CAST(REGEXP_REPLACE(sales_amount, r'[^0-9.\-]', '') AS NUMERIC) IS NULL,
      STRUCT(source_file_name, row_number, sales_amount), NULL
    ) IGNORE NULLS LIMIT 5))
  FROM pf_latest_raw
)
SELECT v_run_id, CURRENT_TIMESTAMP(), v_target_month, check_name, severity, table_name, error_count, sample_json
FROM checks WHERE error_count > 0;

SELECT COALESCE(SUM(error_count), 0) AS total_error_count
FROM `{{ project_id }}.{{ audit_dataset }}.source_quality_results`
WHERE run_id = v_run_id AND severity = 'ERROR';