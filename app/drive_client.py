from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import io
import re
from typing import Iterable


EXCEL_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SourceKind(StrEnum):
    PRODUCT_MASTER = "product_master"
    AUTHOR_CONDITIONS = "author_conditions"
    EP_STATEMENT_DETAIL = "ep_statement_detail"
    MONTHLY_PRODUCT_SALES = "monthly_product_sales"


SOURCE_KIND_LABELS = {
    SourceKind.PRODUCT_MASTER: "商品マスタ",
    SourceKind.AUTHOR_CONDITIONS: "著者条件一覧",
    SourceKind.EP_STATEMENT_DETAIL: "電子出版確報明細",
    SourceKind.MONTHLY_PRODUCT_SALES: "月別電子出版プロダクト別売上",
}


@dataclass
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    source_kind: SourceKind
    target_month: str | None
    modified_time: str | None = None


class DriveClient:
    def __init__(self, folder_id: str):
        from googleapiclient.discovery import build

        self.folder_id = folder_id
        self.service = build("drive", "v3", cache_discovery=False)

    def list_candidate_files(self, target_month: str | None = None) -> list[DriveFile]:
        candidates = []
        for item in self._list_folder_files():
            source_kind = classify_source_file(item["name"])
            if source_kind:
                candidates.append(DriveFile(
                    file_id=item["id"],
                    name=item["name"],
                    mime_type=item.get("mimeType", ""),
                    source_kind=source_kind,
                    target_month=extract_target_month(item["name"]),
                    modified_time=item.get("modifiedTime"),
                ))
        return select_source_files(candidates, target_month)

    def download_file(self, drive_file: DriveFile) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        if drive_file.mime_type == "application/vnd.google-apps.spreadsheet":
            request = self.service.files().export_media(
                fileId=drive_file.file_id,
                mimeType=EXCEL_MIME_TYPE,
            )
        else:
            request = self.service.files().get_media(fileId=drive_file.file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()

    def upload_excel(self, file_name: str, payload: bytes) -> str:
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(io.BytesIO(payload), mimetype=EXCEL_MIME_TYPE, resumable=True)
        existing = self._find_file_by_name(file_name)
        if existing:
            result = self.service.files().update(
                fileId=existing["id"],
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            ).execute()
        else:
            result = self.service.files().create(
                body={"name": file_name, "parents": [self.folder_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            ).execute()
        return result["id"]

    def _find_file_by_name(self, file_name: str) -> dict | None:
        escaped_name = file_name.replace("'", "\\'")
        query = (
            f"'{self.folder_id}' in parents and trashed = false "
            f"and name = '{escaped_name}' and mimeType != 'application/vnd.google-apps.folder'"
        )
        response = self.service.files().list(
            q=query,
            fields="files(id, name, modifiedTime)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=10,
        ).execute()
        files = response.get("files", [])
        if not files:
            return None
        return max(files, key=lambda item: _parse_time(item.get("modifiedTime")))

    def _list_folder_files(self) -> list[dict]:
        query = f"'{self.folder_id}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
        files: list[dict] = []
        page_token = None
        while True:
            response = self.service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageSize=1000,
            ).execute()
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return files


def classify_source_file(file_name: str) -> SourceKind | None:
    if re.fullmatch(r"【商品マスタ】report.*\.xls", file_name, flags=re.IGNORECASE):
        return SourceKind.PRODUCT_MASTER
    if re.fullmatch(r"【著者条件一覧】report.*\.xls", file_name, flags=re.IGNORECASE):
        return SourceKind.AUTHOR_CONDITIONS
    if re.fullmatch(r"電子出版確報明細データICE_.*反映版\.xlsx", file_name, flags=re.IGNORECASE):
        return SourceKind.EP_STATEMENT_DETAIL
    if re.fullmatch(r"月別電子出版プロダクト別売上一覧\(ICE印税用\)_.*反映版\.xlsx", file_name, flags=re.IGNORECASE):
        return SourceKind.MONTHLY_PRODUCT_SALES
    return None


def extract_target_month(file_name: str) -> str | None:
    match = re.search(r"(20\d{2})(?:年)?(0[1-9]|1[0-2])", file_name)
    return "".join(match.groups()) if match else None


def select_source_files(files: Iterable[DriveFile], target_month: str | None) -> list[DriveFile]:
    grouped = {kind: [] for kind in SourceKind}
    for file in files:
        grouped[file.source_kind].append(file)
    missing = [SOURCE_KIND_LABELS[k] for k, values in grouped.items() if not values]
    if missing:
        raise FileNotFoundError(f"required source file not found: {', '.join(missing)}")
    return [_select_one(kind, candidates, target_month) for kind, candidates in grouped.items()]


def _select_one(source_kind: SourceKind, candidates: list[DriveFile], target_month: str | None) -> DriveFile:
    if target_month:
        matches = [file for file in candidates if file.target_month == target_month]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return _latest_unique(source_kind, matches)
    return _latest_unique(source_kind, candidates)


def _latest_unique(source_kind: SourceKind, candidates: list[DriveFile]) -> DriveFile:
    if len(candidates) == 1:
        return candidates[0]
    ordered = sorted(candidates, key=lambda f: (_parse_time(f.modified_time), f.name), reverse=True)
    latest = _parse_time(ordered[0].modified_time)
    tied = [file for file in ordered if _parse_time(file.modified_time) == latest]
    if len(tied) > 1:
        raise ValueError(f"multiple latest files for {SOURCE_KIND_LABELS[source_kind]}: {', '.join(f.name for f in tied)}")
    return ordered[0]


def _parse_time(value: str | None) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.min.replace(tzinfo=timezone.utc)
