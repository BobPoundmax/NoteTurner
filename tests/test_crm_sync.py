from noteturner.services.crm_sync import ENDPOINTS, _serialize_record


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
