from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import noteturner.services.crm_sync as cs
from noteturner.services.crm_sync import ENDPOINTS, SyncedRecord, VectorChunkSpec, _serialize_record


def test_endpoints_include_financial_flag() -> None:
    assert all(len(entry) == 4 for entry in ENDPOINTS)
    assert any(is_financial for *_, is_financial in ENDPOINTS)
    assert any(not is_financial for *_, is_financial in ENDPOINTS)


def test_serialize_record_includes_scalar_fields() -> None:
    text = _serialize_record(
        "lead",
        {"Id": 5, "FirstName": "Иван", "Status": "Новый", "Updated": "2026-01-01T00:00:00"},
    )
    assert text.startswith("CRM lead #5")
    assert "Status: Новый" in text


def test_serialize_record_skips_empty_and_nested() -> None:
    text = _serialize_record(
        "student",
        {"Id": 1, "Note": "", "Extra": {"a": 1}, "Tags": [1, 2]},
    )
    assert "Note" not in text
    assert "Extra" not in text
    assert "Tags" not in text


async def test_vectorize_records_namespaces_external_id(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_scope():
        yield object()

    replace_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(cs, "session_scope", fake_scope)
    monkeypatch.setattr(cs, "replace_file_chunks", replace_mock)

    openrouter = AsyncMock()
    openrouter.embed = AsyncMock(side_effect=lambda batch, **kw: [[0.1, 0.2] for _ in batch])

    records = [
        SyncedRecord(
            external_id="5",
            record_type="lead",
            title="CRM lead #5",
            content="lead 5",
            payload=None,
            is_financial=False,
            vector_chunks=[VectorChunkSpec(content="lead 5")],
        ),
        SyncedRecord(
            external_id="5",
            record_type="student",
            title="CRM student #5",
            content="stud 5",
            payload=None,
            is_financial=False,
            vector_chunks=[VectorChunkSpec(content="stud 5")],
        ),
        SyncedRecord(
            external_id="9",
            record_type="payment",
            title="CRM payment #9",
            content="pay 9",
            payload=None,
            is_financial=True,
            vector_chunks=[VectorChunkSpec(content="pay 9")],
        ),
    ]
    stored = await cs._vectorize_records(openrouter, records)

    assert stored == 3
    external_ids = [call.kwargs["external_id"] for call in replace_mock.await_args_list]
    assert external_ids == ["lead:5", "student:5", "payment:9"]
    payment_chunk = replace_mock.await_args_list[2].kwargs["chunks"][0]
    assert payment_chunk.is_financial is True
    assert replace_mock.await_args_list[2].kwargs["source"] == "hollihop"


def test_scope_types_include_new_entities() -> None:
    assert ("edunit", "GetEdUnits", "EdUnits", True) in ENDPOINTS
    assert ("edunit_student", "GetEdUnitStudents", "EdUnitStudents", True) in ENDPOINTS
    assert ("study_request", "GetStudyRequests", "StudyRequests", False) in ENDPOINTS
    assert ("balance", "GetBalances", "Balances", True) in ENDPOINTS


def test_scope_priority_prefers_students_then_finance_then_leads() -> None:
    assert cs.get_scope_record_types("students") == ("student",)
    assert cs.get_scope_record_types("finance") == ("payment", "balance")
    assert cs.get_scope_record_types("leads") == ("lead", "study_request")
    assert cs.get_scope_record_types("all")[:5] == (
        "student",
        "payment",
        "balance",
        "lead",
        "study_request",
    )


def test_edunit_serialization_splits_schedule_from_finance() -> None:
    record = {
        "Id": 1,
        "Name": "English A1",
        "Discipline": "English",
        "ScheduleItems": [
            {
                "BeginDate": "2026-07-04",
                "EndDate": "2026-07-04",
                "BeginTime": "10:00",
                "EndTime": "11:00",
                "Teachers": "Ivan",
                "ClassroomName": "Room 1",
            }
        ],
        "Days": [{"Date": "2026-07-04", "Minutes": 60, "Pass": False, "Description": "Lesson"}],
        "FiscalInfo": {"Price": "1000"},
    }

    synced = cs._serialize_edunit(cs.ENTITY_CONFIGS["edunit"], record, {})

    record_types = [chunk.record_type for chunk in synced.vector_chunks]
    assert synced.is_financial is False
    assert "schedule_item" in record_types
    assert "schedule_day" in record_types
    assert "group_fiscal" in record_types
    schedule_chunk = next(chunk for chunk in synced.vector_chunks if chunk.record_type == "schedule_item")
    fiscal_chunk = next(chunk for chunk in synced.vector_chunks if chunk.record_type == "group_fiscal")
    assert schedule_chunk.is_financial is False
    assert schedule_chunk.payload["begin_date"] == "2026-07-04"
    assert fiscal_chunk.is_financial is True


def test_edunit_student_serialization_splits_payers_from_schedule() -> None:
    record = {
        "EdUnitId": 1,
        "StudentClientId": 2,
        "EdUnitName": "English A1",
        "StudentName": "Alice",
        "Days": [{"Date": "2026-07-04", "Minutes": 60, "Pass": False, "Description": "Lesson"}],
        "Payers": [{"ClientId": 9, "Name": "Parent", "ContractNumber": "C-1", "PriceName": "Base"}],
    }

    synced = cs._serialize_edunit_student(cs.ENTITY_CONFIGS["edunit_student"], record, {})

    record_types = [chunk.record_type for chunk in synced.vector_chunks]
    assert synced.is_financial is False
    assert "schedule_day" in record_types
    assert "group_payer" in record_types
    payer_chunk = next(chunk for chunk in synced.vector_chunks if chunk.record_type == "group_payer")
    assert payer_chunk.is_financial is True


async def test_sync_entity_reports_page_progress(monkeypatch) -> None:
    class _FakeSession:
        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def fake_scope():
        yield _FakeSession()

    monkeypatch.setattr(cs, "session_scope", fake_scope)
    monkeypatch.setattr(cs, "get_sync_cursor", AsyncMock(return_value=None))
    monkeypatch.setattr(cs, "upsert_raw_record", AsyncMock())

    hollihop = AsyncMock()
    hollihop.call = AsyncMock(
        side_effect=[
            {
                "Leads": [
                    {"Id": 1, "Updated": "2026-01-01T00:00:00"},
                    {"Id": 2, "Updated": "2026-01-01T00:01:00"},
                ],
                "Now": "2026-01-01T00:02:00",
            },
            {"Leads": [], "Now": "2026-01-01T00:02:00"},
        ]
    )

    progress_updates = []

    async def progress(update) -> None:
        progress_updates.append(update)

    _next_cursor, _next_meta, processed, chunks_added = await cs._sync_entity(
        hollihop,
        config=cs.ENTITY_CONFIGS["lead"],
        progress=progress,
    )

    assert processed == 2
    assert chunks_added == 0
    page_update = next(update for update in progress_updates if update.stage == "page_fetched")
    assert page_update.record_type == "lead"
    assert page_update.records_processed == 2
    assert page_update.page_index == 1
    assert "загружено 2 записей типа lead" in (page_update.message or "")


async def test_run_hollihop_sync_reports_progress(monkeypatch) -> None:
    class _FakeSession:
        async def get(self, *_args, **_kwargs):
            return object()

    @asynccontextmanager
    async def fake_scope():
        yield _FakeSession()

    monkeypatch.setattr(cs, "session_scope", fake_scope)
    monkeypatch.setattr(cs, "create_sync_run", AsyncMock(return_value=SimpleNamespace(id=7)))
    monkeypatch.setattr(cs, "finish_sync_run", AsyncMock())
    monkeypatch.setattr(cs, "upsert_sync_cursor", AsyncMock())
    # _sync_entity now streams pages and vectorizes inline, returning only
    # (next_cursor, next_meta, processed, chunks_added).
    monkeypatch.setattr(
        cs,
        "_sync_entity",
        AsyncMock(
            return_value=(
                "2026-01-01T00:00:00",
                {"last_synced_at": "2026-01-01T00:00:00"},
                1,
                2,
            )
        ),
    )

    hollihop = AsyncMock()
    hollihop.is_configured = True

    openrouter = AsyncMock()
    openrouter.is_configured = True

    progress_updates = []

    async def progress(update) -> None:
        progress_updates.append(update)

    result = await cs.run_hollihop_sync(
        hollihop,
        openrouter,
        record_types=("lead",),
        progress=progress,
    )

    assert result.status == "ok"
    assert result.records_processed == 1
    assert result.chunks_processed == 2
    assert [update.stage for update in progress_updates] == [
        "entity_started",
        "entity_finished",
    ]
