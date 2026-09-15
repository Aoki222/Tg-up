from src.domain.settings_hub import SettingsHub
from src.domain.task import AfterSuccess
from src.domain.upload_settings import PreviewMode, UploadSettings
from pathlib import Path


def test_policy_for_row_reads_after_success_column() -> None:
    hub = SettingsHub.__new__(SettingsHub)
    hub._policies = {}
    hub._settings = UploadSettings(
        chat_id=1,
        observer_paths=(Path("d"),),
        page_dir=Path("p"),
        archive_dir=Path("a"),
        preview=PreviewMode.OFF,
        topic_creation_enabled=False,
        after_success=AfterSuccess.DELETE,
        concurrency=1,
        max_retries=9,
        upload_timeout_seconds=1,
        assigned_timeout_seconds=1,
        stable_timeout_seconds=1,
        watch_extensions=frozenset(),
    )
    policy = hub.policy_for_row(
        {"id": 1, "single_page": 0, "content_page": 0, "after_success": "keep", "max_retries": 3}
    )
    assert policy.after_success is AfterSuccess.KEEP
    assert policy.max_retries == 3
