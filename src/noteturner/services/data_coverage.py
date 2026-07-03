from datetime import datetime

from noteturner.db.repositories.sync import count_raw_records_by_type, recent_sync_runs
from noteturner.db.repositories.vectors import (
    count_doc_chunks_by_record_type,
    count_doc_chunks_by_source,
)
from noteturner.db.session import session_scope


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "неизвестно"
    return value.astimezone().strftime("%d.%m %H:%M")


def _count_sum(mapping: dict[str, int], keys: tuple[str, ...]) -> int:
    return sum(mapping.get(key, 0) for key in keys)


async def build_data_coverage_message() -> str:
    async with session_scope() as session:
        raw_by_type = await count_raw_records_by_type(session, source="hollihop")
        crm_chunks = await count_doc_chunks_by_record_type(session, source="hollihop")
        drive_chunks = await count_doc_chunks_by_record_type(session, source="gdrive")
        chunks_by_source = await count_doc_chunks_by_source(session)
        runs = await recent_sync_runs(session, limit=10)

    latest_crm = next((run for run in runs if run.source == "hollihop"), None)
    latest_drive = next((run for run in runs if run.source == "gdrive"), None)

    finance_chunk_count = _count_sum(
        crm_chunks,
        ("payment", "balance", "group_payer", "group_fiscal"),
    )
    schedule_chunk_count = _count_sum(crm_chunks, ("schedule_item", "schedule_day"))

    lines = [
        "<b>Покрытие локального индекса</b>",
        "",
        "<b>CRM raw_records</b>",
        f"• lead: {raw_by_type.get('lead', 0)}",
        f"• student: {raw_by_type.get('student', 0)}",
        f"• study_request: {raw_by_type.get('study_request', 0)}",
        f"• payment: {raw_by_type.get('payment', 0)}",
        f"• balance: {raw_by_type.get('balance', 0)}",
        f"• edunit (группы): {raw_by_type.get('edunit', 0)}",
        f"• edunit_student (связки ученик-группа): {raw_by_type.get('edunit_student', 0)}",
        "",
        "<b>Векторная база CRM (doc_chunks)</b>",
        f"• лиды: {crm_chunks.get('lead', 0)}",
        f"• студенты: {crm_chunks.get('student', 0)}",
        f"• маркетинг/заявки: {crm_chunks.get('study_request', 0)}",
        f"• платежи: {crm_chunks.get('payment', 0)}",
        f"• балансы: {crm_chunks.get('balance', 0)}",
        f"• группы: {crm_chunks.get('edunit', 0)}",
        f"• связки ученик-группа: {crm_chunks.get('edunit_student', 0)}",
        f"• расписание: {schedule_chunk_count} "
        f"(schedule_item {crm_chunks.get('schedule_item', 0)}, schedule_day {crm_chunks.get('schedule_day', 0)})",
        f"• финансовые CRM-чанки: {finance_chunk_count}",
        "",
        "<b>Векторная база Google Drive</b>",
        f"• всего чанков: {chunks_by_source.get('gdrive', 0)}",
        f"• doc: {drive_chunks.get('doc', 0)}",
        f"• sheet: {drive_chunks.get('sheet', 0)}",
        f"• slides: {drive_chunks.get('slides', 0)}",
        f"• pdf: {drive_chunks.get('pdf', 0)}",
        "",
        "<b>Последние обновления</b>",
        (
            f"• CRM: {latest_crm.status}, старт {_format_timestamp(latest_crm.started_at)}, "
            f"финиш {_format_timestamp(latest_crm.finished_at)}"
            if latest_crm is not None
            else "• CRM: синхронизаций ещё не было"
        ),
        (
            f"• Drive: {latest_drive.status}, старт {_format_timestamp(latest_drive.started_at)}, "
            f"финиш {_format_timestamp(latest_drive.finished_at)}"
            if latest_drive is not None
            else "• Drive: синхронизаций ещё не было"
        ),
    ]
    return "\n".join(lines)
