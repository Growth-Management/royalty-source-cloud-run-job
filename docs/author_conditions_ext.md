# 拡張マスタ (source_author_conditions_ext)

`source_author_conditions`(Excel由来、2026-07-22の単発ロード以降更新なし)に一致しない
product_code は `electronic_publication_code` が `#N/A` になり、下流の `ice_qb_source_p1`
の que テーブルから INNER JOIN で除外される(202603分で341行/74 product_code、確認済み)。

対応として `source_author_conditions` 自体は凍結し、新規追加分は
`royalty_source.source_author_conditions_ext` に積む。投入経路は2つ:

- 自動投入(月次): `scripts/sync_author_conditions_ext.py` が Salesforce ミラー
  (`ice_qb_source.sf_Biblio__c` / `sf_BiblioContributor__c`, US) と突合し、一致した
  product_code を `source_type='sf_auto'` として投入する。
- 手動投入: 自動で解決しなかった product_code (`source_author_conditions_ext_unresolved`
  に `resolved=false` で記録される) を Google Sheets + Apps Script、または
  royalty-source-admin の画面から人が確認して `source_type='manual'` として投入する。

## テーブル

`sql/source_author_conditions_ext_tables.sql` (royalty-source-cloud-run-job) で定義。

- `source_author_conditions_ext`: 本体。論理キーは **`(product_code, payee_code)`**
  (product_code 単独ではない)。`source_author_conditions` 実データの検証
  (2026-08-28、BigQuery実データ照合)で、同一 product_code に対して最大10行・複数の
  異なる payee_code が存在する共著作品が実在することを確認したため、product_code
  単独キーでは共著者の支払情報が上書き・欠落する。`payee_code` は NOT NULL とし、
  同一 product_code に複数著者がいる場合は複数行を許容する。
- `source_author_conditions_ext_staging`: Sheets からの手動投入の一時受け皿
  (`validated` フラグで昇格待ちを管理、こちらも `(product_code, payee_code)` 単位)。
- `source_author_conditions_ext_unresolved`: 自動投入で解決しなかった product_code の
  履歴(`resolved` フラグで解消済みを区別)。こちらは product_code 単位のまま
  (electronic_publication_code 補完が解決したかどうかの管理であり、payee_code 単位の
  支払情報完全性を管理するものではない)。

このファイルは `app/pipeline.py` から自動実行されない。`source_author_conditions` を
参照する既存 SQL と同じ扱いで、事前に手動で一度だけ適用する必要がある
(`bq query` 等。今回のタスクでは実行していない)。

## access_input_build.sql の変更

`access_input_sales` / `access_input_store_detail` の `author_lookup` CTE を
`source_author_conditions` と `source_author_conditions_ext` の優先マージに変更した
(同一 product_key があれば拡張マスタを優先)。`legacy_author_lookup_202602` を使う
202602分の特別扱いはそのまま。`access_input_author_conditions`(source_author_conditions
の全件出力)は変更していない -- 凍結維持の指示どおり、参照方法自体は変えていない。

## DDL適用前に確認すべきこと

`source_author_conditions` の実スキーマは初版では `bq show` 未確認・推定だったが、
2026-08-28にBigQuery実データで確認済み(下表の推定は**完全一致**)。

| 列 | 型 | 根拠 |
|---|---|---|
| product_code, electronic_publication_code, payee_code, author_name, payee_name, withholding_tax_type | STRING | CASTなしで直接使用 / BigQuery実データで確認済み |
| initial_royalty_rate, revised_royalty_rate, revised_rate_sales_amount | NUMERIC | `CAST(... AS STRING)` で出力 / BigQuery実データで確認済み |
| revised_rate_sales_quantity | INT64 | 同上 / BigQuery実データで確認済み |
| author_identifier_id | STRING | `(product_code, author_identifier_id)` で重複判定 / BigQuery実データで確認済み |
| row_number | INT64 | `ORDER BY row_number` で使用 / BigQuery実データで確認済み |

`source_author_conditions_ext` のDDLはタスク指定の主要列のみに絞っており、
`author_identifier_id` や `title` 等は含めていない。同一 product_code に複数著者が
存在するケース(共著作品)は実データで確認済みのため、拡張マスタは
`(product_code, payee_code)` の複合キーで複数行を許容する設計に変更済み(上記
「テーブル」節参照)。

## 新規サービスアカウントに必要なIAM(適用なし)

`royalty-source-ext-loader@ice-qb.iam.gserviceaccount.com`(想定名):

