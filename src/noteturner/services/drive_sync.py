import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from noteturner.config.settings import Settings, get_settings
from noteturner.db.models import SyncRun
from noteturner.db.repositories.sync import create_sync_run, finish_sync_run
from noteturner.db.repositories.vectors import (
    ChunkInput,
    add_chunks,
    delete_source_chunks,
)
from noteturner.db.session import session_scope
from noteturner.integrations.gdrive import (
    RECORD_TYPE_BY_MIME,
    DriveFile,
    GoogleDriveClient,
    GoogleDriveError,
)
from noteturner.integrations.openrouter import OpenRouterClient

logger = logging.getLogger(__name__)

SOURCE = "gdrive"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150
EMBED_BATCH = 64


@dataclass
class DriveSyncResult:
    status: str = "ok"
    files_discovered: int = 0
    files_processed: int = 0
    chunks_processed: int = 0
    financial_files: int = 0
    per_type: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    hint: str | None = None


@dataclass(frozen=True)
class DriveSyncProgress:
    stage: str
    total_files: int = 0
    current_index: int = 0
    file_name: str | None = None
    file_type: str | None = None
    message: str | None = None


ProgressReporter = Callable[[DriveSyncProgress], Awaitable[None]]


def chunk_text(text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping character windows, dropping empty pieces."""
    text = text.strip()
    if not text:
        return []
    if overlap >= size:
        overlap = size // 4

    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + size, length)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = end - overlap
    return chunks


def is_financial_name(name: str, keywords: list[str]) -> bool:
    lowered = name.lower()
    return any(kw in lowered for kw in keywords)


def _truncate_name(name: str, *, limit: int = 72) -> str:
    trimmed = name.strip()
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[: limit - 1].rstrip() + "…"


def _summarize_discovered_files(files: list[DriveFile]) -> str:
    total = len(files)
    if not total:
        return "🔎 Поддерживаемых файлов для синхронизации не найдено."
    per_type: dict[str, int] = {}
    for file in files:
        per_type[file.record_type] = per_type.get(file.record_type, 0) + 1
    details = ", ".join(f"{record_type}: {count}" for record_type, count in sorted(per_type.items()))
    return f"🔎 Найдено {total} файлов для синхронизации. {details}."


async def _report_progress(
    progress: ProgressReporter | None,
    update: DriveSyncProgress,
) -> None:
    if progress is not None:
        await progress(update)


def _embed_batch_size() -> int:
    try:
        return max(1, get_settings().embedding_batch_size)
    except Exception:  # noqa: BLE001
        return EMBED_BATCH


async def _sync_file(
    gdrive: GoogleDriveClient,
    openrouter: OpenRouterClient,
    file: DriveFile,
    *,
    financial_keywords: list[str],
) -> int:
    text = await gdrive.extract_text(file)
    pieces = chunk_text(text)
    # Release the (potentially large) full-document string before embedding.
    del text
    if not pieces:
        return 0

    financial = is_financial_name(file.name, financial_keywords)
    payload = {"file_id": file.id, "mime_type": file.mime_type}
    batch_size = _embed_batch_size()

    # Delete the old chunks once, then embed + insert in small batches so we
    # never hold every embedding for a big file in memory at the same time.
    async with session_scope() as session:
        await delete_source_chunks(session, source=SOURCE, external_id=file.id)

    stored = 0
    for start in range(0, len(pieces), batch_size):
        batch = pieces[start : start + batch_size]
        embeddings = await openrouter.embed(batch)
        chunks = [
            ChunkInput(
                content=piece,
                embedding=embedding,
                chunk_index=start + offset,
                title=file.name,
                record_type=file.record_type,
                is_financial=financial,
                payload=payload,
            )
            for offset, (piece, embedding) in enumerate(zip(batch, embeddings))
        ]
        async with session_scope() as session:
            stored += await add_chunks(
                session, source=SOURCE, external_id=file.id, chunks=chunks
            )
    return stored


async def run_drive_sync(
    gdrive: GoogleDriveClient,
    openrouter: OpenRouterClient,
    settings: Settings,
    *,
    progress: ProgressReporter | None = None,
) -> DriveSyncResult:
    """Fetch Google Drive files, embed them and store chunks in doc_chunks."""
    result = DriveSyncResult()

    if not gdrive.is_configured:
        result.status = "error"
        result.error = "Google Drive is not configured"
        return result
    if not openrouter.is_configured:
        result.status = "error"
        result.error = "OpenRouter is not configured (needed for embeddings)"
        return result

    async with session_scope() as session:
        sync_run = await create_sync_run(session, source=SOURCE)
        run_id = sync_run.id

    financial_keywords = settings.financial_keyword_list
    errors: list[str] = []
    discovery_hint: str | None = None

    try:
        discovery = await gdrive.list_files_detailed()
        files = [file for file in discovery.files if file.record_type in RECORD_TYPE_BY_MIME.values()]
        result.files_discovered = len(files)
        await _report_progress(
            progress,
            DriveSyncProgress(
                stage="discovery",
                total_files=result.files_discovered,
                message=_summarize_discovered_files(files),
            ),
        )
        if not files:
            discovery_hint = discovery.hint_when_empty
    except GoogleDriveError as exc:
        files = []
        errors.append(str(exc))

    for index, file in enumerate(files, start=1):
        await _report_progress(
            progress,
            DriveSyncProgress(
                stage="processing",
                total_files=result.files_discovered,
                current_index=index,
                file_name=file.name,
                file_type=file.record_type,
                message=(
                    f"⏳ Обрабатываю файл {index}/{result.files_discovered}: "
                    f"{_truncate_name(file.name)} ({file.record_type})"
                ),
            ),
        )
        try:
            count = await _sync_file(
                gdrive, openrouter, file, financial_keywords=financial_keywords
            )
        except Exception as exc:  # noqa: BLE001 - keep syncing remaining files
            logger.exception("Failed to sync Drive file %s (%s)", file.name, file.id)
            errors.append(f"{file.name}: {exc}")
            continue

        result.files_processed += 1
        result.per_type[file.record_type] = result.per_type.get(file.record_type, 0) + 1
        if is_financial_name(file.name, financial_keywords):
            result.financial_files += 1
        if count:
            result.chunks_processed += count

    if errors:
        result.error = "; ".join(errors[:5])
        if result.chunks_processed == 0:
            result.status = "error"
    elif discovery_hint:
        result.hint = discovery_hint

    async with session_scope() as session:
        sync_run = await session.get(SyncRun, run_id)
        if sync_run is not None:
            await finish_sync_run(
                session,
                sync_run,
                status=result.status,
                records_processed=result.chunks_processed,
                error_log=result.error,
            )

    return result
