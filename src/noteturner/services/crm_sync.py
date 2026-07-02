import logging
from dataclasses import dataclass, field
from typing import Any

from noteturner.db.models import SyncRun
from noteturner.db.repositories.sync import create_sync_run, finish_sync_run, upsert_raw_record
from noteturner.db.session import session_scope
from noteturner.integrations.hollihop import HollihopClient, HollihopError

logger = logging.getLogger(__name__)

SOURCE = "hollihop"

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
    per_type: dict[str, int] = field(default_factory=dict)
    error: str | None = None


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
) -> int:
    processed = 0
    skip = 0
    async with session_scope() as session:
        while processed < MAX_RECORDS_PER_TYPE:
            data = await hollihop.call(function_name, take=PAGE_SIZE, skip=skip)
            items = data.get(result_key) or []
            if not items:
                break
            for record in items:
                external_id = record.get("Id")
                await upsert_raw_record(
                    session,
                    source=SOURCE,
                    record_type=record_type,
                    external_id=str(external_id) if external_id is not None else None,
                    content=_serialize_record(record_type, record),
                    payload=record,
                    is_financial=is_financial,
                )
                processed += 1
            await session.commit()
            if len(items) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
    return processed


async def run_hollihop_sync(hollihop: HollihopClient) -> CrmSyncResult:
    """Fetch whitelisted Hollihop records into raw_records, tracked by a sync_run."""
    result = CrmSyncResult()

    if not hollihop.is_configured:
        result.status = "error"
        result.error = "Hollihop CRM is not configured"
        return result

    async with session_scope() as session:
        sync_run = await create_sync_run(session, source=SOURCE)
        run_id = sync_run.id

    try:
        for record_type, function_name, result_key, is_financial in ENDPOINTS:
            count = await _sync_endpoint(
                hollihop,
                record_type=record_type,
                function_name=function_name,
                result_key=result_key,
                is_financial=is_financial,
            )
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
