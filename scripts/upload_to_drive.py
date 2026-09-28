"""TodenYOLO.exe を Google Drive の指定フォルダへアップロードする。

GitHub Actions の ``build-windows-exe.yml`` から呼ぶ想定。必要な環境変数:

- ``GOOGLE_DRIVE_CREDENTIALS_JSON``: サービスアカウント鍵 JSON の中身
- ``GOOGLE_DRIVE_FOLDER_ID``: 保存先フォルダ ID（共有ドライブ内のフォルダも可）
- ``DRIVE_FILE_NAME``（任意）: Drive 上のファイル名。既定はローカルファイル名
- ``SOURCE_URL``（任意）: 配布 EXE に対応するソースのコミット URL

同名ファイルがフォルダ内に既にあれば上書き更新する（drive.file スコープのため、
更新できるのはこのサービスアカウント自身が作成したファイルのみ）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def main() -> int:
    file_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist/TodenYOLO.exe")
    creds_json = os.environ.get("GOOGLE_DRIVE_CREDENTIALS_JSON", "").strip()
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()

    if not file_path.is_file():
        print(f"アップロード対象が見つかりません: {file_path}", file=sys.stderr)
        return 1
    if not creds_json or not folder_id:
        print(
            "GOOGLE_DRIVE_CREDENTIALS_JSON / GOOGLE_DRIVE_FOLDER_ID が未設定です",
            file=sys.stderr,
        )
        return 1

    name = os.environ.get("DRIVE_FILE_NAME") or file_path.name
    source_url = os.environ.get("SOURCE_URL", "").strip()
    metadata = {"description": f"AGPL-3.0 対応ソース: {source_url}"} if source_url else {}
    creds = Credentials.from_service_account_info(
        json.loads(creds_json), scopes=SCOPES
    )
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    media = MediaFileUpload(str(file_path), resumable=True)

    query = (
        f"'{folder_id}' in parents and "
        f"name = '{_escape_query_value(name)}' and trashed = false"
    )
    existing = (
        drive.files()
        .list(
            q=query,
            fields="files(id,name)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
        .get("files", [])
    )

    if existing:
        target = existing[0]
        drive.files().update(
            fileId=target["id"], body=metadata, media_body=media, supportsAllDrives=True
        ).execute()
        print(
            f"Google Drive の既存ファイルを更新しました: {name} (id={target['id']})"
        )
    else:
        created = (
            drive.files()
            .create(
                body={"name": name, "parents": [folder_id], **metadata},
                media_body=media,
                fields="id,name",
                supportsAllDrives=True,
            )
            .execute()
        )
        print(
            f"Google Drive にアップロードしました: {created['name']} (id={created['id']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
