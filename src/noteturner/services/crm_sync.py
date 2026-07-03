import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from noteturner.db.models import SyncRun
from noteturner.db.repositories.sync import (
    create_sync_run,
    finish_sync_run,
    get_sync_cursor,
    upsert_raw_record,
    upsert_sync_cursor,
)
from noteturner.db.repositories.vectors import ChunkInput, replace_file_chunks
from noteturner.db.session import session_scope
from noteturner.integrations.hollihop import HollihopClient, HollihopError
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError

logger = logging.getLogger(__name__)

SOURCE = "hollihop"
EMBED_BATCH = 64
DEFAULT_PAGE_SIZE = 200
MAX_RECORDS_PER_TYPE = 5000

SYNC_SCOPE_TYPES: dict[str, tuple[str, ...]] = {
    "all": ("lead", "student", "payment", "study_request", "edunit", "edunit_student", "balance"),
    "finance": ("payment", "balance"),
    "groups": ("edunit", "edunit_student"),
    "marketing": ("lead", "study_request"),
    "students": ("student", "edunit_student"),
}


@dataclass
class CrmSyncResult:
    status: str = "ok"
    records_processed: int = 0
    financial_processed: int = 0
    chunks_processed: int = 0
    per_type: dict[str, int] = field(default_factory=dict)
    error: str | None = None


@dataclass
class VectorChunkSpec:
    content: str
    payload: dict[str, Any] | None = None


@dataclass
class SyncedRecord:
    external_id: str
    record_type: str
    title: str
    content: str
    payload: dict[str, Any] | None
    is_financial: bool
    vector_chunks: list[VectorChunkSpec] = field(default_factory=list)


@dataclass(frozen=True)
class EntitySyncConfig:
    record_type: str
    function_name: str
    result_key: str
    is_financial: bool = False
    cursor_kind: str = "snapshot"  # updated | created | snapshot
    cursor_param: str | None = None
    cursor_response_field: str | None = None
    cursor_item_field: str | None = None
    page_size: int = DEFAULT_PAGE_SIZE
    id_fields: tuple[str, ...] = ("Id",)
    default_params: dict[str, Any] = field(default_factory=dict)


def get_scope_record_types(scope: str) -> tuple[str, ...]:
    return SYNC_SCOPE_TYPES.get(scope, SYNC_SCOPE_TYPES["all"])


def _current_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _full_name(*parts: Any) -> str | None:
    name = " ".join(part.strip() for part in (str(p) for p in parts if p) if part.strip())
    return name or None


def _max_iso(current: str | None, candidate: Any) -> str | None:
    text = _clean(candidate)
    if text is None:
        return current
    if current is None or text > current:
        return text
    return current


def _scalar_lines(record: dict[str, Any], fields: list[tuple[str, str]]) -> list[str]:
    lines: list[str] = []
    for key, label in fields:
        value = record.get(key)
        text = _clean(value)
        if text is not None:
            lines.append(f"{label}: {text}")
    return lines


def _format_named_items(
    title: str,
    items: list[dict[str, Any]] | None,
    fields: list[tuple[str, str]],
    *,
    limit: int = 8,
) -> list[str]:
    if not items:
        return []
    lines = [title + ":"]
    for item in items[:limit]:
        parts = [f"{label}={text}" for key, label in fields if (text := _clean(item.get(key))) is not None]
        if parts:
            lines.append("• " + ", ".join(parts))
    remaining = len(items) - limit
    if remaining > 0:
        lines.append(f"• ... ещё {remaining}")
    return lines


def _format_string_list(title: str, values: list[Any] | None, *, limit: int = 8) -> list[str]:
    cleaned = [_clean(v) for v in values or []]
    normalized = [value for value in cleaned if value is not None]
    if not normalized:
        return []
    view = ", ".join(normalized[:limit])
    if len(normalized) > limit:
        view += f", ... ещё {len(normalized) - limit}"
    return [f"{title}: {view}"]


