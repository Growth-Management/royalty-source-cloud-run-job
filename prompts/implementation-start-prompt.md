# 実装開始プロンプト

電子出版印税データ基盤の Cloud Run Job 初版です。

## スコープ

Google Drive 上の加工前 Excel / xls-html / xlsx ファイルを取得し、BigQuery 上で STAGING -> RAW -> SOURCE までを処理します。

## 前提

- 加工前 Google Drive データを正本とする。
- STAGING と RAW は別 BigQuery データセットで管理する。
- STAGING / RAW には業務ロジックを持たせない。
- SOURCE は MS Access 取込後相当の整形済みデータとする。
- AGGREGATION / PROCESS / DATAMART / MERGE / UPSERT は今回スコープ外。
- Cloud Run 側は制御、Drive 取得、ファイル解析、BigQuery ロード、SQL 実行制御、監査ログ出力を担当する。
- 業務ロジックは可能な限り BigQuery SQL 側に寄せる。

## 対象ファイル

- `【商品マスタ】report*.xls`
- `【著者条件一覧】report*.xls`
- `電子出版確報明細データICE_*反映版.xlsx`
- `月別電子出版プロダクト別売上一覧(ICE印税用)_*反映版.xlsx`

## 完了条件

- `python -m app.main` でエントリポイントが動く。
- STAGING -> RAW -> SOURCE の流れがコードと README で一致している。
- Python 構文チェックが通る。
- 後段スコープの実装が混入していない。
