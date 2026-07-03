import logging
from dataclasses import dataclass, field

from noteturner.config.settings import Settings
from noteturner.db.models import SyncRun
from noteturner.db.repositories.sync import create_sync_run, finish_sync_run
from noteturner.db.repositories.vectors import ChunkInput, replace_file_chunks
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
    files_processed: int = 0
    chunks_processed: int = 0
    financial_files: int = 0
    per_type: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    hint: str | None = None


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


async def _embed_all(openrouter: OpenRouterClient, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        vectors.extend(await openrouter.embed(batch))
    return vectors


async def _sync_file(
    gdrive: GoogleDriveClient,
    openrouter: OpenRouterClient,
    file: DriveFile,
    *,
    financial_keywords: list[str],
) -> int:
    text = await gdrive.extract_text(file)
    pieces = chunk_text(text)
    if not pieces:
        return 0

    embeddings = await _embed_all(openrouter, pieces)
    financial = is_financial_name(file.name, financial_keywords)
    chunks = [
        ChunkInput(
            content=piece,
            embedding=embedding,
            chunk_index=index,
            title=file.name,
            record_type=file.record_type,
            is_financial=financial,
            payload={"file_id": file.id, "mime_type": file.mime_type},
        )
        for index, (piece, embedding) in enumerate(zip(pieces, embeddings))
    ]

    async with session_scope() as session:
        await replace_file_chunks(
            session, source=SOURCE, external_id=file.id, chunks=chunks
        )
    return len(chunks)


async def run_drive_sync(
    gdrive: GoogleDriveClient,
    openrouter: OpenRouterClient,
    settings: Settings,
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
        files = discovery.files
        if not files:
            discovery_hint = discovery.hint_when_empty
    except GoogleDriveError as exc:
        files = []
        errors.append(str(exc))

    for file in files:
        if file.record_type not in RECORD_TYPE_BY_MIME.values():
            continue
        try:
            count = await _sync_file(
                gdrive, openrouter, file, financial_keywords=financial_keywords
            )
        except Exception as exc:  # noqa: BLE001 - keep syncing remaining files
            logger.exception("Failed to sync Drive file %s (%s)", file.name, file.id)
            errors.append(f"{file.name}: {exc}")
            continue

        if count:
            result.files_processed += 1
            result.chunks_processed += count
            result.per_type[file.record_type] = result.per_type.get(file.record_type, 0) + 1
            if is_financial_name(file.name, financial_keywords):
                result.financial_files += 1

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