def _format_extra_fields(title: str, items: list[dict[str, Any]] | None) -> list[str]:
    return _format_named_items(title, items, [("Name", "Name"), ("Value", "Value")], limit=10)


def _format_study_requests(items: list[dict[str, Any]] | None) -> list[str]:
    return _format_named_items(
        "StudyRequests",
        items,
        [
            ("Id", "Id"),
            ("Created", "Created"),
            ("LeadId", "LeadId"),
            ("Referrer", "Referrer"),
        ],
    )


def _format_schedule(items: list[dict[str, Any]] | None) -> list[str]:
    return _format_named_items(
        "ScheduleItems",
        items,
        [
            ("BeginDate", "BeginDate"),
            ("EndDate", "EndDate"),
            ("BeginTime", "BeginTime"),
            ("EndTime", "EndTime"),
            ("Teachers", "Teachers"),
            ("ClassroomName", "Classroom"),
        ],
    )


def _format_days(items: list[dict[str, Any]] | None) -> list[str]:
    return _format_named_items(
        "Days",
        items,
        [
            ("Date", "Date"),
            ("Minutes", "Minutes"),
            ("Pass", "Pass"),
            ("Description", "Description"),
        ],
        limit=10,
    )


def _format_payers(items: list[dict[str, Any]] | None) -> list[str]:
    return _format_named_items(
        "Payers",
        items,
        [
            ("ClientId", "ClientId"),
            ("Name", "Name"),
            ("ContractNumber", "Contract"),
            ("PriceName", "Price"),
        ],
    )


def _format_edunit_balances(items: list[dict[str, Any]] | None) -> list[str]:
    return _format_named_items(
        "EdUnitsBalances",
        items,
        [
            ("EdUnitId", "EdUnitId"),
            ("EdUnitName", "EdUnitName"),
            ("StudentName", "Student"),
            ("BalanceMoney", "BalanceMoney"),
            ("DebtMoney", "DebtMoney"),
        ],
        limit=12,
    )


def _format_json_object(title: str, value: dict[str, Any] | None) -> list[str]:
    if not value:
        return []
    lines = [title + ":"]
    for key, raw in value.items():
        text = _clean(raw)
        if text is not None:
            lines.append(f"• {key}={text}")
    return lines


