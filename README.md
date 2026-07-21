# royalty-source-cloud-run-job

Cloud Run Job で電子出版印税データ基盤の **STAGING → RAW → SOURCE** を処理する初版です。

## Scope
- Google Drive 上の加工前ファイルを走査する
- 対象ファイルを種別判定する
- ダウンロードして STAGING 入力を作る
- STAGING データセットへ一時格納する
- RAW へロードする
- SOURCE 生成 SQL と SOURCE 品質チェック SQL を実行する
- 監査ログを BigQuery と Cloud Logging に出力する

## Dataset policy
- `BQ_STAGING_DATASET`: Drive 取得直後の一時受け、シート展開、標準化結果を置く
- `BQ_RAW_DATASET`: 監査・再取込・差分確認のための元データを保全する
- `BQ_SOURCE_DATASET`: Access 取込後相当の整形済みデータを置く

STAGING と RAW は別データセットに分けて管理する。

## Directory
- `app/main.py`: エントリポイント
- `app/settings.py`: 環境変数設定
- `app/pipeline.py`: パイプライン制御
- `app/drive_client.py`: Drive 走査とダウンロード
- `app/bigquery_client.py`: BigQuery ロードと SQL 実行
- `app/parsers.py`: xls/xlsx 入力の標準化
- `sql/staging_tables.sql`: STAGING テーブル設計初版
- `sql/raw_tables.sql`: RAW テーブル設計初版
- `sql/source_build.sql`: SOURCE 生成 SQL 雛形
- `sql/source_quality_checks.sql`: SOURCE 品質チェック雛形
- `docs/source_header_mapping.md`: 実 Drive ファイル確認に基づくヘッダー/固定列マッピング
- `docs/deploy.md`: GitHub Actions から Cloud Run Job へ deploy する手順
- `.github/workflows/deploy-cloud-run.yml`: Cloud Run Job deploy workflow
- `prompts/implementation-start-prompt.md`: 実装開始用プロンプト
- `.env.example`: 必須環境変数例
- `requirements.txt`: Python 依存
- `Dockerfile`: Cloud Run Job 用コンテナ定義

## Required environment variables
- `GCP_PROJECT_ID`
- `BQ_STAGING_DATASET`
- `BQ_RAW_DATASET`
- `BQ_SOURCE_DATASET`
- `BQ_AUDIT_DATASET`
- `BQ_LOCATION` (任意。既定 `asia-northeast1`)
- `DRIVE_SOURCE_FOLDER_ID`
- `GOOGLE_APPLICATION_CREDENTIALS` または Workload Identity
- `JOB_TARGET_MONTH` (任意。未指定なら最新対象を選ぶ)
- `LOG_LEVEL` (任意。既定 `INFO`)
- `FAIL_ON_QUALITY_ERRORS` (任意。既定 `true`)
- `MAX_GENERIC_COLUMNS` (任意。既定 `40`)

## Runtime flow
1. Drive フォルダを走査
2. 対象 4 種のファイル種別と対象月を推定
3. 対象ファイルをダウンロード
4. xlsx / xls / html-xls を解析し、シート名・行番号・元ファイル情報を保持
5. 汎用 STAGING `staging_ingest` と種別別 STAGING へロード
6. 汎用 RAW `raw_ingest` と種別別 RAW へロード
7. `sql/source_build.sql` を実行して SOURCE 4 テーブルを作成
8. `sql/source_quality_checks.sql` を実行し、結果を監査テーブルへ保存
9. BigQuery 監査ログと Cloud Logging に結果を記録

## Initial SQL files
### `sql/staging_tables.sql`
- 汎用 STAGING 受け口 `staging_ingest`
- 対象ファイル別の `staging_*` テーブル初版
- Drive 取得直後の標準化済み行を一時格納する前提

### `sql/raw_tables.sql`
- 汎用 RAW 受け口 `raw_ingest`
- 対象ファイル別の RAW テーブル初版
- `loaded_at` パーティション、`target_month` ベースのクラスタリング

### `sql/source_build.sql`
- 商品マスタ、著者条件一覧、電子出版確報明細、月別電子出版プロダクト別売上の SOURCE 化雛形
- 型変換、`target_month`、`product_key`、商品マスタ補完の初版
- 実データ確認後に列マッピングと日付変換を調整する前提

### `sql/source_quality_checks.sql`
- 必須列 NULL
- `target_month` NULL / 対象月不一致
- 商品コード / 電子出版コード未補完
- 商品マスタ / 著者条件未一致
- 重複キー
- 売上数 / 売上額の型変換失敗
- SOURCE 件数 0 件
を `BQ_AUDIT_DATASET.source_quality_results` に記録する。

## Run locally
```bash
cd cloud-run-source-job
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
set -a
. .env
set +a
python -m app.main
```

Cloud Run Job では Workload Identity またはサービスアカウントに、Drive 読み取り、BigQuery dataset/table 作成、BigQuery data editor、BigQuery job user 相当の権限を付与する。

## Deploy

GitHub Actions の `.github/workflows/deploy-cloud-run.yml` から Cloud Run Job `royalty-source-job` へ deploy する。

既定値:

- GCP project: `ice-qb`
- Region: `asia-northeast1`
- Artifact Registry repository: `royalty-source`
- Cloud Run Job: `royalty-source-job`

詳細は `docs/deploy.md` を参照する。

## Implementation prompt
実装開始時は `prompts/implementation-start-prompt.md` を使用する。

このプロンプトは、Drive 取得、Excel 解析、STAGING ロード、RAW ロード、SOURCE 生成 SQL、SOURCE 品質チェック、監査ログ、エラー処理までを対象にしている。

## Next work
- 実ファイルの列マッピングを `app/parsers.py` の alias に合わせて調整する
- STAGING データセットへ置く中間テーブル粒度を確定する
- `sql/raw_tables.sql` の列型と列名を実ファイルに合わせて確定する
- SOURCE 品質チェックの閾値を実運用基準に合わせる
- 通知先 (Slack / Gmail など) を必要に応じて追加する
