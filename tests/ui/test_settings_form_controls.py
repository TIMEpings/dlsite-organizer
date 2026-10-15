"""Behavioral and rendering characterization for Settings form controls."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
)

from dlsite_organizer.app.settings import (
    AppSettings,
    SettingsService,
    StartupMode,
    load_settings,
)
from dlsite_organizer.ui.pages.settings_page import SettingsPage


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def _page(tmp_path: Path) -> SettingsPage:
    service = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3"),
        config_path=tmp_path / "config.toml",
    )
    page = SettingsPage(service)
    page.resize(1160, 800)
    page.show()
    QApplication.processEvents()
    return page


def test_settings_combos_keep_item_data_and_staged_keyboard_selection(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = _page(tmp_path)
    combo = page.startup_mode_input
    assert isinstance(combo, QComboBox)
    assert [combo.itemData(index) for index in range(combo.count())] == [
        StartupMode.FULL.value,
        StartupMode.LIGHTWEIGHT.value,
    ]
    assert combo.currentData() == AppSettings().startup_mode.value

    combo.setCurrentIndex(0)
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key.Key_Down)
    QTest.keyClick(combo, Qt.Key.Key_Enter)

    assert combo.currentData() == StartupMode.LIGHTWEIGHT.value
    assert not page._settings_service.config_path.exists()
    page.close()


def test_metadata_combo_keyboard_selection_and_disabled_rendering(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = _page(tmp_path)
    combo = page.metadata_locale_input
    assert isinstance(combo, QComboBox)
    expected = [(label, code) for label, code in page._LOCALES]
    assert [(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())] == [
        (f"{label} ({code})", code) for label, code in expected
    ]

    combo.setCurrentIndex(0)
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key.Key_Down)
    QTest.keyClick(combo, Qt.Key.Key_Enter)
    assert combo.currentData() == expected[1][1]
    assert not page._settings_service.config_path.exists()

    combo.setEnabled(False)
    image = combo.grab().toImage()
    assert isinstance(image, QImage) and not image.isNull()
    assert image.width() == combo.width()
    page.close()


def test_tag_limit_spinbox_preserves_stepper_bounds_and_unlimited_state(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = _page(tmp_path)
    spin = page.max_tags_input
    assert isinstance(spin, QSpinBox)
    assert (spin.minimum(), spin.maximum(), spin.specialValueText()) == (0, 100, "不限")
    assert spin.text() == "不限"

    spin.setFocus()
    QTest.keyClick(spin, Qt.Key.Key_Up)
    assert spin.value() == 1
    QTest.keyClick(spin, Qt.Key.Key_Down)
    assert spin.value() == 0

    spin.setValue(99)
    enabled = spin.stepEnabled()
    assert enabled & QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
    assert enabled & QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
    QTest.keyClick(spin, Qt.Key.Key_Up)
    QTest.keyClick(spin, Qt.Key.Key_Up)
    assert spin.value() == 100
    assert not spin.stepEnabled() & QAbstractSpinBox.StepEnabledFlag.StepUpEnabled

    spin.setValue(50)
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    up_rect = spin.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox,
        option,
        QStyle.SubControl.SC_SpinBoxUp,
        spin,
    )
    QTest.mouseMove(spin, up_rect.center())
    assert spin.value() == 50
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=up_rect.center())
    assert spin.value() == 51
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    down_rect = spin.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox,
        option,
        QStyle.SubControl.SC_SpinBoxDown,
        spin,
    )
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=down_rect.center())
    assert spin.value() == 50
    spin.lineEdit().selectAll()
    QTest.keyClicks(spin.lineEdit(), "62")
    QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Enter)
    assert spin.value() == 62
    image = spin.grab().toImage()
    assert not image.isNull()
    assert not page._settings_service.config_path.exists()
    page.close()


def test_combo_and_spinbox_share_settings_field_height_without_forcing_width(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = _page(tmp_path)
    page.resize(960, 640)
    page.show()
    qapp.processEvents()

    fields = (
        page.startup_mode_input,
        page.metadata_locale_input,
        page.max_tags_input,
        page.template_input,
    )
    assert (
        max(widget.height() for widget in fields) - min(widget.height() for widget in fields) <= 6
    )
    assert page.startup_mode_input.width() > page.max_tags_input.width()
    assert page.save_button.isVisible()
    scroll = page.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0
    assert list(page.template_variable_buttons) == [value for _, value in page._VARIABLES]
    page.close()


def test_combo_save_and_reset_remain_staged_until_explicit_save(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    page = _page(tmp_path)
    page.startup_mode_input.setCurrentIndex(0)
    page.metadata_locale_input.setCurrentIndex(0)
    assert not config_path.exists()

    page.save_settings()
    saved = load_settings(config_path)
    assert saved.startup_mode is StartupMode.FULL
    assert saved.provider.metadata_locale == "ja_jp"

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    page.reset_defaults()
    assert page.startup_mode_input.currentData() == AppSettings().startup_mode.value
    assert page.metadata_locale_input.currentData() == AppSettings().provider.metadata_locale
    assert load_settings(config_path) == saved

    page.save_settings()
    assert load_settings(config_path).startup_mode is AppSettings().startup_mode
    page.close()


def test_tag_limit_save_and_reset_keep_the_original_contract(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    page = _page(tmp_path)
    QTest.mouseClick(page.max_tags_input, Qt.MouseButton.LeftButton)
    QTest.keyClick(page.max_tags_input, Qt.Key.Key_Up)
    assert page.max_tags_input.value() == 1
    page.max_tags_input.setValue(42)
    page.save_settings()
    assert load_settings(config_path).max_tags == 42

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    page.reset_defaults()
    assert page.max_tags_input.value() == AppSettings().max_tags
    assert load_settings(config_path).max_tags == 42
    page.save_settings()
    assert load_settings(config_path).max_tags == AppSettings().max_tags
    page.close()
