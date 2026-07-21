# Source Header Mapping

実 Drive フォルダ `1w0dUHIsBxlp1BHy0VQuxg0DyBEQqbnJX` の加工前ファイル確認結果です。

確認日: 2026-07-21

## 対象ファイル

| source_kind | file_name | format | sheet/table | header |
| --- | --- | --- | --- | --- |
| product_master | `【商品マスタ】report1751950998814.xls` | html-xls | `html_table_1` | なし。位置固定 |
| author_conditions | `【著者条件一覧】report1751951026780.xls` | html-xls | `html_table_1` | なし。位置固定 |
| ep_statement_detail | `電子出版確報明細データICE_2025年06月反映版.xlsx` | xlsx | `Datalizer1` | 2 行目 |
| monthly_product_sales | `月別電子出版プロダクト別売上一覧(ICE印税用)_2025年06月反映版.xlsx` | xlsx | `Datalizer1` | 2 行目 |

## 商品マスタ

ヘッダー行なしのため列位置固定。

| position | mapped field | required |
| --- | --- | --- |
| 1 | `product_code` | yes |
| 2 | `base_isbn` | no |
| 3 | `electronic_publication_code` | yes |
| 4 | `planning_editor` | no |
| 5 | `sales_start_date` | no |
| 6 | `title` | yes |
| 7 | `subtitle` | no |
| 8 | `price` | no |
| 10 | `isbn` | no |
| 11 | `author` | no |

確認結果: 5,169 行。必須列の空値は `product_code=0`, `electronic_publication_code=91`, `title=0`。

## 著者条件一覧

ヘッダー行なしのため列位置固定。

| position | mapped field | required |
| --- | --- | --- |
| 1 | `product_code` | yes |
| 2 | `electronic_publication_code` | yes |
| 3 | `author_identifier_id` | yes |
| 4 | `title` | yes |
| 5 | `planning_editor` | no |
| 7 | `author_category` | yes |
| 8 | `payee_code` | no |
| 9 | `author_name` | yes |
| 9 | `payee_name` | no |
| 14 | `initial_royalty_rate` | no |
| 15 | `revised_royalty_rate` | no |
| 16 | `revised_rate_sales_quantity` | no |
| 17 | `revised_rate_sales_amount` | no |
| 18 | `payment_hold_limit_amount` | no |
| 19 | `withholding_tax_type` | no |

確認結果: 13,688 行。必須列の空値は `product_code=0`, `electronic_publication_code=211`, `author_identifier_id=0`, `title=0`, `author_category=0`, `author_name=0`。

## 電子出版確報明細

実ヘッダーは 2 行目。

| source header | mapped field | required |
| --- | --- | --- |
| `計上年月` | `accounting_month` | yes |
| `請求先コード` | `billing_code` | no |
| `請求先名` | `billing_name` | no |
| `電子出版コード` | `electronic_publication_code` | yes |
| `書名` | `book_title` | no |
| `基準価格` | `list_price` | no |
| `書店名` | `store_name` | yes |
| `販売数量` | `sales_quantity` | no |
| `バリューコード` | `value_code` | no |

確認結果: 20,445 データ行。必須列の空値は `accounting_month=0`, `electronic_publication_code=0`, `store_name=0`。

## 月別電子出版プロダクト別売上

実ヘッダーは 2 行目。

| source header | mapped field | required |
| --- | --- | --- |
| `計上年月` | `accounting_month` | yes |
| `プロダクトコード` | `product_code` | yes |
| `プロダクト名` | `product_name` | no |
| `売上計上会社コード` | `sales_company_code` | no |
| `売上計上会社名` | `sales_company_name` | no |
| `売上計上部門コード` | `sales_department_code` | no |
| `売上計上部門名` | `sales_department_name` | no |
| `プロダクト種別コード` | `product_type_code` | no |
| `プロダクト種別名` | `product_type_name` | no |
| `書誌タイトル` | `bibliographic_title` | no |
| `請求先コード` | `billing_code` | no |
| `請求先名` | `billing_name` | no |
| `EP_確報コード` | `ep_statement_code` | no |
| `基準価格` | `list_price` | no |
| `売上数` | `sales_quantity` | no |
| `売上額` | `sales_amount` | no |
| `消費税額` | `tax_amount` | no |
| `消費税種別` | `tax_type` | no |
| `電子出版コード` | `electronic_publication_code` | no |
| `パートナー会社コード` | `partner_company_code` | no |
| `パートナー会社名` | `partner_company_name` | no |

確認結果: 9,414 データ行。必須列の空値は `accounting_month=0`, `product_code=0`。
