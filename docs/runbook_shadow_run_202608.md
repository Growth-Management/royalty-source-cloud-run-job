# Runbook: 202608 Shadow Run（Phase 1 差分 Worker 初回）

Phase 1 差分 Worker の最初の Shadow Run を 202608 で行うための手順。
**この Runbook の範囲では `apply=true` を実行しない。** apply は手順 12 の別承認後に行う。

## 前提

- PR #1 (`feature/source-diff-worker-phase1`) がレビュー・承認済みで `fix/quality-check-samples` にマージされ、
  `deploy-production-publish.yml` による production publish Job の deploy が成功していること
  （マージ＝deploy。deploy 時は `PRODUCTION_PUBLISH_APPLY=false`）。
- deploy 後の production publish Job の image が PR #1 のコミットを含むこと
  （Actions の "Verify deployed production publish job" で確認）。
- SOURCE への直接 INSERT は禁止。必ず Drive → STAGING → RAW → SOURCE → validation → cumulative →
  production staging → diff → apply の経路を通す。
- 対象月以外の本番データは変更しない。

## 手順

| # | 作業 | 実行者 / 場所 | 合格条件 | 不合格時 |
|---|---|---|---|---|
| 1 | MASTER 同期（商品マスタ・著者条件。著者条件拡張 `scripts/sync_author_conditions_ext.py` を含む） | 既存 MASTER 同期手順 | 同期ジョブ成功 | 停止・原因調査 |
| 2 | MASTER Gate PASS 確認 | 管理画面 | MASTER Gate が PASS | 停止。未解決 product_code は `source_author_conditions_ext_unresolved` を確認し手動投入を検討 |
| 3 | 202608 正式 SOURCE ファイル確認 | Drive | 正式版ファイルが揃っている（暫定版・差し替え前版でない） | 停止 |
| 4 | `royalty-source-job` 実行（target_month=202608） | `deploy-cloud-run.yml` workflow_dispatch（`run_validation=true`） | 実行成功 | 停止・ログ確認 |
| 5 | STAGING / RAW / SOURCE 確認 | 管理画面「月次状態」 | 各層の行数が整合、二重計上なし | 停止 |
| 6 | Workbook 比較 | 管理画面「Workbook ↔ BigQuery 比較」 | 差分なし | 停止・差分内容を記録 |
| 7 | ALL PASS 確認 | 管理画面（品質チェック・制約チェック・Workbook 比較） | すべて PASS | 停止 |
| 8 | `royalty_cumulative` 投入 | 管理画面「累積投入」 | `cumulative_promotion_log` に SUCCESS、actor_email 記録 | 停止 |
| 9 | production publish **apply=false** | `deploy-production-publish.yml` workflow_dispatch: `target_month=202608`, `execute=true`, `apply=false`, `confirmation` 空欄 | Job 成功、status が `DRY_RUN_MISMATCH` または `ALREADY_MATCHED` | `FAILED` なら error_message を確認し停止 |
| 10 | INSERT / DELETE_CANDIDATE 確認 | 管理画面「本番反映」または下記 SQL | 下記「判定」参照 | — |
| 11 | 人間レビュー | 担当者 + 承認者 | 判定結果と件数を記録 | — |
| 12 | apply=true | **別途承認後**に実施（本 Runbook 範囲外） | — | — |

### 手順 10 の確認 SQL（読み取り専用）

```sql
SELECT
    published_at
    , status
    , apply_requested
    , source_sales_rows, target_before_sales_rows
    , sales_unchanged_rows_before, sales_insert_rows_before, sales_delete_candidate_rows_before
    , source_store_rows, target_before_store_rows
    , store_unchanged_rows_before, store_insert_rows_before, store_delete_candidate_rows_before
    , source_pod_rows, target_before_pod_rows
    , pod_unchanged_rows_before, pod_insert_rows_before, pod_delete_candidate_rows_before
    , error_message
FROM
    `ice-qb.royalty_audit.production_publish_log`
WHERE
    target_month = '202608'
ORDER BY
    published_at DESC
LIMIT 5
```

### 判定

| 結果 | 意味 | 次の行動 |
|---|---|---|
| すべての差分 0（`ALREADY_MATCHED`） | 本番と一致 | apply 不要 |
| `INSERT > 0` かつ全テーブル `DELETE_CANDIDATE = 0` | 新規月（本番未投入）など追加のみ | 件数が SOURCE 行数と整合するか確認し、apply=true の承認を申請 |
| いずれかのテーブルで `DELETE_CANDIDATE > 0` | 本番にのみ存在する行がある | **STOP**。apply=true を実行しても Gate で停止する。行の内容を調査し、原因（再出力・マスタ遡及・手修正など）を記録して管理本部／責任者と協議 |
| `source_rows = target_rows` だが差分あり | 値が変わった行がある（UPDATE は Phase 1 では INSERT+DELETE_CANDIDATE として出る） | 同上（STOP） |

202608 が本番未投入の新規月であれば、期待値は「INSERT = SOURCE 行数、UNCHANGED = 0、DELETE_CANDIDATE = 0」。

### RETROACTIVE_MASTER_UPDATE の確認（任意・読み取り専用）

手順 2 の後、`sql/audit/retroactive_master_update_candidates.sql` を `from_month=202603`,
`to_month=202607` で手動実行し、過去月の `#N/A` 行に対して現在は著者条件が揃った product_code を確認する。
新たな候補があっても **自動補正・補正 INSERT はしない**。一覧を記録し、補正要否は別途判断する。

## 手順 12（参考・本 Runbook では実施しない）

- `deploy-production-publish.yml` を `apply=true`, `confirmation=202608` で実行。
- 実行前に手順 9 の dry-run 結果と件数が変わっていないことを確認する（Job 内で再比較し、
  `DELETE_CANDIDATE > 0` なら停止する）。
- 実行後、status=`SUCCESS` と post-apply verification 成功（`target_after_*_rows` = `source_*_rows`）を確認。
- workflow の "Restore safe default" で `PRODUCTION_PUBLISH_APPLY=false` に戻ったことを確認。
