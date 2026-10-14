from pathlib import Path

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
)
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.app.settings import AppSettings, SettingsService, load_settings
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.explorer_integration import (
    ExplorerRegistration,
    ExplorerRegistrationState,
)
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.ui.main_window import MainWindow
from dlsite_organizer.ui.pages.organizer_page import PreviewStaleReason
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
    assert any(
        "右键菜单支持同时选择多个文件夹并作为一次 Quick Rename 请求处理。"
        in label.text()
        for label in page.findChildren(QLabel)
    )
    assert "{workno}" in page.template_variable_buttons
    assert "{rjcode}" not in page.template_variable_buttons
    assert "RJ01234567" in initial
    assert page.explorer_status_label.text() == "状态：不支持"
    assert not page.register_explorer_button.isEnabled()
    assert not page.remove_explorer_button.isEnabled()
    page.close()


@pytest.mark.parametrize(
    ("state", "label"),
    (
        (ExplorerRegistrationState.ABSENT, "状态：未注册"),
        (ExplorerRegistrationState.LEGACY, "状态：需要更新"),
        (ExplorerRegistrationState.STALE, "状态：需要更新"),
        (ExplorerRegistrationState.CURRENT, "状态：已注册"),
    ),
)
def test_settings_page_renders_explorer_registration_states(
    qapp: QApplication,
    tmp_path: Path,
    state: ExplorerRegistrationState,
    label: str,
) -> None:
    service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"),
        config_path=tmp_path / "config.toml",
    )
    page = SettingsPage(service)

    page._render_explorer_registration(
        ExplorerRegistration(
            state=state,
            current_executable=tmp_path / "dlsite-organizer.exe",
        )
    )

    assert page.explorer_status_label.text() == label
    page.close()


def test_settings_quick_access_group_precedes_rename_and_metadata_groups(
    qapp: QApplication, tmp_path: Path
) -> None:
    service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"),
        config_path=tmp_path / "config.toml",
    )
    page = SettingsPage(service)

    content = page.findChild(QScrollArea)
    assert isinstance(content, QScrollArea)
    content_widget = content.widget()
    assert content_widget is not None
    layout = content_widget.layout()
    assert isinstance(layout, QVBoxLayout)

    assert layout.indexOf(page.quick_access_box) < layout.indexOf(page.naming_box)
    assert page.explorer_box.parentWidget() is page.quick_access_box
    quick_layout = page.quick_access_box.layout()
    assert isinstance(quick_layout, QVBoxLayout)
    assert quick_layout.indexOf(page.explorer_box) == 0
    assert quick_layout.indexOf(page.runtime_box) == 1
    page.close()


def test_canonical_variable_button_inserts_workno_token(
    qapp: QApplication, tmp_path: Path
) -> None:
    service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"),
        config_path=tmp_path / "config.toml",
    )
    page = SettingsPage(service)

    page.template_input.clear()
    page.template_variable_buttons["{workno}"].click()

    assert page.template_input.text() == "{workno}"
    assert "RJ01234567" in page.preview_label.text()
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
    assert window.organizer_page.preview_stale_reason is PreviewStaleReason.SETTINGS_CHANGED
    assert window.settings_page.settings_status_label.text().startswith("设置已保存")
    window.close()


def test_settings_save_without_naming_change_keeps_preview_fresh(
    qapp: QApplication, tmp_path: Path
) -> None:
    settings_service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"),
        config_path=tmp_path / "config.toml",
    )
    lookup = LookupService(FakeProvider(), NamingService())
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

    try:
        window.settings_page.save_settings()

        assert not window.organizer_page.preview_stale
        assert window.organizer_page.preview_stale_reason is None
    finally:
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

    defaults = AppSettings()
    assert page.template_input.text() == defaults.naming_template
    assert page.startup_mode_input.currentData() == defaults.startup_mode.value
    assert page.metadata_locale_input.currentData() == defaults.provider.metadata_locale
    assert not config_path.exists()
    page.close()


@pytest.mark.parametrize(
    ("timeout", "ttl"),
    (
        (15.0, 24.0),
        (1.25, 10000.25),
        (1.2345678901234567, 1.2345678901234567),
        (0.00000123456789, 0.000000123456789),
        (5e-324, 5e-324),
        (120.0, 1.7976931348623157e308),
    ),
)
def test_settings_numeric_controls_display_and_collect_configured_values(
    qapp: QApplication, tmp_path: Path, timeout: float, ttl: float
) -> None:
    service = SettingsService(
        AppSettings(
            database_path=tmp_path / "metadata.sqlite3",
            provider={"timeout_seconds": timeout},
            cache={"ttl_hours": ttl},
        ),
        config_path=tmp_path / "config.toml",
    )
    page = SettingsPage(service)

    assert _numeric_control_value(page.timeout_input) == timeout
    assert _numeric_control_value(page.cache_ttl_input) == ttl
    collected = page._collect_values()
    assert collected["provider"]["timeout_seconds"] == timeout
    assert collected["cache"]["ttl_hours"] == ttl
    page.close()


