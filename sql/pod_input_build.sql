DECLARE v_target_month STRING DEFAULT '{{ target_month }}';

CREATE OR REPLACE TABLE `{{ project_id }}.{{ source_dataset }}.access_input_pod_sales` AS
SELECT
    publisher
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
    `{{ project_id }}.{{ source_dataset }}.source_pod_sales_report`
WHERE
    target_month = v_target_month
ORDER BY
    source_kind
    , source_row_number;
