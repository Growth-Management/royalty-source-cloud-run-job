#!/usr/bin/env python3
"""Download a Google Drive file by ID or by exact name within a folder."""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--file-id")
    selector.add_argument("--name")
    parser.add_argument("--folder-id")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def resolve_file_id(service, *, file_id: str | None, name: str | None, folder_id: str | None) -> str:
    if file_id:
        return file_id
    if not name or not folder_id:
        raise ValueError("--name requires --folder-id")

    query = (
        f"name = '{escape_query(name)}' and "
        f"'{escape_query(folder_id)}' in parents and trashed = false"
    )
    response = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id,name,modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=10,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = response.get("files", [])
    if not files:
        raise FileNotFoundError(f"Drive file not found: {name}")
    return files[0]["id"]


def main() -> None:
    args = parse_args()
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    file_id = resolve_file_id(
        service,
        file_id=args.file_id,
        name=args.name,
        folder_id=args.folder_id,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    output.write_bytes(buffer.getvalue())
    print(f"Downloaded Drive file {file_id} to {output}")


if __name__ == "__main__":
    main()
