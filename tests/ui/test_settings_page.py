from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QGroupBox, QMessageBox
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.app.settings import AppSettings, SettingsService, load_settings
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.ui.main_window import MainWindow
from dlsite_organizer.ui.pages.settings_page import SettingsPage


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def test_settings_page_has_live_preview_and_rejects_invalid_template(
    qapp: QApplication, tmp_path: Path
) -> None:
    service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"),
        config_path=tmp_path / "config.toml",
    )
    page = SettingsPage(service)

    initial = page.preview_label.text()
    page.template_input.setText("[{maker_name}][{series}] {title}")
    changed = page.preview_label.text()
    page.template_input.setText("{unknown_field}")

    assert initial != changed
    assert "Example Circle" in changed
    assert "模板无效" in page.template_error_label.text()
    assert not page.save_button.isEnabled()
    assert page.open_config_button is not None
    assert page.open_database_button is not None
    assert page.open_logs_button is not None
    assert "资源管理器集成" in [box.title() for box in page.findChildren(QGroupBox)]
    assert page.explorer_status_label.text() == "状态：不支持"
    assert not page.register_explorer_button.isEnabled()
    assert not page.remove_explorer_button.isEnabled()
    page.close()


def test_settings_page_save_writes_config_and_main_window_invalidates_preview(
    qapp: QApplication, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.toml"
    settings_service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"),
        config_path=config_path,
    )
    provider = FakeProvider(
        work=Work(
            workno="RJ01234567", title="Example Title", maker_name="Example Circle"
        )
    )
    lookup = LookupService(provider, NamingService())
    organizer = OrganizerService(lookup)
    window = MainWindow(
        lookup,
        CoverService(),
        organizer_service=organizer,
        settings_service=settings_service,
    )
    root = tmp_path / "works"
    root.mkdir()
    (root / "old RJ01234567").mkdir()
    window.organizer_page.set_preview(organizer.preview(root))
    assert not window.organizer_page.preview_stale

    window.settings_page.template_input.setText("{title}")
    window.settings_page.save_settings()

    assert load_settings(config_path).naming_template == "{title}"
    assert window.organizer_page.preview_stale
    assert window.settings_page.settings_status_label.text().startswith("设置已保存")
    window.close()


def test_reset_defaults_requires_confirmation_and_does_not_write(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    service = SettingsService(
        AppSettings(naming_template="{title}"),
        config_path=config_path,
    )
    page = SettingsPage(service)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    page.reset_defaults()

    assert page.template_input.text() == AppSettings().naming_template
    assert not config_path.exists()
    page.close()
