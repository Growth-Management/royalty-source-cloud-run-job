# Phase 1 SOURCE diff worker

## 目的

本番反映 (`scripts/publish_to_ice_qb_source_p1.py`) は、検証済みの月次スナップショットを
`royalty_cumulative` から US リージョンの一時 staging に載せ、`ice_qb_source_p1` と比較し、
`apply=true` のときだけ対象月をトランザクションで置き換える。

Phase 1 はこの挙動を維持したまま、比較処理を `app/source_diff_worker.py` に切り出し、
差分を `UNCHANGED / INSERT / DELETE_CANDIDATE / UPDATE` に分類して監査ログに残す。
**差分判定の正は Cloud Run Job 側のみ**。Admin 画面は監査ログを表示するだけで再計算しない。

既存の Access 互換計算 SQL (`sql/*.sql`) は変更していない。

## 差分判定仕様

`wholesale_sales_report` には検証済みの一意な業務キーが無い。候補キー
`year_month, dl_year_month, billing_code, wholesale_code, product_code, contents_code`
は 202603〜202607 の実データで月あたり約 1,200〜1,400 の重複グループを持つ。
そのため Phase 1 では UPDATE キーを推測せず、**完全行の multiset 比較**を正とする。

| 分類 | 意味 | 計算 (完全行ごとの出現数 s=source, t=target) |
|---|---|---|
| `UNCHANGED` | 両側に存在する同一行の出現数 | `Σ min(s, t)` |
| `INSERT` | 検証済み SOURCE 側にのみ存在する出現数 | `Σ max(s - t, 0)` |
| `DELETE_CANDIDATE` | 本番 `ice_qb_source_p1` 側にのみ存在する出現数 | `Σ max(t - s, 0)` |
| `UPDATE` | Phase 1 では常に 0 | 一意な業務キーが確定した SourceKind のみ将来有効化 |

- 常に `source_rows = UNCHANGED + INSERT`、`target_rows = UNCHANGED + DELETE_CANDIDATE`。
- 値が 1 列だけ変わった行は `INSERT 1 + DELETE_CANDIDATE 1` として現れる（UPDATE にはしない）。
- 行の同一性は `TO_JSON_STRING(row)` で判定する。NULL は `null` として NULL 同士で一致し、
  `''`・`'#N/A'`・`0` とは区別される。
- stage テーブルは `target.schema` から作成されるため、列構成・列順は本番と常に一致する
  (TO_JSON_STRING が比較可能であることの前提)。
- `diff_row_multisets()` は同じ意味論の Python 実装で、ダミーデータでのローカル確認用。

## 安全機構

| 機構 | 内容 |
|---|---|
| Promotion gate | `cumulative_promotion_log` の最新 SUCCESS と `royalty_cumulative` の件数・validation run_id が一致しない限り停止 |
| DELETE_CANDIDATE gate | `apply=true` かつ `DELETE_CANDIDATE > 0` のとき、トランザクション前に停止 (status=`FAILED`, error_message に `DELETE_CANDIDATE detected`)。dry-run では停止せず件数を記録する |
| Target month guard | `apply=true` のとき、stage 全行の `year_month` が対象月であることを確認してから適用 (他月の本番データに触れないため) |
| Transaction | 3 テーブルの `DELETE 対象月` + `INSERT stage` を 1 つの BigQuery transaction で実行 |
| Post-apply verification | 適用後に再比較し、一致しなければ `FAILED` |
| Audit | 成否にかかわらず `royalty_audit.production_publish_log` に記録。監査書き込み失敗時も一時 stage は削除される |
| SQL injection | 月は query parameter。テーブル ID / 列名は正規表現で検証してから SQL に埋め込む |

### DELETE_CANDIDATE の override

通常の Cloud Run Job 経路 (`main()` → `decide_publish_status()`) から override は呼べない。
環境変数・CLI 引数にも存在しない。

将来の承認付き DELETE 用に、`ensure_delete_candidates_safe(..., approval=DeleteCandidateApproval(...))`
だけを内部 API として用意している。承認は以下を必須とし、**レビュー時の件数と apply 時の件数が
テーブル単位で完全一致したときだけ**通過する。

- `target_month`（対象月）
- `delete_candidate_rows`（対象テーブルごとの件数）
- `approved_by`（承認者）
- `approved_at`（承認日時）
- `validation_run_id`（run_id）

承認付き DELETE を実運用化する際は、承認レコードを `royalty_audit` の専用テーブルに保存し、
publisher がそこから読み込む形にする（Phase 1 では未実装）。

## 監査ログ列

