from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import io
import logging

import pandas as pd

from app.bigquery_client import BigQueryClient
from app.drive_client import DriveClient, DriveFile, SourceKind
from app.parsers import ParsedSheet, WorkbookParser
from app.settings import Settings


RAW_TABLE_BY_KIND = {
    SourceKind.PRODUCT_MASTER: "raw_product_master",
    SourceKind.AUTHOR_CONDITIONS: "raw_author_conditions",
    SourceKind.EP_STATEMENT_DETAIL: "raw_ep_statement_detail",
    SourceKind.MONTHLY_PRODUCT_SALES: "raw_monthly_product_sales",
}
STAGING_TABLE_BY_KIND = {
    SourceKind.PRODUCT_MASTER: "staging_product_master",
    SourceKind.AUTHOR_CONDITIONS: "staging_author_conditions",
    SourceKind.EP_STATEMENT_DETAIL: "staging_ep_statement_detail",
    SourceKind.MONTHLY_PRODUCT_SALES: "staging_monthly_product_sales",
}
SOURCE_TABLES = ("source_product_master", "source_author_conditions", "source_ep_statement_detail", "source_monthly_product_sales")
ACCESS_WORKBOOK_SHEETS = {
    "sales": "access_input_sales",
    "store_detail": "access_input_store_detail",
    "author_conditions": "access_input_author_conditions",
}
POD_WORKBOOK_SHEETS = {
    "pod_sales": "access_input_pod_sales",
}


@dataclass
class AuditStats:
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    target_month: str | None = None
    staging_rows_loaded: int = 0
    raw_rows_loaded: int = 0
    source_rows_created: int = 0
    source_quality_error_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    status: str = "RUNNING"
    error_message: str = ""
    audit_rows: list[dict] = field(default_factory=list)


class SourcePipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.drive = DriveClient(settings.drive_source_folder_id)
        self.output_drive = DriveClient(settings.drive_output_folder_id)
        self.parser = WorkbookParser(settings.max_generic_columns)
        self.bq = BigQueryClient(settings.gcp_project_id, settings.bq_location)
        self.base_dir = Path(__file__).resolve().parents[1]
        self.logger = logging.getLogger(__name__)

    def run(self) -> AuditStats:
        stats = AuditStats(run_id=str(uuid4()), started_at=datetime.now(timezone.utc))
        try:
            self._prepare_bigquery()
            files = self.drive.list_candidate_files(self.settings.job_target_month)
            stats.target_month = self._resolve_target_month(files)
            for drive_file in files:
                self._process_file(stats, drive_file)
            self._build_source(stats)
            self._build_access_equivalent_tables(stats)
            self._run_quality_checks(stats)
            if stats.source_rows_created == 0:
                stats.source_quality_error_count += 1
                raise RuntimeError("SOURCE rows created is 0")
            if self.settings.fail_on_quality_errors and stats.source_quality_error_count > 0:
                raise RuntimeError(f"Quality checks failed: {stats.source_quality_error_count} errors")
            if self.settings.export_output_files:
                self._export_comparison_workbooks(stats)
            stats.status = "SUCCESS"
            return stats
        except Exception as exc:
            stats.status = "FAILED"
            stats.error_count += 1
            stats.error_message = str(exc)
            raise
        finally:
            stats.finished_at = datetime.now(timezone.utc)
            self._write_audit(stats)

    def _prepare_bigquery(self) -> None:
        for dataset in (self.settings.bq_staging_dataset, self.settings.bq_raw_dataset, self.settings.bq_source_dataset, self.settings.bq_audit_dataset):
            self.bq.ensure_dataset(dataset)
        self.bq.ensure_audit_tables(self.settings.bq_audit_dataset)
        self.bq.run_sql_file(self.base_dir / "sql" / "staging_tables.sql", self._sql_replacements(self.settings.job_target_month or ""))
        self.bq.run_sql_file(self.base_dir / "sql" / "raw_tables.sql", self._sql_replacements(self.settings.job_target_month or ""))

    def _process_file(self, stats: AuditStats, drive_file: DriveFile) -> None:
        payload = self.drive.download_file(drive_file)
        for sheet in self.parser.parse(drive_file.name, payload, drive_file.source_kind):
            self._process_sheet(stats, drive_file, sheet)

    def _process_sheet(self, stats: AuditStats, drive_file: DriveFile, sheet: ParsedSheet) -> None:
        target_month = stats.target_month or drive_file.target_month
        if not target_month:
            raise ValueError(f"target_month could not be resolved: {drive_file.name}")
        loaded_at = datetime.now(timezone.utc)
        generic = self._add_metadata(sheet.generic_dataframe, drive_file, sheet.sheet_name, target_month, loaded_at, False)
        mapped_staging = self._add_metadata(sheet.mapped_dataframe, drive_file, sheet.sheet_name, target_month, loaded_at, True)
        mapped_raw = self._add_metadata(sheet.mapped_dataframe, drive_file, sheet.sheet_name, target_month, loaded_at, False)

        staging_rows = self.bq.load_dataframe(generic, self._table(self.settings.bq_staging_dataset, "staging_ingest"))
        raw_rows = self.bq.load_dataframe(generic.copy(), self._table(self.settings.bq_raw_dataset, "raw_ingest"))
        staging_rows += self.bq.load_dataframe(mapped_staging, self._table(self.settings.bq_staging_dataset, STAGING_TABLE_BY_KIND[drive_file.source_kind]))
        raw_rows += self.bq.load_dataframe(mapped_raw, self._table(self.settings.bq_raw_dataset, RAW_TABLE_BY_KIND[drive_file.source_kind]))

        stats.staging_rows_loaded += staging_rows
        stats.raw_rows_loaded += raw_rows
        stats.warning_count += len(sheet.warnings)
        stats.audit_rows.append(self._audit_row(stats, drive_file, sheet.sheet_name, staging_rows, raw_rows, len(sheet.warnings), "LOADED"))

    def _build_source(self, stats: AuditStats) -> None:
        if not stats.target_month:
            raise ValueError("target_month is required before SOURCE build")
        self.bq.run_sql_file(self.base_dir / "sql" / "source_build.sql", self._sql_replacements(stats.target_month))
        stats.source_rows_created = sum(self.bq.count_rows(self._table(self.settings.bq_source_dataset, table), stats.target_month) for table in SOURCE_TABLES)

    def _build_access_equivalent_tables(self, stats: AuditStats) -> None:
        if not stats.target_month:
            raise ValueError("target_month is required before Access-equivalent table build")
        replacements = self._sql_replacements(stats.target_month)
        self.bq.run_sql_file(self.base_dir / "sql" / "access_input_build.sql", replacements)
        self.bq.run_sql_file(self.base_dir / "sql" / "pod_input_build.sql", replacements)

    def _run_quality_checks(self, stats: AuditStats) -> None:
        if not stats.target_month:
            raise ValueError("target_month is required before quality checks")
        replacements = self._sql_replacements(stats.target_month) | {"run_id": stats.run_id}
        self.bq.run_sql_file(self.base_dir / "sql" / "source_quality_checks.sql", replacements)
        rows = self.bq.run_sql_file(self.base_dir / "sql" / "access_input_quality_checks.sql", replacements)
        stats.source_quality_error_count = int(rows[0]["total_error_count"]) if rows else 0

    def _export_comparison_workbooks(self, stats: AuditStats) -> None:
        if not stats.target_month:
            raise ValueError("target_month is required before comparison workbook export")
        accounting_month = stats.target_month.replace("-", "")
        access_file_name = f"comparison_access_input_{accounting_month}.xlsx"
        pod_file_name = f"comparison_pod_input_{accounting_month}.xlsx"

        access_payload = self._build_workbook(ACCESS_WORKBOOK_SHEETS)
        pod_payload = self._build_workbook(POD_WORKBOOK_SHEETS)
        access_file_id = self.output_drive.upload_excel(access_file_name, access_payload)
        pod_file_id = self.output_drive.upload_excel(pod_file_name, pod_payload)
        self.logger.info(
            "comparison workbooks exported",
            extra={
                "run_id": stats.run_id,
                "target_month": stats.target_month,
                "access_file_name": access_file_name,
                "access_file_id": access_file_id,
                "pod_file_name": pod_file_name,
                "pod_file_id": pod_file_id,
            },
        )

    def _build_workbook(self, sheet_tables: dict[str, str]) -> bytes:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            for sheet_name, table_name in sheet_tables.items():
                dataframe = self.bq.read_table_dataframe(self._table(self.settings.bq_source_dataset, table_name))
                dataframe.to_excel(writer, sheet_name=sheet_name, index=False)
        return buffer.getvalue()

    def _write_audit(self, stats: AuditStats) -> None:
        rows = stats.audit_rows + [self._audit_row(stats, None, None, stats.staging_rows_loaded, stats.raw_rows_loaded, stats.warning_count, stats.status)]
        try:
            self.bq.insert_json_rows(self._table(self.settings.bq_audit_dataset, "pipeline_audit_log"), rows)
        except Exception:
            self.logger.exception("failed to write audit log", extra={"run_id": stats.run_id})

    def _add_metadata(self, dataframe: pd.DataFrame, drive_file: DriveFile, sheet_name: str, target_month: str, loaded_at: datetime, raw_prefix: bool) -> pd.DataFrame:
        working = dataframe.copy()
        if raw_prefix:
            working = working.rename(columns={c: f"raw_{c}" for c in working.columns if c != "row_number" and not c.startswith("raw_")})
        working.insert(1, "target_month", target_month)
        working.insert(2, "loaded_at", loaded_at)
        working.insert(3, "source_file_id", drive_file.file_id)
        working.insert(4, "source_file_name", drive_file.name)
        working.insert(5, "source_sheet_name", sheet_name)
        return working.fillna("")

    def _resolve_target_month(self, files: list[DriveFile]) -> str:
        if self.settings.job_target_month:
            return self.settings.job_target_month
        months = sorted({file.target_month for file in files if file.target_month})
        if not months:
            raise ValueError("target_month could not be extracted from selected source files")
        return months[-1]

    def _audit_row(self, stats: AuditStats, drive_file: DriveFile | None, sheet_name: str | None, staging_rows: int, raw_rows: int, warning_count: int, status: str) -> dict:
        return {
            "run_id": stats.run_id,
            "started_at": stats.started_at.isoformat(),
            "finished_at": (stats.finished_at or datetime.now(timezone.utc)).isoformat(),
            "target_month": stats.target_month,
            "source_kind": drive_file.source_kind.value if drive_file else None,
            "source_file_id": drive_file.file_id if drive_file else None,
            "source_file_name": drive_file.name if drive_file else None,
            "source_sheet_name": sheet_name,
            "staging_rows_loaded": staging_rows,
            "raw_rows_loaded": raw_rows,
            "source_rows_created": stats.source_rows_created,
            "source_quality_error_count": stats.source_quality_error_count,
            "warning_count": warning_count,
            "error_count": stats.error_count,
            "status": status,
            "error_message": stats.error_message,
        }

    def _sql_replacements(self, target_month: str) -> dict[str, str]:
        return {
            "project_id": self.settings.gcp_project_id,
            "staging_dataset": self.settings.bq_staging_dataset,
            "raw_dataset": self.settings.bq_raw_dataset,
            "source_dataset": self.settings.bq_source_dataset,
            "audit_dataset": self.settings.bq_audit_dataset,
            "target_month": target_month,
            "run_id": "",
        }

    def _table(self, dataset: str, table: str) -> str:
        return f"{self.settings.gcp_project_id}.{dataset}.{table}"
