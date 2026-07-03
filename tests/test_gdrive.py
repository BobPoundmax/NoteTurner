from noteturner.config.settings import Settings
from noteturner.integrations.gdrive import (
    MIME_DOC,
    MIME_SHEET,
    DriveFile,
    GoogleDriveClient,
    _rows_to_text,
)


class _Exec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeFiles:
    def export(self, fileId, mimeType):  # noqa: N803 - Google API kwarg name
        return _Exec(b"hello from doc")


class _FakeDrive:
    def files(self):
        return _FakeFiles()


class _FakeValues:
    def get(self, spreadsheetId, range):  # noqa: A002, N803 - Google API kwarg names
        return _Exec({"values": [["a", "b"], ["1", "2"]]})


class _FakeSpreadsheets:
    def get(self, spreadsheetId):  # noqa: N803
        return _Exec({"sheets": [{"properties": {"title": "Лист1"}}]})

    def values(self):
        return _FakeValues()


class _FakeSheets:
    def spreadsheets(self):
        return _FakeSpreadsheets()


def _client_with_fakes() -> GoogleDriveClient:
    client = GoogleDriveClient(Settings())
    client._drive = _FakeDrive()
    client._sheets = _FakeSheets()
    return client


def test_record_type_mapping() -> None:
    assert DriveFile(id="1", name="d", mime_type=MIME_DOC).record_type == "doc"
    assert DriveFile(id="2", name="s", mime_type=MIME_SHEET).record_type == "sheet"
    assert DriveFile(id="3", name="x", mime_type="unknown/mime").record_type == "unknown"


def test_rows_to_text() -> None:
    text = _rows_to_text("Лист1", [["a", "b"], ["1", "", "2"]])
    assert text.startswith("# Лист1")
    assert "a | b" in text
    assert "1 | 2" in text


def test_extract_doc_text() -> None:
    client = _client_with_fakes()
    file = DriveFile(id="1", name="doc", mime_type=MIME_DOC)
    assert client._extract_text_sync(file) == "hello from doc"


def test_extract_sheet_text() -> None:
    client = _client_with_fakes()
    file = DriveFile(id="2", name="sheet", mime_type=MIME_SHEET)
    text = client._extract_text_sync(file)
    assert "# Лист1" in text
    assert "a | b" in text
