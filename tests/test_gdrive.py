from noteturner.config.settings import Settings
from noteturner.integrations.gdrive import (
    MIME_DOC,
    MIME_FOLDER,
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


class _FakeListFiles:
    def __init__(self, tree: dict) -> None:
        self._tree = tree

    def get(self, fileId, fields, supportsAllDrives=True):  # noqa: N803
        return _Exec(self._tree["meta"][fileId])

    def list(
        self,
        q,
        fields,
        pageSize,  # noqa: N803
        pageToken=None,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ):
        parent = q.split("'")[1]
        return _Exec({"files": self._tree["children"].get(parent, [])})


class _FakeDriveWithTree:
    def __init__(self, tree: dict) -> None:
        self._tree = tree

    def files(self):
        return _FakeListFiles(self._tree)


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


def test_list_files_multiple_roots() -> None:
    tree = {
        "meta": {
            "folder1": {"id": "folder1", "name": "Folder", "mimeType": MIME_FOLDER},
            "doc1": {"id": "doc1", "name": "In folder", "mimeType": MIME_DOC},
            "doc2": {"id": "doc2", "name": "Standalone", "mimeType": MIME_DOC},
        },
        "children": {
            "folder1": [{"id": "doc1", "name": "In folder", "mimeType": MIME_DOC}],
        },
    }
    client = GoogleDriveClient(Settings(gdrive_folder_id="folder1,doc2"))
    client._drive = _FakeDriveWithTree(tree)
    client._sheets = _FakeSheets()
    files = client._list_files_sync_locked(["folder1", "doc2"])
    assert {f.id for f in files} == {"doc1", "doc2"}
