DECLARE v_target_month STRING DEFAULT '{{ target_month }}';
DECLARE v_run_id STRING DEFAULT '{{ run_id }}';

DELETE FROM `{{ project_id }}.{{ audit_dataset }}.source_quality_results` WHERE run_id = v_run_id;

INSERT INTO `{{ project_id }}.{{ audit_dataset }}.source_quality_results`
  (run_id, checked_at, target_month, check_name, severity, table_name, error_count, sample_json)
WITH checks AS (
  SELECT 'source_product_master_required_null' check_name, 'ERROR' severity, 'source_product_master' table_name,
    COUNTIF(product_code IS NULL OR title IS NULL OR target_month IS NULL) error_count,
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, title) LIMIT 5)) sample_json
  FROM `{{ project_id }}.{{ source_dataset }}.source_product_master` WHERE target_month = v_target_month
  UNION ALL
  SELECT 'source_author_conditions_required_null', 'ERROR', 'source_author_conditions',
    COUNTIF(product_code IS NULL OR author_name IS NULL OR target_month IS NULL),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, author_name) LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.source_author_conditions` WHERE target_month = v_target_month
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
  SELECT 'monthly_sales_product_master_unmatched', 'ERROR', 'source_monthly_product_sales', COUNTIF(pm.product_code IS NULL),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(s.product_code, s.product_name) LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` s
  LEFT JOIN `{{ project_id }}.{{ source_dataset }}.source_product_master` pm
    ON s.product_code = pm.product_code AND s.target_month = pm.target_month
  WHERE s.target_month = v_target_month
  UNION ALL
  SELECT 'monthly_sales_author_conditions_unmatched', 'ERROR', 'source_monthly_product_sales', COUNTIF(ac.product_code IS NULL),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(s.product_code, s.electronic_publication_code) LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` s
  LEFT JOIN `{{ project_id }}.{{ source_dataset }}.source_author_conditions` ac
    ON s.product_code = ac.product_code AND s.target_month = ac.target_month
  WHERE s.target_month = v_target_month
  UNION ALL
  SELECT 'electronic_publication_code_missing_after_completion', 'ERROR', 'source_monthly_product_sales', COUNTIF(electronic_publication_code IS NULL),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, product_name) LIMIT 5))
  FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` WHERE target_month = v_target_month
  UNION ALL
  SELECT 'source_product_master_duplicate_key', 'ERROR', 'source_product_master', COUNT(*),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, duplicate_count) LIMIT 5))
  FROM (SELECT product_code, COUNT(*) duplicate_count FROM `{{ project_id }}.{{ source_dataset }}.source_product_master` WHERE target_month = v_target_month GROUP BY product_code HAVING COUNT(*) > 1)
  UNION ALL
  SELECT 'source_author_conditions_duplicate_key', 'ERROR', 'source_author_conditions', COUNT(*),
    TO_JSON_STRING(ARRAY_AGG(STRUCT(product_code, author_identifier_id, duplicate_count) LIMIT 5))
  FROM (SELECT product_code, author_identifier_id, COUNT(*) duplicate_count FROM `{{ project_id }}.{{ source_dataset }}.source_author_conditions` WHERE target_month = v_target_month GROUP BY product_code, author_identifier_id HAVING COUNT(*) > 1)
  UNION ALL
  SELECT CONCAT(table_name, '_row_count_zero'), 'ERROR', table_name, IF(row_count = 0, 1, 0), NULL
  FROM (
    SELECT 'source_product_master' table_name, COUNT(*) row_count FROM `{{ project_id }}.{{ source_dataset }}.source_product_master` WHERE target_month = v_target_month
    UNION ALL SELECT 'source_author_conditions', COUNT(*) FROM `{{ project_id }}.{{ source_dataset }}.source_author_conditions` WHERE target_month = v_target_month
    UNION ALL SELECT 'source_ep_statement_detail', COUNT(*) FROM `{{ project_id }}.{{ source_dataset }}.source_ep_statement_detail` WHERE target_month = v_target_month
    UNION ALL SELECT 'source_monthly_product_sales', COUNT(*) FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` WHERE target_month = v_target_month
  )
)
SELECT v_run_id, CURRENT_TIMESTAMP(), v_target_month, check_name, severity, table_name, error_count, sample_json
FROM checks WHERE error_count > 0;

SELECT COALESCE(SUM(error_count), 0) AS total_error_count
FROM `{{ project_id }}.{{ audit_dataset }}.source_quality_results`
WHERE run_id = v_run_id AND severity = 'ERROR';