`royalty_audit.production_publish_log` に以下を追加した（すべて NULL 可 INT64、`*_before` は適用前の比較値）。

| 列 | 用途 |
|---|---|
| `{sales,store,pod}_unchanged_rows_before` | 一致行数 |
| `{sales,store,pod}_insert_rows_before` | 追加行数 |
| `{sales,store,pod}_delete_candidate_rows_before` | 削除候補行数 |

既存の `source_*_rows`（SOURCE 行数）、`target_before_*_rows`（本番行数）、
`*_mismatch_groups_before` と合わせて、Admin で「対象行 / 一致 / 追加 / 削除候補」を表示できる。

既存テーブルへの追加方式:

- 起動時に `get_table()` で現行スキーマを確認し、**不足列があるときだけ** 1 本の
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS a INT64, ADD COLUMN IF NOT EXISTS b INT64, ...` を発行する。
- 列が揃っていれば DDL は発行しない（毎回 DDL を打つと BigQuery のテーブルメタデータ更新レート制限に
  当たる可能性があるため、当初実装の「6 本の ALTER を毎回実行」から変更した）。
- NULL 可列の追加は既存行・既存クエリに影響しない。Phase 1 以前の行は新列が NULL になるので、
  Admin 側は NULL を「未計測」として扱う。
- `production_publish_log` は `royalty_audit` (asia-northeast1) にあり、`ice_qb_source_p1` ではない。

## RETROACTIVE_MASTER_UPDATE（監視のみ・Phase 1 では補正しない）

移行検証で判明したケース: 202604 の月次処理時点で product_code `2325411768` / `2325411784`
の著者条件が未登録だったため `digital_pub_code = '#N/A'` で出力された。5/18〜5/19 に Salesforce
著者条件が登録され、8/3 の Access 再出力で過去月に 22,712 円が追加された
(`113,200 × 20% = 22,640`、`80 × 80% = 64`、`80 × 10% = 8`)。
202603〜202607 で著者条件まで揃った遡及候補はこの 2 商品のみ。

これは外注実装範囲外で、当社運用として扱う。分類名 `RETROACTIVE_MASTER_UPDATE`。

検出条件:

```text
過去月 royalty_cumulative.sales で electronic_publication_code = '#N/A'
  AND 現在の source_product_master に product_code がある
  AND 現在の source_author_conditions または source_author_conditions_ext に著者条件がある
```

監視 SQL（読み取り専用）: `sql/audit/retroactive_master_update_candidates.sql`

方針:

- `#N/A` 行を最新マスタで一律補完しない。
- 検出しても自動で SOURCE / cumulative / `ice_qb_source_p1` / 補正テーブルへ書き込まない。
- 202604 の 22,712 円も補正 INSERT しない。補正要否・方法は人間が判断する。
- 本 SQL は `app/pipeline.py` から実行されない（手動実行のみ）。

## Admin 連携

`Growth-Management/royalty-source-admin` は `royalty_audit.production_publish_log` を読むだけ。
追加列を表示し、以下で状態を表示する（差分の再計算はしない）。

| 条件 | 表示 |
|---|---|
| いずれかの `delete_candidate_rows_before > 0` | FAIL / 要確認 |
| `insert_rows_before > 0` かつ削除候補 0 | WARN / 反映待ち |
| 差分すべて 0 | PASS / 一致 |
| 差分列が NULL（Phase 1 以前の実行） | 未計測 |

## テスト

GCP 認証なしで実行できる（BigQuery はモック）。

```bash
python -m py_compile app/source_diff_worker.py scripts/publish_to_ice_qb_source_p1.py \
  tests/test_source_diff_worker.py tests/test_publish_to_ice_qb_source_p1.py
python -m unittest discover -s tests -p "test_*.py"
```

- `tests/test_source_diff_worker.py`: 分類計算、重複行、NULL、Gate、承認 override、SQL 識別子検証
- `tests/test_publish_to_ice_qb_source_p1.py`: `python scripts/...` 起動時の import、status 判定、
  監査列 DDL、`main()` の dry-run / apply / Gate / post-verification / cleanup

## デプロイ上の注意

`fix/quality-check-samples` への push（PR マージ含む）で以下が自動起動する:

- `deploy-production-publish.yml`（`scripts/publish_to_ice_qb_source_p1.py` 変更時）:
  本番 publish Job を deploy する。`PRODUCTION_PUBLISH_APPLY=false` で deploy され、実行はしない。
- `deploy-cloud-run.yml`: `royalty-source-job` を deploy する。

PR #1 のマージ自体が本番 deploy になるため、マージは承認を得てから行う。
