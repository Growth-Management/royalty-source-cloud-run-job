# royalty-source-cloud-run-job

Cloud Run Job で電子出版印税データ基盤の **Google Drive 取込 → STAGING → RAW → SOURCE → 品質チェック** を処理するジョブです。

将来的には、生成した加工済みファイルを Google Drive の「加工データ」フォルダへ出力します。現時点では出力先フォルダ設定まで実装済みで、Drive アップロード処理は次フェーズで追加します。

## Scope

- Google Drive 上の加工前ファイルを走査する
- 対象ファイルを種別判定する
- ダウンロードして STAGING 入力を作る
- STAGING データセットへ一時格納する
- RAW へロードする
- AGGREGATION の共通マスタを参照して SOURCE を生成する
- SOURCE 品質チェック SQL を実行する
- 監査ログを BigQuery と Cloud Logging に出力する
- 加工済みファイルの出力先 Google Drive フォルダを環境変数で管理する

## Production Google Drive folders

### 取り込み用データ

- Folder ID: `1I9sBj6LYyaRPy1LcOWdQ3mDtJA-HDAQN`
- Environment variable: `DRIVE_SOURCE_FOLDER_ID`
- 用途: Cloud Run Job が取得する加工前 Excel / xls-html / xlsx ファイルの正本

### 加工データ

- Folder ID: `1zlmvNdJK3u8w0cQdBWCfZSxX7VpSTwzH`
- Environment variable: `DRIVE_OUTPUT_FOLDER_ID`
- 用途: 後続フェーズで生成する加工済みデータの保存先

サービスアカウントには、取り込み用フォルダへの読み取り権限と、加工データフォルダへの書き込み権限を付与する。

## Dataset policy

- `BQ_STAGING_DATASET`: Drive 取得直後の一時受け、シート展開、標準化結果を置く
- `BQ_RAW_DATASET`: 監査・再取込・差分確認のための元データを保全する
- `ice_qb_aggregation`: 複数処理で共通利用する商品マスタ・著者条件などの参照データを置く
- `BQ_SOURCE_DATASET`: Access 取込後相当の整形済みデータを置く
- `BQ_AUDIT_DATASET`: パイプライン実行履歴と品質チェック結果を置く

STAGING と RAW は別データセットに分け、業務ロジックを持たせない。共通マスタは AGGREGATION に集約し、SOURCE 生成時に参照する。

## Target architecture

```text
Google Drive: 取り込み用データ
        |
        v
STAGING
        |
        v
RAW -----------+
                |
ice_qb_aggregation
                |
                v
SOURCE
        |
        v
SOURCE quality checks / audit logs
        |
        v
Google Drive: 加工データ
```

加工データDriveへの出力は目標構成であり、現時点では環境変数とCloud Run Jobへの設定まで実装済みです。

## Directory

- `app/main.py`: エントリポイント
- `app/settings.py`: 環境変数設定
- `app/pipeline.py`: パイプライン制御
- `app/drive_client.py`: Drive 走査とダウンロード
- `app/bigquery_client.py`: BigQuery ロードと SQL 実行
- `app/parsers.py`: xls/xlsx 入力の標準化
- `sql/staging_tables.sql`: STAGING テーブル設計
- `sql/raw_tables.sql`: RAW テーブル設計
- `sql/source_build.sql`: SOURCE 生成 SQL
- `sql/source_quality_checks.sql`: SOURCE 品質チェック SQL
- `docs/source_header_mapping.md`: 実 Drive ファイル確認に基づくヘッダー/固定列マッピング
- `docs/deploy.md`: GitHub Actions から Cloud Run Job へ deploy する手順
- `.github/workflows/deploy-cloud-run.yml`: コンテナ build と Cloud Run Job deploy
- `.github/workflows/run-cloud-run.yml`: 対象月を指定した Cloud Run Job 実行
- `prompts/implementation-start-prompt.md`: エージェント実装用プロンプト
- `.env.example`: 必須環境変数例
- `requirements.txt`: Python 依存
- `Dockerfile`: Cloud Run Job 用コンテナ定義

## Required environment variables

- `GCP_PROJECT_ID`
- `BQ_STAGING_DATASET`
- `BQ_RAW_DATASET`
- `BQ_SOURCE_DATASET`
- `BQ_AUDIT_DATASET`
- `BQ_LOCATION`（任意。既定 `asia-northeast1`）
- `DRIVE_SOURCE_FOLDER_ID`
- `DRIVE_OUTPUT_FOLDER_ID`
- `GOOGLE_APPLICATION_CREDENTIALS` または Workload Identity
- `JOB_TARGET_MONTH`（任意。未指定なら最新対象を選ぶ）
- `LOG_LEVEL`（任意。既定 `INFO`）
- `FAIL_ON_QUALITY_ERRORS`（任意。既定 `true`）
- `MAX_GENERIC_COLUMNS`（任意。既定 `40`）

