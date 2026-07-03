import asyncio
import io
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noteturner.config.settings import Settings
from noteturner.debug_session import agent_log

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_DOC = "application/vnd.google-apps.document"
MIME_SLIDES = "application/vnd.google-apps.presentation"
MIME_SHEET = "application/vnd.google-apps.spreadsheet"
MIME_PDF = "application/pdf"

# Maps a Google Drive MIME type to our internal record_type label.
RECORD_TYPE_BY_MIME = {
    MIME_DOC: "doc",
    MIME_SLIDES: "slides",
    MIME_SHEET: "sheet",
    MIME_PDF: "pdf",
}


class GoogleDriveError(Exception):
    pass


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str

    @property
    def record_type(self) -> str:
        return RECORD_TYPE_BY_MIME.get(self.mime_type, "unknown")


def _rows_to_text(sheet_title: str, rows: list[list[Any]]) -> str:
    """Serialize spreadsheet rows into a compact text block for embedding."""
    lines = [f"# {sheet_title}"]
    for row in rows:
        cells = [str(cell) for cell in row if str(cell).strip()]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


class GoogleDriveClient:
    """Reads files from a Google Drive folder using a service account."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._drive: Any = None
        self._sheets: Any = None
        self._lock = threading.RLock()

    @property
    def is_configured(self) -> bool:
        configured = self._settings.gdrive_is_configured
        # region agent log
        agent_log(
            location="gdrive.py:is_configured",
            message="gdrive configured check",
            data={
                "configured": configured,
                "has_folder_id": bool(self._settings.gdrive_folder_id.strip()),
                "has_project_id": bool(self._settings.google_project_id.strip()),
                "has_email": bool(self._settings.google_service_account_email.strip()),
                "has_private_key": bool(self._settings.google_private_key.strip()),
            },
            hypothesis_id="H4",
        )
        # endregion
        return configured

    def _build_services(self) -> tuple[Any, Any]:
        with self._lock:
            if self._drive is not None and self._sheets is not None:
                return self._drive, self._sheets

            # region agent log
            agent_log(
                location="gdrive.py:_build_services",
                message="building google drive services",
                data={},
                hypothesis_id="H1",
            )
            # endregion

            # Imported lazily so the module can be imported without the Google
            # client libraries installed (e.g. in unit tests that only touch helpers).
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            try:
                info = self._settings.google_service_account_info()
            except (ValueError, json.JSONDecodeError) as exc:
                raise GoogleDriveError(f"Invalid Google service account settings: {exc}") from exc

            credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            self._drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
            self._sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)

            # region agent log
            agent_log(
                location="gdrive.py:_build_services",
                message="google drive services built",
                data={},
                hypothesis_id="H1",
            )
            # endregion
            return self._drive, self._sheets

    async def list_files(self) -> list[DriveFile]:
        if not self.is_configured:
            raise GoogleDriveError(
                "Google Drive is not configured "
                "(GDRIVE_FOLDER_ID + GOOGLE_PROJECT_ID / GOOGLE_SERVICE_ACCOUNT_EMAIL / …)"
            )
        return await asyncio.to_thread(self._list_files_sync, self._settings.gdrive_folder_id)

    def _list_files_sync(self, folder_id: str) -> list[DriveFile]:
        with self._lock:
            return self._list_files_sync_locked(folder_id)

    def _list_files_sync_locked(self, folder_id: str) -> list[DriveFile]:
        # region agent log
        agent_log(
            location="gdrive.py:_list_files_sync",
            message="list_files start",
            data={"folder_id_len": len(folder_id)},
            hypothesis_id="H1",
        )
        # endregion
        drive, _ = self._build_services()
        collected: list[DriveFile] = []
        stack = [folder_id]
        visited: set[str] = set()

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            page_token: str | None = None
            while True:
                response = (
                    drive.files()
                    .list(
                        q=f"'{current}' in parents and trashed = false",
                        fields="nextPageToken, files(id, name, mimeType)",
                        pageSize=100,
                        pageToken=page_token,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute()
                )
                for item in response.get("files", []):
                    mime = item["mimeType"]
                    if mime == MIME_FOLDER:
                        stack.append(item["id"])
                    else:
                        collected.append(
                            DriveFile(id=item["id"], name=item["name"], mime_type=mime)
                        )
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

        # region agent log
        agent_log(
            location="gdrive.py:_list_files_sync",
            message="list_files done",
            data={"files_count": len(collected), "visited_folders": len(visited)},
            hypothesis_id="H1",
        )
        # endregion
        return collected

    def _probe_folder_sync(self, folder_id: str) -> str:
        """Lightweight health probe: verify folder is reachable (no full listing)."""
        with self._lock:
            # region agent log
            agent_log(
                location="gdrive.py:_probe_folder_sync",
                message="probe start",
                data={"folder_id_len": len(folder_id)},
                hypothesis_id="H2",
            )
            # endregion
            drive, _ = self._build_services()
            meta = (
                drive.files()
                .get(
                    fileId=folder_id,
                    fields="id,name,mimeType",
                    supportsAllDrives=True,
                )
                .execute()
            )
            name = str(meta.get("name") or folder_id)
            # region agent log
            agent_log(
                location="gdrive.py:_probe_folder_sync",
                message="probe done",
                data={"folder_name": name, "mime_type": meta.get("mimeType")},
                hypothesis_id="H2",
            )
            # endregion
            return name

    async def extract_text(self, file: DriveFile) -> str:
        return await asyncio.to_thread(self._extract_text_sync, file)

    def _extract_text_sync(self, file: DriveFile) -> str:
        drive, sheets = self._build_services()
        if file.mime_type in (MIME_DOC, MIME_SLIDES):
            data = drive.files().export(fileId=file.id, mimeType="text/plain").execute()
            return data.decode("utf-8") if isinstance(data, bytes) else str(data)
        if file.mime_type == MIME_SHEET:
            return self._extract_sheet_sync(sheets, file.id)
        if file.mime_type == MIME_PDF:
            data = drive.files().get_media(fileId=file.id).execute()
            return self._extract_pdf(data)
        return ""

    @staticmethod
    def _extract_sheet_sync(sheets: Any, spreadsheet_id: str) -> str:
        meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        blocks: list[str] = []
        for sheet in meta.get("sheets", []):
            title = sheet.get("properties", {}).get("title", "Sheet")
            values = (
                sheets.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=title)
                .execute()
            )
            rows = values.get("values", [])
            if rows:
                blocks.append(_rows_to_text(title, rows))
        return "\n\n".join(blocks)

    @staticmethod
    def _extract_pdf(data: bytes) -> str:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)

    async def health_check(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        if not self.is_configured:
            return {
                "status": "skipped",
                "error": "GDRIVE_FOLDER_ID or Google service account env vars not set",
            }
        try:
            # region agent log
            agent_log(
                location="gdrive.py:health_check",
                message="health_check start",
                data={},
                hypothesis_id="H1",
            )
            # endregion
            folder_name = await asyncio.to_thread(
                self._probe_folder_sync, self._settings.gdrive_folder_id
            )
            # region agent log
            agent_log(
                location="gdrive.py:health_check",
                message="health_check ok",
                data={"folder_name": folder_name},
                hypothesis_id="H1",
            )
            # endregion
            return {
                "status": "ok",
                "folder_name": folder_name,
                "latency_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            }
        except GoogleDriveError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - surface any Google API failure
            logger.exception("Google Drive health check failed")
            return {"status": "error", "error": str(exc)}
