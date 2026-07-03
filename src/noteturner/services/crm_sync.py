import logging
from dataclasses import dataclass, field
from typing import Any

from noteturner.db.models import SyncRun
from noteturner.db.repositories.sync import create_sync_run, finish_sync_run, upsert_raw_record
from noteturner.db.repositories.vectors import ChunkInput, replace_file_chunks
from noteturner.db.session import session_scope
from noteturner.integrations.hollihop import HollihopClient, HollihopError
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)

SOURCE = "hollihop"
EMBED_BATCH = 64

# Hollihop endpoints to sync. Financial records (is_financial=True) are stored
# but flagged so they can be restricted to admins at retrieval time.
# NOTE: verify financial result_key values against the Hollihop API 2.0 docs.
ENDPOINTS: list[tuple[str, str, str, bool]] = [
    # (record_type, api_function, result_key, is_financial)
    ("lead", "GetLeads", "Leads", False),
    ("student", "GetStudents", "Students", False),
    ("payment", "GetPayments", "Payments", True),
]

PAGE_SIZE = 100
MAX_RECORDS_PER_TYPE = 500


@dataclass
class CrmSyncResult:
    status: str = "ok"
    records_processed: int = 0
    financial_processed: int = 0
    chunks_processed: int = 0
    per_type: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass
class SyncedRecord:
    """A raw record staged for vectorization into the shared doc_chunks store."""

    external_id: str
    record_type: str
    content: str
    is_financial: bool


def _serialize_record(record_type: str, record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in record.items():
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            parts.append(f"{key}: {value}")
    header = f"{record_type.capitalize()} #{record.get('Id', '?')}"
    return f"{header}\n" + "\n".join(parts)


async def _sync_endpoint(
    hollihop: HollihopClient,
    *,
    record_type: str,
    function_name: str,
    result_key: str,
    is_financial: bool,
) -> list[SyncedRecord]:
    synced: list[SyncedRecord] = []
    skip = 0
    async with session_scope() as session:
        while len(synced) < MAX_RECORDS_PER_TYPE:
            data = await hollihop.call(function_name, take=PAGE_SIZE, skip=skip)
            items = data.get(result_key) or []
            if not items:
                break
            for record in items:
                external_id = record.get("Id")
                content = _serialize_record(record_type, record)
                await upsert_raw_record(
                    session,
                    source=SOURCE,
                    record_type=record_type,
                    external_id=str(external_id) if external_id is not None else None,
                    content=content,
                    payload=record,
                    is_financial=is_financial,
                )
                if external_id is not None:
                    synced.append(
                        SyncedRecord(
                            external_id=str(external_id),
                            record_type=record_type,
                            content=content,
                            is_financial=is_financial,
                        )
                    )
            await session.commit()
            if len(items) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
    return synced


async def _embed_all(openrouter: OpenRouterClient, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        vectors.extend(await openrouter.embed(batch))
    return vectors


async def _vectorize_records(
    openrouter: OpenRouterClient, records: list[SyncedRecord]
) -> int:
    """Embed synced CRM records into the shared doc_chunks store (one chunk each).

    External ids are namespaced by record type so that, for example, lead #5 and
    student #5 do not overwrite each other's chunk (they share ``Id`` in Hollihop).
    """
    if not records:
        return 0

    embeddings = await _embed_all(openrouter, [r.content for r in records])
    stored = 0
    async with session_scope() as session:
        for record, embedding in zip(records, embeddings):
            await replace_file_chunks(
                session,
                source=SOURCE,
                external_id=f"{record.record_type}:{record.external_id}",
                chunks=[
                    ChunkInput(
                        content=record.content,
                        embedding=embedding,
                        chunk_index=0,
                        title=f"CRM {record.record_type} #{record.external_id}",
                        record_type=record.record_type,
                        is_financial=record.is_financial,
                        payload={"crm_type": record.record_type},
                    )
                ],
            )
            stored += 1
    return stored


async def run_hollihop_sync(
    hollihop: HollihopClient, openrouter: OpenRouterClient | None = None
) -> CrmSyncResult:
    """Fetch whitelisted Hollihop records into raw_records, tracked by a sync_run.

    When ``openrouter`` is configured, synced records are also embedded into the
    shared ``doc_chunks`` store so the assistant can retrieve CRM data (including
    financial records, restricted to admins) alongside Google Drive documents.
    """
    result = CrmSyncResult()

    if not hollihop.is_configured:
        result.status = "error"
        result.error = "Hollihop CRM is not configured"
        return result

    async with session_scope() as session:
        sync_run = await create_sync_run(session, source=SOURCE)
        run_id = sync_run.id

    synced: list[SyncedRecord] = []
    try:
        for record_type, function_name, result_key, is_financial in ENDPOINTS:
            records = await _sync_endpoint(
                hollihop,
                record_type=record_type,
                function_name=function_name,
                result_key=result_key,
                is_financial=is_financial,
            )
            synced.extend(records)
            count = len(records)
            result.per_type[record_type] = count
            result.records_processed += count
            if is_financial:
                result.financial_processed += count
    except HollihopError as exc:
        result.status = "error"
        result.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the admin
        logger.exception("Hollihop sync failed")
        result.status = "error"
        result.error = str(exc)

    if result.status == "ok" and openrouter is not None and openrouter.is_configured:
        try:
            result.chunks_processed = await _vectorize_records(openrouter, synced)
        except OpenRouterError as exc:
            logger.warning("CRM vectorization failed: %s", exc)
            result.error = f"Записи сохранены, но векторизация не удалась: {exc}"

    async with session_scope() as session:
        sync_run = await session.get(SyncRun, run_id)
        if sync_run is not None:
            await finish_sync_run(
                session,
                sync_run,
                status=result.status,
                records_processed=result.records_processed,
                error_log=result.error,
            )

    return result
