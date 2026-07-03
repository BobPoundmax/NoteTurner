import asyncio
import io
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noteturner.config.settings import Settings

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
MIME_SHORTCUT = "application/vnd.google-apps.shortcut"

# Maps a Google Drive MIME type to our internal record_type label.
RECORD_TYPE_BY_MIME = {
    MIME_DOC: "doc",
    MIME_SLIDES: "slides",
    MIME_SHEET: "sheet",
    MIME_PDF: "pdf",
}

LIST_FIELDS = "nextPageToken, files(id, name, mimeType, shortcutDetails)"


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


@dataclass
class DriveRootSummary:
    root_id: str
    name: str
    mime_type: str
    items_seen: int = 0
    supported_files: int = 0
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class DriveListResult:
    files: list[DriveFile]
    roots: list[DriveRootSummary]
    skipped_by_mime: dict[str, int]

    @property
    def hint_when_empty(self) -> str | None:
        if self.files:
            return None
        lines = [
            "Файлы не найдены. Проверьте:",
            "• GDRIVE_FOLDER_ID — ID папки из URL …/folders/ID (не …/drive/project/…);",
            "• папку «Поделиться» с email сервисного аккаунта (Читатель);",
            "• типы файлов: Google Docs/Sheets/Slides и PDF (загруженные .docx/.xlsx пока не читаются).",
        ]
        for root in self.roots:
            if root.skipped and root.skip_reason:
                lines.append(f"• Корень «{root.name}»: {root.skip_reason}")
            elif root.items_seen and not root.supported_files:
                lines.append(
                    f"• В «{root.name}» найдено {root.items_seen} элемент(ов), "
                    "но ни один не поддерживается (Docs/Sheets/Slides/PDF)."
                )
            elif not root.items_seen and not root.skipped:
                lines.append(
                    f"• Папка «{root.name}» пуста для сервисного аккаунта "
                    "(нет доступа или неверный ID)."
                )
        if self.skipped_by_mime:
            top = sorted(self.skipped_by_mime.items(), key=lambda x: -x[1])[:3]
            samples = ", ".join(f"{mime} ({count})" for mime, count in top)
            lines.append(f"• Пропущено по типу: {samples}")
        return "\n".join(lines)