- `roles/bigquery.dataViewer` on `ice_qb_source` データセット(US)
- `roles/bigquery.dataViewer` on `royalty_source` データセット(asia-northeast1)
- `roles/bigquery.dataEditor` on `ice_qb_source_salesforce` データセット(asia-northeast1、中継用)
- `roles/bigquery.dataEditor`(`source_author_conditions_ext` / `_unresolved` テーブルへの書き込み。テーブル単位のIAM条件が使えない場合は `royalty_source` データセット全体への dataEditor)
- `roles/bigquery.jobUser`(プロジェクトレベル、US/asia-northeast1両方でクエリ実行に必要)

## 未確認・要判断事項

2026-08-28、BigQuery実データによる2回の独立検証(Cowork)で1〜3・5・6は解消済み。
4は実データでリスクが実証されたため設計を修正した(下記参照)。

1. ~~`source_author_conditions` の実スキーマが未確認~~ → **解消**。BigQuery実データで
   確認し、上表の推定と完全一致。
2. ~~Salesforceフィールド名が仮置き~~ → **解消**。`scripts/sync_author_conditions_ext.py`
   の `build_salesforce_match_sql` を、実データで確認済みの列名に全面差し替えた:
   - `sf_Biblio__c.product_code__c`, `sf_Biblio__c.e_publishing_code__c`,
     `sf_BiblioContributor__c.payee_code__c`, `.tax_withholding_type__c` — 確認済み
   - `royalty_rate_1__c`→`initial_royalty_rate`、`royalty_rate_2__c`→`revised_royalty_rate`
     — 実データ検証(両方に値がありかつ異なる1,060件で常に rate_2 > rate_1、かつ
     `royalty_condition_amount_1__c`/`royalty_condition_quantity_1__c`が同時にセットされる
     パターンから確認)。ただし公式ドキュメントでの裏付けではなくデータパターンからの
     推測のため、業務担当者への一言確認を推奨(コード内コメントにも明記済み)
   - `author_name__c` / `payee_name__c` / `revised_rate_sales_quantity__c` /
     `revised_rate_sales_amount__c` は **`sf_BiblioContributor__c` に実在しない**ことが
     確認された(存在するのは `contributor_name__c` と
     `royalty_condition_quantity_1__c` / `royalty_condition_amount_1__c` のみ)。
     `author_name` / `payee_name` は両方とも `contributor_name__c` を充てる
     (`source_author_conditions`側で著者名=支払先名が常に一致することを実データで確認済み)。
     `revised_rate_sales_quantity` / `revised_rate_sales_amount` は
     `royalty_condition_quantity_1__c` / `royalty_condition_amount_1__c` に差し替え。
   - `royalty_condition_quantity_1__c` の `9999999` は「閾値なし」を表すセンチネル値と
     判断し、`NULLIF` で `NULL` に変換する処理を追加(`SALESFORCE_NO_THRESHOLD_SENTINEL`
     定数)。
3. ~~`ice_qb_source_salesforce` がデータセットかテーブルか未確認~~ → **解消**。
   データセットであることを確認済み(2025-10-30作成)。
4. **拡張マスタのキー設計を修正**: 当初 product_code 単位1行としていたが、
   `source_author_conditions` に同一 product_code で最大10行・複数 payee_code が
   存在する共著作品が実在することをBigQuery実データで確認(2026-08-28)。
   これを放置すると共著作品で1人分の支払情報しか記録されず実質的な支払漏れに
   つながるため、キーを `(product_code, payee_code)` の複合キーに変更し、1
   product_code に複数行を許容する設計に修正した。修正箇所:
   `sql/source_author_conditions_ext_tables.sql`(payee_code を NOT NULL 化)、
   `scripts/sync_author_conditions_ext.py`(Salesforce突合を
   `(product_code, payee_code)` 単位でdedup、マージのNOT EXISTS判定も複合キー化)、
   royalty-source-admin側の `author_conditions_ext_common.py` / 
   `pages/author_conditions_ext.py` / Apps Script `Code.gs` /
   `sql/promote_author_conditions_ext_staging.sql`(いずれも重複・UPSERTキーを
   複合キー化)。`access_input_build.sql` の electronic_publication_code 補完ロジック
   (QUALIFY dedup)は、同一 product_code 内で electronic_publication_code が常に
   1値に統一されていることを実データで確認済みのため変更不要(この点は当初設計の
   まま)。
5. 月次自動投入ジョブの Cloud Scheduler 登録は本タスクでは行っていない
   (`.github/workflows/deploy-sync-author-conditions-ext.yml` の
   `workflow_dispatch` で手動実行は可能)。push承認後の作業として想定通り。
6. `royalty-source-ext-loader` サービスアカウントの実際の作成・IAM付与は未実施。
   push承認後、IAM適用時に合わせて実施想定。
