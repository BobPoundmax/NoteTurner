import json

from noteturner.config.settings import Settings


def test_gdrive_configured_from_separate_fields() -> None:
    settings = Settings(
        gdrive_folder_id="folder123",
        google_project_id="virtuozy",
        google_service_account_email="bot@virtuozy.iam.gserviceaccount.com",
        google_private_key_id="key-id-1",
        google_private_key="-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n",
        google_client_id="12345",
    )
    assert settings.gdrive_is_configured is True
    info = settings.google_service_account_info()
    assert info["project_id"] == "virtuozy"
    assert info["client_email"] == "bot@virtuozy.iam.gserviceaccount.com"
    assert "\n" in info["private_key"]


def test_gdrive_configured_from_json_fallback() -> None:
    payload = {
        "type": "service_account",
        "project_id": "p",
        "private_key_id": "k",
        "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n",
        "client_email": "a@b.iam.gserviceaccount.com",
        "client_id": "1",
    }
    settings = Settings(
        gdrive_folder_id="folder",
        google_service_account_json=json.dumps(payload),
    )
    assert settings.gdrive_is_configured is True
    assert settings.google_service_account_info() == payload


def test_gdrive_not_configured_without_credentials() -> None:
    settings = Settings(gdrive_folder_id="folder")
    assert settings.gdrive_is_configured is False
