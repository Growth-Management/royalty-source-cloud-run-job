# Deploy

Cloud Run Job `royalty-source-job` の GitHub Actions deploy 手順です。

## 前提

- GCP project: `ice-qb`
- deploy branch: `main`
- region: `asia-northeast1`
- Cloud Run Job: `royalty-source-job`
- Artifact Registry repository: `asia-northeast1-docker.pkg.dev/ice-qb/royalty-source/royalty-source-job`
- Workflow: `.github/workflows/deploy-cloud-run.yml`

## GitHub Settings

Repository secrets:

- `GCP_DEPLOY_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_DEPLOY_SERVICE_ACCOUNT`

Repository variables:

- `CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT`

`GCP_DEPLOY_SERVICE_ACCOUNT` は deploy 用サービスアカウントです。Cloud Run Job の実行時サービスアカウントとは分けます。

## Required IAM

Deploy service account:

- `roles/run.developer`
- `roles/artifactregistry.writer`
- `roles/iam.serviceAccountUser` on the runtime service account

Runtime service account:

- Google Drive source folder read access
- `roles/bigquery.jobUser`
- BigQuery dataset/table 作成と書き込みに必要な権限
- 必要に応じて Cloud Logging writer

## Initial deploy checklist

初回 deploy 前に次を確認します。

- GitHub secrets `GCP_DEPLOY_WORKLOAD_IDENTITY_PROVIDER` と `GCP_DEPLOY_SERVICE_ACCOUNT` が設定済み
- GitHub variable `CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT` が設定済み
- Artifact Registry repository `royalty-source` が `asia-northeast1` に作成済み
- Workload Identity provider condition が repository `Growth-Management/royalty-source-cloud-run-job`、branch `main`、workflow `Deploy Cloud Run Job` を許可している
- Runtime service account `royalty-source-runner@ice-qb.iam.gserviceaccount.com` に Drive source folder Viewer と BigQuery 書き込み権限がある

## Deploy

通常は `main` への push または GitHub Actions の手動実行で deploy します。

手動実行時は任意で `target_month` を指定できます。例:

```text
202506
```

未指定の場合、Job は Drive フォルダ内の対象ファイルから最新候補を選びます。

## Verify

deploy workflow の最後で次を確認します。

- Cloud Run Job が存在する
- deploy された image が commit SHA tag を指している

Job を実行する場合は、デプロイ後に明示的に実行します。

```bash
gcloud run jobs execute royalty-source-job \
  --project ice-qb \
  --region asia-northeast1 \
  --wait
```

## Scope

この Job は STAGING -> RAW -> SOURCE までを対象にします。
AGGREGATION / PROCESS / DATAMART / MERGE / UPSERT は今回の deploy 対象に含めません。
