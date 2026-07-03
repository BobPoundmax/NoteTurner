from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import noteturner.services.crm_sync as cs
from noteturner.services.crm_sync import ENDPOINTS, SyncedRecord, _serialize_record


def test_endpoints_include_financial_flag() -> None:
    assert all(len(entry) == 4 for entry in ENDPOINTS)
    assert any(is_financial for *_, is_financial in ENDPOINTS)
    assert any(not is_financial for *_, is_financial in ENDPOINTS)


def test_serialize_record_includes_scalar_fields() -> None:
    text = _serialize_record(
        "lead",
        {"Id": 5, "FirstName": "Иван", "StatusId": 2},
    )
    assert text.startswith("Lead #5")
    assert "FirstName: Иван" in text
    assert "StatusId: 2" in text


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
        SyncedRecord(external_id="5", record_type="lead", content="lead 5", is_financial=False),
        SyncedRecord(external_id="5", record_type="student", content="stud 5", is_financial=False),
        SyncedRecord(external_id="9", record_type="payment", content="pay 9", is_financial=True),
    ]
    stored = await cs._vectorize_records(openrouter, records)

    assert stored == 3
    external_ids = [call.kwargs["external_id"] for call in replace_mock.await_args_list]
    assert external_ids == ["lead:5", "student:5", "payment:9"]
    payment_chunk = replace_mock.await_args_list[2].kwargs["chunks"][0]
    assert payment_chunk.is_financial is True
    assert replace_mock.await_args_list[2].kwargs["source"] == "hollihop"