def test_unedited_numeric_controls_survive_unrelated_saves_and_reload(
    qapp: QApplication, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.toml"
    service = SettingsService(
        AppSettings(
            database_path=tmp_path / "metadata.sqlite3",
            provider={"timeout_seconds": 1.25},
            cache={"ttl_hours": 10000.25},
        ),
        config_path=config_path,
    )
    page = SettingsPage(service)

    for template in ("[{workno}] {title}", "[{maker_name}] {title}"):
        page.template_input.setText(template)
        page.save_settings()
        reloaded = load_settings(config_path)
        assert reloaded.provider.timeout_seconds == 1.25
        assert reloaded.cache.ttl_hours == 10000.25
        assert service.settings.provider.timeout_seconds == 1.25
        assert service.settings.cache.ttl_hours == 10000.25
    page.close()


def test_explicit_valid_numeric_text_edits_save_exact_values(
    qapp: QApplication, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.toml"
    service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"), config_path=config_path
    )
    page = SettingsPage(service)
    _set_numeric_control_text(page.timeout_input, "2.375")
    _set_numeric_control_text(page.cache_ttl_input, "10000.375")

    page.save_settings()

    reloaded = load_settings(config_path)
    assert reloaded.provider.timeout_seconds == 2.375
    assert reloaded.cache.ttl_hours == 10000.375
    assert _numeric_control_value(page.timeout_input) == 2.375
    assert _numeric_control_value(page.cache_ttl_input) == 10000.375
    page.close()


@pytest.mark.parametrize(
    ("field", "invalid_text"),
    (
        ("timeout", "0"),
        ("timeout", "120.1"),
        ("timeout", "not a number"),
        ("ttl", "0"),
        ("ttl", "not a number"),
    ),
)
def test_invalid_numeric_edit_is_rejected_without_saving_old_value(
    qapp: QApplication, tmp_path: Path, field: str, invalid_text: str
) -> None:
    config_path = tmp_path / "config.toml"
    service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"), config_path=config_path
    )
    page = SettingsPage(service)
    input_widget = page.timeout_input if field == "timeout" else page.cache_ttl_input
    _set_numeric_control_text(input_widget, invalid_text)

    page.save_settings()

    assert not config_path.exists()
    assert service.settings.provider.timeout_seconds == 15.0
    assert page.settings_status_label.text().startswith("保存失败：")
    page.close()


def test_reset_restores_editable_numeric_defaults(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SettingsService(
        AppSettings(
            database_path=tmp_path / "metadata.sqlite3",
            provider={"timeout_seconds": 1.25},
            cache={"ttl_hours": 10000.25},
        ),
        config_path=tmp_path / "config.toml",
    )
    page = SettingsPage(service)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    page.reset_defaults()

    defaults = AppSettings()
    assert _numeric_control_value(page.timeout_input) == defaults.provider.timeout_seconds
    assert _numeric_control_value(page.cache_ttl_input) == defaults.cache.ttl_hours
    assert not service.config_path.exists()
    page.close()


def test_failed_numeric_save_preserves_disk_and_service_model(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    service = SettingsService(
        AppSettings(
            database_path=tmp_path / "metadata.sqlite3",
            provider={"timeout_seconds": 1.25},
            cache={"ttl_hours": 10000.25},
        ),
        config_path=config_path,
    )
    service.save(service.settings)
    previous_disk = config_path.read_text(encoding="utf-8")
    previous_model = service.settings
    page = SettingsPage(service)
    _set_numeric_control_text(page.timeout_input, "2.375")

    def fail_replace(*_args: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("dlsite_organizer.app.settings.os.replace", fail_replace)

    page.save_settings()

    assert config_path.read_text(encoding="utf-8") == previous_disk
    assert service.settings == previous_model
    assert page.settings_status_label.text().startswith("保存失败：")
    page.close()


def _numeric_control_value(widget: QDoubleSpinBox | QLineEdit) -> float:
    if isinstance(widget, QDoubleSpinBox):
        return widget.value()
    return float(widget.text())


def _set_numeric_control_text(widget: QDoubleSpinBox | QLineEdit, text: str) -> None:
    if isinstance(widget, QDoubleSpinBox):
        widget.lineEdit().setText(text)
    else:
        widget.setText(text)
