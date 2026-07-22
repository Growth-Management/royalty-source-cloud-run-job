DECLARE v_target_month STRING DEFAULT '{{ target_month }}';

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.access_input_pod_sales` AS
SELECT
  s.accounting_month,
  s.product_code,
  s.product_name,
  s.sales_company_code,
  s.sales_company_name,
  s.sales_department_code,
  s.sales_department_name,
  s.product_type_code,
  s.product_type_name,
  s.bibliographic_title,
  s.billing_code,
  s.billing_name,
  s.ep_statement_code,
  s.list_price,
  s.sales_quantity,
  s.sales_amount,
  s.tax_amount,
  s.tax_type,
  s.electronic_publication_code,
  s.partner_company_code,
  s.partner_company_name,
  s.product_key,
  s.row_number AS source_row_number,
  s.source_file_id,
  s.source_file_name,
  s.loaded_at
FROM `{{ project_id }}.{{ source_dataset }}.source_monthly_product_sales` s
WHERE s.target_month = v_target_month
  AND (
    TRIM(COALESCE(s.product_type_name, '')) = 'POD'
    OR TRIM(COALESCE(s.billing_code, '')) = '6191'
  )
ORDER BY s.row_number;