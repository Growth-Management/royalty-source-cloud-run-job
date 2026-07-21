from __future__ import annotations

import os
from pydantic import BaseModel


class Settings(BaseModel):
    gcp_project_id: str
    bq_staging_dataset: str
    bq_raw_dataset: str
    bq_source_dataset: str
    bq_audit_dataset: str
    bq_location: str = "asia-northeast1"
    drive_source_folder_id: str
    job_target_month: str | None = None
    log_level: str = "INFO"
    fail_on_quality_errors: bool = True
    max_generic_columns: int = 40

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            gcp_project_id=os.environ["GCP_PROJECT_ID"],
            bq_staging_dataset=os.environ["BQ_STAGING_DATASET"],
            bq_raw_dataset=os.environ["BQ_RAW_DATASET"],
            bq_source_dataset=os.environ["BQ_SOURCE_DATASET"],
            bq_audit_dataset=os.environ["BQ_AUDIT_DATASET"],
            bq_location=os.getenv("BQ_LOCATION", "asia-northeast1"),
            drive_source_folder_id=os.environ["DRIVE_SOURCE_FOLDER_ID"],
            job_target_month=os.getenv("JOB_TARGET_MONTH"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            fail_on_quality_errors=os.getenv("FAIL_ON_QUALITY_ERRORS", "true").lower() in {"1", "true", "yes", "y"},
            max_generic_columns=int(os.getenv("MAX_GENERIC_COLUMNS", "40")),
        )