def _build_external_id(config: EntitySyncConfig, record: dict[str, Any]) -> str:
    parts = [_clean(record.get(field)) for field in config.id_fields]
    if all(part is not None for part in parts):
        return ":".join(part for part in parts if part is not None)

    fallback_fields = {
        "study_request": [
            record.get("Id"),
            record.get("Created"),
            record.get("Name"),
            record.get("Phone"),
            record.get("EMail"),
        ],
        "balance": [record.get("ClientId")],
    }.get(config.record_type, [record.get("Id"), record.get("Created"), record.get("Updated")])
    digest = hashlib.sha1(
        json.dumps(fallback_fields, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"fallback:{digest}"


def _build_vector_payload(
    config: EntitySyncConfig,
    record: dict[str, Any],
    *,
    is_financial: bool,
    sync_meta: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "crm_type": config.record_type,
        "crm_source": SOURCE,
        "is_financial": is_financial,
        "sync_cursor_kind": config.cursor_kind,
    }
    if sync_meta:
        payload.update(sync_meta)

    field_map = {
        "office_or_company_id": record.get("OfficeOrCompanyId") or record.get("EdUnitOfficeOrCompanyId"),
        "client_id": record.get("ClientId"),
        "student_client_id": record.get("StudentClientId"),
        "lead_id": record.get("LeadId"),
        "edunit_id": record.get("EdUnitId") or record.get("Id") if config.record_type == "edunit" else record.get("EdUnitId"),
        "status": record.get("Status"),
        "updated_at": record.get("Updated") or record.get("Created"),
    }
    for key, value in field_map.items():
        if value is not None:
            payload[key] = value

    if config.record_type == "balance":
        payload["has_debt"] = bool(record.get("HasAnyDebtMoney") or record.get("HasAnyDebtUnits"))

    return payload


def _make_synced_record(
    config: EntitySyncConfig,
    record: dict[str, Any],
    *,
    title: str,
    summary_lines: list[str],
    detail_sections: list[tuple[str, list[str]]],
    sync_meta: dict[str, Any],
) -> SyncedRecord:
    external_id = _build_external_id(config, record)
    payload = _build_vector_payload(
        config,
        record,
        is_financial=config.is_financial,
        sync_meta=sync_meta,
    )
    main_lines = [title, *summary_lines]
    main_content = "\n".join(line for line in main_lines if line.strip())
    chunks = [VectorChunkSpec(content=main_content, payload=payload)]

    for section_name, lines in detail_sections:
        if not lines:
            continue
        section_payload = dict(payload)
        section_payload["chunk_section"] = section_name.lower()
        chunks.append(
            VectorChunkSpec(
                content="\n".join([title, f"{section_name}:", *lines]),
                payload=section_payload,
            )
        )

    return SyncedRecord(
        external_id=external_id,
        record_type=config.record_type,
        title=title,
        content=main_content,
        payload=payload,
        is_financial=config.is_financial,
        vector_chunks=chunks,
    )


def _serialize_lead(config: EntitySyncConfig, record: dict[str, Any], sync_meta: dict[str, Any]) -> SyncedRecord:
    title = (
        f"CRM lead #{record.get('Id', '?')}"
        + (f" — {_full_name(record.get('LastName'), record.get('FirstName'), record.get('MiddleName'))}" if _full_name(record.get('LastName'), record.get('FirstName'), record.get('MiddleName')) else "")
    )
    summary = _scalar_lines(
        record,
        [
            ("Status", "Status"),
            ("AddressDate", "AddressDate"),
            ("Phone", "Phone"),
            ("Mobile", "Mobile"),
            ("EMail", "Email"),
            ("AdSource", "AdSource"),
            ("LearningType", "LearningType"),
            ("Discipline", "Discipline"),
            ("Level", "Level"),
            ("StudentClientId", "StudentClientId"),
            ("Updated", "Updated"),
        ],
    )
    details = [
        ("Agents", _format_named_items("Agents", record.get("Agents"), [("FirstName", "FirstName"), ("LastName", "LastName"), ("WhoIs", "WhoIs"), ("Phone", "Phone"), ("EMail", "Email")])),
        ("Assignees", _format_named_items("Assignees", record.get("Assignees"), [("Id", "Id"), ("FullName", "FullName")])),
        ("ExtraFields", _format_extra_fields("ExtraFields", record.get("ExtraFields"))),
    ]
    return _make_synced_record(config, record, title=title, summary_lines=summary, detail_sections=details, sync_meta=sync_meta)


def _serialize_student(config: EntitySyncConfig, record: dict[str, Any], sync_meta: dict[str, Any]) -> SyncedRecord:
    title = (
        f"CRM student #{record.get('Id', '?')}"
        + (f" — {_full_name(record.get('LastName'), record.get('FirstName'), record.get('MiddleName'))}" if _full_name(record.get('LastName'), record.get('FirstName'), record.get('MiddleName')) else "")
    )
    summary = _scalar_lines(
        record,
        [
            ("Status", "Status"),
            ("AddressDate", "AddressDate"),
            ("VisitDateTime", "VisitDateTime"),
            ("Phone", "Phone"),
            ("Mobile", "Mobile"),
            ("EMail", "Email"),
            ("JobOrStudyPlace", "JobOrStudyPlace"),
            ("Position", "Position"),
            ("Updated", "Updated"),
        ],
    )
    summary.extend(_format_string_list("LearningTypes", record.get("LearningTypes")))
    summary.extend(
        _format_named_items(
            "Disciplines",
            record.get("Disciplines"),
            [("Discipline", "Discipline"), ("Level", "Level")],
        )
    )
    details = [
        ("Agents", _format_named_items("Agents", record.get("Agents"), [("FirstName", "FirstName"), ("LastName", "LastName"), ("WhoIs", "WhoIs"), ("Phone", "Phone"), ("EMail", "Email")])),
        ("Assignees", _format_named_items("Assignees", record.get("Assignees"), [("Id", "Id"), ("FullName", "FullName")])),
        ("ExtraFields", _format_extra_fields("ExtraFields", record.get("ExtraFields"))),
        ("StudyRequests", _format_study_requests(record.get("StudyRequests"))),
    ]
    return _make_synced_record(config, record, title=title, summary_lines=summary, detail_sections=details, sync_meta=sync_meta)


def _serialize_payment(config: EntitySyncConfig, record: dict[str, Any], sync_meta: dict[str, Any]) -> SyncedRecord:
    title = f"CRM payment #{record.get('Id', '?')} — {record.get('ClientName') or 'unknown client'}"
    summary = _scalar_lines(
        record,
        [
            ("Type", "Type"),
            ("State", "State"),
            ("Date", "Date"),
            ("PaidDate", "PaidDate"),
            ("RequiredPaidDate", "RequiredPaidDate"),
            ("OfficeOrCompanyName", "Office"),
            ("ClientName", "Client"),
            ("Value", "Value"),
            ("PaymentMethodName", "PaymentMethod"),
            ("Tag", "Tag"),
            ("Description", "Description"),
            ("Created", "Created"),
        ],
    )
    return _make_synced_record(config, record, title=title, summary_lines=summary, detail_sections=[], sync_meta=sync_meta)


def _serialize_study_request(
    config: EntitySyncConfig,
    record: dict[str, Any],
    sync_meta: dict[str, Any],
) -> SyncedRecord:
    title = f"CRM study_request #{record.get('Id', '?')} — {record.get('Name') or 'unknown'}"
    summary = _scalar_lines(
        record,
        [
            ("Status", "Status"),
            ("Created", "Created"),
            ("Location", "Location"),
            ("Office", "Office"),
            ("Name", "Name"),
            ("Phone", "Phone"),
            ("EMail", "Email"),
            ("Discipline", "Discipline"),
            ("Level", "Level"),
            ("Teacher", "Teacher"),
            ("BeginDate", "BeginDate"),
            ("EndDate", "EndDate"),
            ("Type", "Type"),
            ("Referrer", "Referrer"),
            ("LeadId", "LeadId"),
            ("StudentClientId", "StudentClientId"),
        ],
    )
    details = [
        ("ExtraFields", _format_extra_fields("ExtraFields", record.get("ExtraFields"))),
        ("Utm", _format_json_object("Utm", record.get("Utm"))),
    ]
    return _make_synced_record(config, record, title=title, summary_lines=summary, detail_sections=details, sync_meta=sync_meta)


def _serialize_edunit(config: EntitySyncConfig, record: dict[str, Any], sync_meta: dict[str, Any]) -> SyncedRecord:
    title = f"CRM edunit #{record.get('Id', '?')} — {record.get('Name') or 'unknown'}"
    summary = _scalar_lines(
        record,
        [
            ("Type", "Type"),
            ("OfficeOrCompanyName", "Office"),
            ("Discipline", "Discipline"),
            ("Level", "Level"),
            ("Maturity", "Maturity"),
            ("LearningType", "LearningType"),
            ("StudentsCount", "StudentsCount"),
            ("Vacancies", "Vacancies"),
            ("Description", "Description"),
        ],
    )
    summary.extend(
        _format_named_items(
            "Assignee",
            [record.get("Assignee")] if isinstance(record.get("Assignee"), dict) else None,
            [("Id", "Id"), ("FullName", "FullName")],
        )
    )
    details = [
        ("ExtraFields", _format_extra_fields("ExtraFields", record.get("ExtraFields"))),
        ("Schedule", _format_schedule(record.get("ScheduleItems"))),
        ("Days", _format_days(record.get("Days"))),
        ("FiscalInfo", _format_json_object("FiscalInfo", record.get("FiscalInfo"))),
    ]
    return _make_synced_record(config, record, title=title, summary_lines=summary, detail_sections=details, sync_meta=sync_meta)


def _serialize_edunit_student(
    config: EntitySyncConfig,
    record: dict[str, Any],
    sync_meta: dict[str, Any],
) -> SyncedRecord:
    title = (
        f"CRM edunit_student #{record.get('EdUnitId', '?')}:{record.get('StudentClientId', '?')}"
        f" — {record.get('EdUnitName') or 'unknown group'} / {record.get('StudentName') or 'unknown student'}"
    )
    summary = _scalar_lines(
        record,
        [
            ("EdUnitType", "EdUnitType"),
            ("EdUnitName", "EdUnitName"),
            ("EdUnitOfficeOrCompanyName", "Office"),
            ("EdUnitDiscipline", "Discipline"),
            ("EdUnitLevel", "Level"),
            ("StudentName", "Student"),
            ("StudentMobile", "StudentMobile"),
            ("StudentEMail", "StudentEmail"),
            ("Status", "Status"),
            ("BeginDate", "BeginDate"),
            ("EndDate", "EndDate"),
            ("StudyUnits", "StudyUnits"),
        ],
    )
    details = [
        ("StudentAgents", _format_named_items("StudentAgents", record.get("StudentAgents"), [("FirstName", "FirstName"), ("LastName", "LastName"), ("WhoIs", "WhoIs"), ("Phone", "Phone"), ("EMail", "Email")])),
        ("StudentExtraFields", _format_extra_fields("StudentExtraFields", record.get("StudentExtraFields"))),
        ("Payers", _format_payers(record.get("Payers"))),
        ("Days", _format_days(record.get("Days"))),
    ]
    return _make_synced_record(config, record, title=title, summary_lines=summary, detail_sections=details, sync_meta=sync_meta)


def _serialize_balance(config: EntitySyncConfig, record: dict[str, Any], sync_meta: dict[str, Any]) -> SyncedRecord:
    title = f"CRM balance client #{record.get('ClientId', '?')}"
    summary = _scalar_lines(
        record,
        [
            ("ClientId", "ClientId"),
            ("BalanceUnits", "BalanceUnits"),
            ("BalanceMoney", "BalanceMoney"),
            ("DebtUnits", "DebtUnits"),
            ("DebtMoney", "DebtMoney"),
            ("HasAnyDebtUnits", "HasAnyDebtUnits"),
            ("HasAnyDebtMoney", "HasAnyDebtMoney"),
            ("HasAggregateDebtUnits", "HasAggregateDebtUnits"),
            ("HasAggregateDebtMoney", "HasAggregateDebtMoney"),
        ],
    )
    study_balance = record.get("StudyBalance") if isinstance(record.get("StudyBalance"), dict) else None
    details = [
        ("StudyBalance", _format_json_object("StudyBalance", study_balance)),
        ("EdUnitsBalances", _format_edunit_balances(record.get("EdUnitsBalances"))),
    ]
    return _make_synced_record(config, record, title=title, summary_lines=summary, detail_sections=details, sync_meta=sync_meta)


SERIALIZERS = {
    "lead": _serialize_lead,
    "student": _serialize_student,
    "payment": _serialize_payment,
    "study_request": _serialize_study_request,
    "edunit": _serialize_edunit,
    "edunit_student": _serialize_edunit_student,
    "balance": _serialize_balance,
}

ENTITY_CONFIGS: dict[str, EntitySyncConfig] = {
    "lead": EntitySyncConfig(
        record_type="lead",
        function_name="GetLeads",
        result_key="Leads",
        cursor_kind="updated",
        cursor_param="lastUpdatedFrom",
        cursor_response_field="Now",
        cursor_item_field="Updated",
    ),
    "student": EntitySyncConfig(
        record_type="student",
        function_name="GetStudents",
        result_key="Students",
        cursor_kind="updated",
        cursor_param="lastUpdatedFrom",
        cursor_response_field="Now",
        cursor_item_field="Updated",
        default_params={"queryStudyRequests": True},
    ),
    "payment": EntitySyncConfig(
        record_type="payment",
        function_name="GetPayments",
        result_key="Payments",
        is_financial=True,
        cursor_kind="created",
        cursor_param="createdFrom",
        cursor_item_field="Created",
    ),
    "study_request": EntitySyncConfig(
        record_type="study_request",
        function_name="GetStudyRequests",
        result_key="StudyRequests",
        cursor_kind="created",
        cursor_param="from",
        cursor_item_field="Created",
    ),
    "edunit": EntitySyncConfig(
        record_type="edunit",
        function_name="GetEdUnits",
        result_key="EdUnits",
        is_financial=True,
        cursor_kind="updated",
        cursor_param="lastUpdatedFrom",
        cursor_response_field="Now",
        cursor_item_field="Updated",
        default_params={"queryFiscalInfo": True},
    ),
    "edunit_student": EntitySyncConfig(
        record_type="edunit_student",
        function_name="GetEdUnitStudents",
        result_key="EdUnitStudents",
        is_financial=True,
        cursor_kind="snapshot",
        id_fields=("EdUnitId", "StudentClientId"),
        default_params={"queryPayers": True},
    ),
    "balance": EntitySyncConfig(
        record_type="balance",
        function_name="GetBalances",
        result_key="Balances",
        is_financial=True,
        cursor_kind="snapshot",
        page_size=100,
        id_fields=("ClientId",),
    ),
}

ENDPOINTS: list[tuple[str, str, str, bool]] = [
    (cfg.record_type, cfg.function_name, cfg.result_key, cfg.is_financial)
    for cfg in ENTITY_CONFIGS.values()
]


def _serialize_record(record_type: str, record: dict[str, Any]) -> str:
    config = ENTITY_CONFIGS[record_type]
    serializer = SERIALIZERS[record_type]
    synced = serializer(config, record, {})
    return synced.content


def _build_request_params(
    config: EntitySyncConfig,
    cursor_value: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = dict(config.default_params)
    meta: dict[str, Any] = {}
    if config.record_type == "balance":
        balance_date = date.today().isoformat()
        params["balanceDate"] = balance_date
        meta["balance_date"] = balance_date
    elif config.cursor_param and cursor_value:
        params[config.cursor_param] = cursor_value
        meta["cursor_value"] = cursor_value
    return params, meta


def _next_cursor_value(
    config: EntitySyncConfig,
    *,
    previous: str | None,
    data: dict[str, Any],
    items: list[dict[str, Any]],
    request_meta: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    if config.cursor_kind == "snapshot":
        cursor_value = request_meta.get("balance_date") or _current_utc_iso()
        return cursor_value, dict(request_meta)

    cursor_value = previous
    if config.cursor_response_field:
        cursor_value = _max_iso(cursor_value, data.get(config.cursor_response_field))
    if config.cursor_item_field:
        for item in items:
            cursor_value = _max_iso(cursor_value, item.get(config.cursor_item_field))
    meta = {"last_synced_at": _current_utc_iso()}
    return cursor_value, meta


async def _sync_entity(
    hollihop: HollihopClient,
    *,
    config: EntitySyncConfig,
) -> tuple[list[SyncedRecord], str | None, dict[str, Any], int]:
    records: list[SyncedRecord] = []
    skip = 0
    processed = 0

    async with session_scope() as session:
        cursor = await get_sync_cursor(session, source=SOURCE, record_type=config.record_type)
        cursor_value = cursor.cursor_value if cursor is not None else None

    request_params, request_meta = _build_request_params(config, cursor_value)
    next_cursor, next_meta = cursor_value, {"last_synced_at": _current_utc_iso(), **request_meta}

    async with session_scope() as session:
        while processed < MAX_RECORDS_PER_TYPE:
            data = await hollihop.call(
                config.function_name,
                **request_params,
                take=config.page_size,
                skip=skip,
            )
            items = data.get(config.result_key) or []
            next_cursor, next_meta = _next_cursor_value(
                config,
                previous=next_cursor,
                data=data,
                items=items,
                request_meta=request_meta,
            )
            if not items:
                break

            for item in items:
                synced = SERIALIZERS[config.record_type](config, item, next_meta)
                await upsert_raw_record(
                    session,
                    source=SOURCE,
                    record_type=config.record_type,
                    external_id=synced.external_id,
                    content=synced.content,
                    payload=item,
                    is_financial=config.is_financial,
                )
                records.append(synced)
                processed += 1
                if processed >= MAX_RECORDS_PER_TYPE:
                    break

            await session.commit()
            if len(items) < config.page_size or processed >= MAX_RECORDS_PER_TYPE:
                break
            skip += config.page_size

    return records, next_cursor, next_meta, processed


async def _embed_all(openrouter: OpenRouterClient, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        vectors.extend(await openrouter.embed(batch))
    return vectors


async def _vectorize_records(openrouter: OpenRouterClient, records: list[SyncedRecord]) -> int:
    if not records:
        return 0

    texts = [chunk.content for record in records for chunk in record.vector_chunks]
    embeddings = await _embed_all(openrouter, texts)
    stored = 0
    position = 0

    async with session_scope() as session:
        for record in records:
            chunk_inputs: list[ChunkInput] = []
            for chunk_index, chunk in enumerate(record.vector_chunks):
                chunk_inputs.append(
                    ChunkInput(
                        content=chunk.content,
                        embedding=embeddings[position],
                        chunk_index=chunk_index,
                        title=record.title,
                        record_type=record.record_type,
                        is_financial=record.is_financial,
                        payload=chunk.payload,
                    )
                )
                position += 1
            await replace_file_chunks(
                session,
                source=SOURCE,
                external_id=f"{record.record_type}:{record.external_id}",
                chunks=chunk_inputs,
            )
            stored += len(chunk_inputs)
    return stored


async def run_hollihop_sync(
    hollihop: HollihopClient,
    openrouter: OpenRouterClient | None = None,
    *,
    record_types: tuple[str, ...] | None = None,
) -> CrmSyncResult:
    """Fetch Hollihop entities into raw_records and doc_chunks.

    Incremental entities use a per-type cursor stored in ``sync_cursors``.
    Snapshot-like entities (for example balances) refresh a full point-in-time
    view and store the last snapshot marker as their cursor.
    """
    result = CrmSyncResult()

    if not hollihop.is_configured:
        result.status = "error"
        result.error = "Hollihop CRM is not configured"
        return result

    selected_types = record_types or SYNC_SCOPE_TYPES["all"]
    configs = [ENTITY_CONFIGS[record_type] for record_type in selected_types if record_type in ENTITY_CONFIGS]

    async with session_scope() as session:
        sync_run = await create_sync_run(session, source=SOURCE)
        run_id = sync_run.id

    errors: list[str] = []

    try:
        for config in configs:
            records, next_cursor, next_meta, processed = await _sync_entity(
                hollihop,
                config=config,
            )
            result.per_type[config.record_type] = processed
            result.records_processed += processed
            if config.is_financial:
                result.financial_processed += processed

            if openrouter is not None and openrouter.is_configured:
                try:
                    result.chunks_processed += await _vectorize_records(openrouter, records)
                except OpenRouterError as exc:
                    logger.warning("CRM vectorization failed for %s: %s", config.record_type, exc)
                    errors.append(f"{config.record_type}: vectorization failed ({exc})")
                    continue

            async with session_scope() as session:
                await upsert_sync_cursor(
                    session,
                    source=SOURCE,
                    record_type=config.record_type,
                    cursor_kind=config.cursor_kind,
                    cursor_value=next_cursor,
                    meta=next_meta,
                    last_records_processed=processed,
                )
    except HollihopError as exc:
        result.status = "error"
        result.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the admin
        logger.exception("Hollihop sync failed")
        result.status = "error"
        result.error = str(exc)

    if errors:
        error_text = "; ".join(errors[:6])
        result.error = error_text if result.error is None else f"{result.error}; {error_text}"
        if result.status == "ok":
            result.status = "ok"

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