def parse_gdrive_root_ids(raw: str) -> list[str]:
    """Backward-compatible alias for Settings.parse_gdrive_root_ids."""
    return Settings.parse_gdrive_root_ids(raw)


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
        return self._settings.gdrive_is_configured

    def _build_services(self) -> tuple[Any, Any]:
        with self._lock:
            if self._drive is not None and self._sheets is not None:
                return self._drive, self._sheets

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
            return self._drive, self._sheets

    async def list_files(self) -> list[DriveFile]:
        result = await self.list_files_detailed()
        return result.files

    async def list_files_detailed(self) -> DriveListResult:
        if not self.is_configured:
            raise GoogleDriveError(
                "Google Drive is not configured "
                "(GDRIVE_FOLDER_ID + GOOGLE_PROJECT_ID / GOOGLE_SERVICE_ACCOUNT_EMAIL / …)"
            )
        root_ids = self._settings.gdrive_root_ids
        if not root_ids:
            raise GoogleDriveError("GDRIVE_FOLDER_ID is empty or invalid")
        return await asyncio.to_thread(self._list_files_detailed_sync, root_ids)

    def _list_files_detailed_sync(self, root_ids: list[str]) -> DriveListResult:
        with self._lock:
            return self._list_files_detailed_sync_locked(root_ids)

    def _list_files_sync_locked(self, root_ids: list[str]) -> list[DriveFile]:
        with self._lock:
            return self._list_files_detailed_sync_locked(root_ids).files

    def _list_files_detailed_sync_locked(self, root_ids: list[str]) -> DriveListResult:
        drive, _ = self._build_services()
        collected: list[DriveFile] = []
        seen_file_ids: set[str] = set()
        roots: list[DriveRootSummary] = []
        skipped_by_mime: dict[str, int] = {}
        for root_id in root_ids:
            summary = self._collect_from_root(
                drive, root_id, collected, seen_file_ids, skipped_by_mime
            )
            roots.append(summary)
        return DriveListResult(
            files=collected, roots=roots, skipped_by_mime=skipped_by_mime
        )

    def _collect_from_root(
        self,
        drive: Any,
        root_id: str,
        collected: list[DriveFile],
        seen_file_ids: set[str],
        skipped_by_mime: dict[str, int],
    ) -> DriveRootSummary:
        meta = (
            drive.files()
            .get(
                fileId=root_id,
                fields="id,name,mimeType",
                supportsAllDrives=True,
            )
            .execute()
        )
        name = str(meta.get("name") or root_id)
        mime = meta["mimeType"]
        summary = DriveRootSummary(root_id=root_id, name=name, mime_type=mime)

        if mime == MIME_FOLDER:
            self._walk_folder(drive, root_id, collected, seen_file_ids, skipped_by_mime, summary)
        elif mime in RECORD_TYPE_BY_MIME:
            file_id = meta["id"]
            summary.items_seen = 1
            if file_id not in seen_file_ids:
                seen_file_ids.add(file_id)
                collected.append(DriveFile(id=file_id, name=name, mime_type=mime))
                summary.supported_files = 1
        else:
            summary.skipped = True
            summary.skip_reason = (
                f"неподдерживаемый тип корня ({mime}). "
                "Укажите ID обычной папки (…/folders/ID), не project/space."
            )
            logger.info("Skipping unsupported Drive root %s (%s): %s", name, root_id, mime)
        return summary

    def _walk_folder(
        self,
        drive: Any,
        folder_id: str,
        collected: list[DriveFile],
        seen_file_ids: set[str],
        skipped_by_mime: dict[str, int],
        summary: DriveRootSummary | None = None,
    ) -> None:
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
                        fields=LIST_FIELDS,
                        pageSize=100,
                        pageToken=page_token,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        corpora="allDrives",
                    )
                    .execute()
                )
                for item in response.get("files", []):
                    if summary is not None:
                        summary.items_seen += 1
                    mime = item["mimeType"]
                    if mime == MIME_FOLDER:
                        stack.append(item["id"])
                    elif mime == MIME_SHORTCUT:
                        details = item.get("shortcutDetails") or {}
                        target_mime = details.get("targetMimeType")
                        target_id = details.get("targetId")
                        if target_mime == MIME_FOLDER and target_id:
                            stack.append(target_id)
                        elif target_mime in RECORD_TYPE_BY_MIME and target_id:
                            if target_id not in seen_file_ids:
                                seen_file_ids.add(target_id)
                                collected.append(
                                    DriveFile(
                                        id=target_id,
                                        name=item["name"],
                                        mime_type=target_mime,
                                    )
                                )
                                if summary is not None:
                                    summary.supported_files += 1
                        else:
                            skipped_by_mime[mime] = skipped_by_mime.get(mime, 0) + 1
                    elif mime in RECORD_TYPE_BY_MIME:
                        file_id = item["id"]
                        if file_id not in seen_file_ids:
                            seen_file_ids.add(file_id)
                            collected.append(
                                DriveFile(id=file_id, name=item["name"], mime_type=mime)
                            )
                            if summary is not None:
                                summary.supported_files += 1
                    else:
                        skipped_by_mime[mime] = skipped_by_mime.get(mime, 0) + 1
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

    def _probe_folder_sync(self, folder_id: str) -> str:
        """Lightweight health probe: verify folder is reachable (no full listing)."""
        with self._lock:
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
            return str(meta.get("name") or folder_id)

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
            root_ids = self._settings.gdrive_root_ids
            if not root_ids:
                return {"status": "error", "error": "GDRIVE_FOLDER_ID is empty or invalid"}
            names = await asyncio.gather(
                *[asyncio.to_thread(self._probe_folder_sync, root_id) for root_id in root_ids]
            )
            label = ", ".join(names) if len(names) <= 3 else f"{names[0]} +{len(names) - 1}"
            return {
                "status": "ok",
                "folder_name": label,
                "roots_count": len(root_ids),
                "latency_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            }
        except GoogleDriveError as exc:
            return {"status": "error", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - surface any Google API failure
            logger.exception("Google Drive health check failed")
            return {"status": "error", "error": str(exc)}