## Runtime flow

1. 取り込み用Driveフォルダを走査する
2. 対象4種のファイル種別と対象月を推定する
3. 対象ファイルをダウンロードする
4. xlsx / xls / html-xls を解析し、シート名・行番号・元ファイル情報を保持する
5. 汎用 STAGING `staging_ingest` と種別別 STAGING へロードする
6. 汎用 RAW `raw_ingest` と種別別 RAW へロードする
7. `ice_qb_aggregation` の共通マスタを参照する
8. `sql/source_build.sql` を実行して SOURCE テーブルを作成する
9. `sql/source_quality_checks.sql` を実行し、結果を監査テーブルへ保存する
10. BigQuery 監査ログと Cloud Logging に結果を記録する
11. 後続フェーズで加工済みファイルを加工データDriveへ出力する

## SQL files

### `sql/staging_tables.sql`

- 汎用 STAGING 受け口 `staging_ingest`
- 対象ファイル別の `staging_*` テーブル
- Drive 取得直後の標準化済み行を一時格納する

### `sql/raw_tables.sql`

- 汎用 RAW 受け口 `raw_ingest`
- 対象ファイル別の RAW テーブル
- `loaded_at` パーティション、`target_month` ベースのクラスタリング

### `sql/source_build.sql`

- 商品マスタ、著者条件一覧、電子出版確報明細、月別電子出版プロダクト別売上の SOURCE 化
- 型変換、`target_month`、`product_key`、商品マスタ補完
- Access取込後相当の結果をBigQueryで再現する

### `sql/source_quality_checks.sql`

次のチェック結果を `BQ_AUDIT_DATASET.source_quality_results` に記録する。

- 必須列 NULL
- `target_month` NULL / 対象月不一致
- 商品コード / 電子出版コード未補完
- 商品マスタ / 著者条件未一致
- 重複キー
- 売上数 / 売上額の型変換失敗
- SOURCE 件数 0 件

## GitHub Actions

### Deploy workflow

`.github/workflows/deploy-cloud-run.yml` は次を担当する。

- コンテナイメージの build
- Artifact Registry への push
- Cloud Run Job `royalty-source-job` の作成または更新
- 実運用環境変数の設定

Deploy workflow はジョブを配置するためのもので、月次処理の実行とは分離する。

### Run workflow

`.github/workflows/run-cloud-run.yml` は次を担当する。

- `workflow_dispatch` から対象月を受け取る
- `FAIL_ON_QUALITY_ERRORS` を指定する
- 配置済み Cloud Run Job を実行する
- 実行完了を待機する

次フェーズで、`royalty_audit.pipeline_audit_log` と `royalty_audit.source_quality_results` を参照し、GitHub Step Summary に件数・エラー・警告を表示する。

## Access migration validation

Access置換では、同じ対象月について次を比較する。

1. Accessで生成した従来結果
2. Cloud Run / BigQueryで生成したSOURCE結果
3. レコード件数
4. 主キーまたは業務キー単位の差分
5. 金額・数量の合計差分
6. 商品マスタ・著者条件の未一致件数
7. 品質チェック結果

差分がある場合は、RAW、AGGREGATION参照、SOURCE変換のどの段階で生じたかを切り分ける。

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
set -a
. .env
set +a
python -m app.main
```

Cloud Run Job のサービスアカウントには、Drive読み取り、将来のDrive書き込み、BigQuery dataset/table作成、BigQuery data editor、BigQuery job user相当の権限を付与する。

## Deploy defaults

- GCP project: `ice-qb`
- Region: `asia-northeast1`
- Artifact Registry repository: `royalty-source`
- Cloud Run Job: `royalty-source-job`

詳細は `docs/deploy.md` を参照する。

## Implementation prompt

エージェント実装時は `prompts/implementation-start-prompt.md` を使用する。

プロンプトには、実運用Driveフォルダ、STAGING / RAW / AGGREGATION / SOURCE の責務、品質チェック、監査ログ、加工データDrive出力の段階実装方針を記載する。

## Next work

1. `prompts/implementation-start-prompt.md` を現行構成へ更新する
2. Run workflow に BigQuery 監査サマリーを追加する
3. 加工済みファイルの形式・ファイル名・世代管理方法を確定する
4. `app/drive_client.py` に加工データDriveへのアップロード処理を追加する
5. Access結果との月次差分検証を自動化する
6. SOURCE品質チェックの閾値を実運用基準に合わせる
