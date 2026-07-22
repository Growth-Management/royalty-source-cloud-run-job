# 実装開始プロンプト

電子出版印税データ基盤の Cloud Run Job を実装・保守する。

## 目的

Google Drive の加工前ファイルを取得し、BigQuery 上で STAGING → RAW → AGGREGATION 参照 → SOURCE → 品質チェックまでを再現可能な月次処理として実行する。

将来的には、生成した加工済みファイルを Google Drive の「加工データ」フォルダへ出力する。Drive 出力は段階実装とし、現在の実装範囲と未実装範囲を混同しないこと。

## 実運用環境

- GCP project: `ice-qb`
- Region: `asia-northeast1`
- Cloud Run Job: `royalty-source-job`
- Artifact Registry repository: `royalty-source`

### Google Drive

- 取り込み用データ
  - Folder ID: `1I9sBj6LYyaRPy1LcOWdQ3mDtJA-HDAQN`
  - Environment variable: `DRIVE_SOURCE_FOLDER_ID`
- 加工データ
  - Folder ID: `1zlmvNdJK3u8w0cQdBWCfZSxX7VpSTwzH`
  - Environment variable: `DRIVE_OUTPUT_FOLDER_ID`

取り込み用データを入力正本とする。加工データは後続フェーズで生成ファイルを保存する出力先とする。

## データレイヤー

- STAGING
  - Drive取得直後の一時受け
  - シート展開、ヘッダー正規化、元ファイル情報の保持
  - 業務ロジックを持たせない
- RAW
  - 監査、再取込、差分確認のための元データ保全
  - 業務ロジックを持たせない
- AGGREGATION
  - `ice_qb_aggregation` の共通商品マスタ、著者条件などを参照する
  - このリポジトリから共通マスタを重複生成しない
- SOURCE
  - MS Access取込後相当の整形済みデータ
  - 型変換、キー生成、マスタ補完をBigQuery SQLで行う
- AUDIT
  - パイプライン実行履歴と品質チェック結果を保存する

## 処理フロー

1. `DRIVE_SOURCE_FOLDER_ID` のフォルダを走査する
2. 対象ファイルの種別と対象月を判定する
3. 対象ファイルをダウンロードする
4. xlsx / xls / html-xls を解析する
5. STAGINGへロードする
6. RAWへロードする
7. `ice_qb_aggregation` の共通マスタを参照する
8. `sql/source_build.sql` を実行する
9. `sql/source_quality_checks.sql` を実行する
10. `royalty_audit.pipeline_audit_log` と `royalty_audit.source_quality_results` に結果を記録する
11. Cloud Loggingへ構造化ログを出力する
12. 後続フェーズで加工済みファイルを `DRIVE_OUTPUT_FOLDER_ID` へアップロードする

## 対象ファイル

- `【商品マスタ】report*.xls`
- `【著者条件一覧】report*.xls`
- `電子出版確報明細データICE_*反映版.xlsx`
- `月別電子出版プロダクト別売上一覧(ICE印税用)_*反映版.xlsx`

## 実装方針

- Cloud Run側は、Drive取得、ファイル解析、BigQueryロード、SQL実行制御、監査ログ、エラー制御を担当する。
- 業務ロジックは可能な限りBigQuery SQL側に置く。
- `where 1=1` と `group by all` を利用可能な箇所では既存方針に合わせる。
- 対象月は `JOB_TARGET_MONTH` で指定できるようにする。
- 同じ対象月を再実行しても結果が重複しない設計にする。
- 元ファイル名、Drive file ID、対象月、シート名、元行番号、取込時刻、run_idを追跡可能にする。
- 品質チェックエラー時の終了可否は `FAIL_ON_QUALITY_ERRORS` で制御する。
- 秘密情報や認証情報をコード、ログ、GitHub Actionsへ直接記載しない。

## GitHub Actions

### Deploy workflow

`.github/workflows/deploy-cloud-run.yml` は次だけを担当する。

- コンテナbuild
- Artifact Registryへのpush
- Cloud Run Jobの作成または更新
- 実運用環境変数の設定

Deployと月次実行を混在させない。

### Run workflow

`.github/workflows/run-cloud-run.yml` は次を担当する。

- `workflow_dispatch` から対象月を受け取る
- `FAIL_ON_QUALITY_ERRORS` を指定する
- 配置済みCloud Run Jobを実行する
- 実行完了を待機する
- 後続実装でBigQuery監査結果をGitHub Step Summaryへ表示する

## 品質チェック

最低限、次を確認する。

- 必須列NULL
- 対象月NULLまたは不一致
- 商品コード、電子出版コードの未補完
- 商品マスタ未一致
- 著者条件未一致
- 業務キー重複
- 売上数、売上額の型変換失敗
- SOURCE件数0件

チェック結果は `BQ_AUDIT_DATASET.source_quality_results` に保存する。

## Access移行検証

同じ対象月についてAccess結果とBigQuery結果を比較する。

- レコード件数
- 業務キー単位の差分
- 金額合計
- 数量合計
- マスタ未一致件数
- 品質チェック結果

差分がある場合は、RAW、AGGREGATION参照、SOURCE変換のどの段階で発生したかを切り分ける。

## 今回の実装境界

今回の作業で扱うもの:

- STAGING / RAW / SOURCE
- AGGREGATION参照
- 品質チェック
- 監査ログ
- Deploy / Run workflow
- `DRIVE_OUTPUT_FOLDER_ID` の設定管理

別フェーズで扱うもの:

- 加工済みファイルの形式確定
- ファイル名と世代管理ルール
- Google Driveへのアップロード処理
- PROCESS / DATAMART
- MERGE / UPSERT
- Slack / Gmail通知

未実装機能を実装済みとしてREADMEや完了報告に記載しないこと。

## 完了条件

- `python -m app.main` でエントリポイントが動く
- STAGING → RAW → AGGREGATION参照 → SOURCE → 品質チェックの流れがコードとREADMEで一致する
- `DRIVE_SOURCE_FOLDER_ID` と `DRIVE_OUTPUT_FOLDER_ID` が設定クラス、`.env.example`、deploy workflowで一致する
- Deploy workflowとRun workflowの責務が分離されている
- Python構文チェックが通る
- SQLの構文と参照データセットが整合する
- 再実行時の重複防止が確認できる
- 未実装のDriveアップロード処理が実装済みとして扱われていない
