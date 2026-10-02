"""Deterministic caption requests, presentation, and platform fallback coverage.

These tests deliberately do not claim to prove Windows Snap or native move loops.
"""

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.ui.main_window import MainWindow
from dlsite_organizer.ui.widgets.title_bar import TitleBar
from dlsite_organizer.ui.windows.native_chrome import (
    Hit,
    WindowsChrome,
    native_minimum,
    resize_hit,
    screen_point,
    title_hit,
)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_offscreen_title_bar_requests_and_keyboard_access(qapp):
    bar = TitleBar()
    bar.resize(1160, 40)
    bar.show()
    qapp.processEvents()
    actions = []
    bar.minimize_requested.connect(lambda: actions.append("minimize"))
    bar.maximize_requested.connect(lambda: actions.append("maximize"))
    bar.close_requested.connect(lambda: actions.append("close"))
    try:
        assert bar.height() == 40
        assert bar.title_label.text() == "DLsite Organizer"
        for button in (bar.minimize_button, bar.maximize_button, bar.close_button):
            assert button.width() == 46
            assert button.accessibleName()
            assert button.focusPolicy() == Qt.FocusPolicy.ClickFocus
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert actions == ["minimize", "maximize", "close"]
        bar.maximize_button.setFocus()
        QTest.keyClick(bar.maximize_button, Qt.Key.Key_Space)
        assert actions[-1] == "maximize"
        bar.set_maximized(True)
        assert not bar.maximize_button.restored
        assert bar.maximize_button.accessibleName() == "还原"
        assert bar.maximize_button.toolTip() == "还原"
        bar.set_maximized(False)
        assert bar.maximize_button.restored
        assert bar.maximize_button.accessibleName() == "最大化"
        assert not bar.grab().isNull()
    finally:
        bar.close()


def test_main_caption_buttons_use_existing_actions_and_close_guard(qapp, monkeypatch):
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    window.show()
    qapp.processEvents()
    try:
        window.title_bar.minimize_button.click()
        assert window.isMinimized()
        window.showNormal()
        window.title_bar.maximize_button.click()
        qapp.processEvents()
        assert window.isMaximized()
        assert not window.title_bar.maximize_button.restored
        # External/native state changes must update the same glyph, too.
        window.showNormal()
        qapp.processEvents()
        assert window.title_bar.maximize_button.restored
        window.showMaximized()
        window.title_bar.maximize_button.click()
        assert not window.isMaximized()
        monkeypatch.setattr(window, "is_busy", lambda: True)
        monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *_args: None)
        window.title_bar.close_button.click()
        assert window.isVisible()
        monkeypatch.setattr(window, "is_busy", lambda: False)
        window.title_bar.close_button.click()
        assert not window.isVisible()
        assert not window.navigation.findChildren(QLabel)
        assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
    finally:
        window.close()


def test_non_windows_fallback_never_loads_win32(qapp, monkeypatch):
    monkeypatch.setattr("dlsite_organizer.ui.windows.native_chrome.sys.platform", "linux")
    monkeypatch.setattr(
        "dlsite_organizer.ui.windows.native_chrome.ctypes.WinDLL",
        lambda *_args, **_kwargs: pytest.fail("fallback loaded a Windows DLL"),
        raising=False,
    )
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    try:
        assert WindowsChrome.create(window) is None
        assert window._native_chrome is None
        assert window.title_bar.isHidden()
        assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert window.nativeEvent(b"windows_generic_MSG", 0) == (False, 0)
    finally:
        window.close()


@pytest.mark.parametrize("ratio", [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize(
    ("logical_x", "logical_y", "expected"),
    [
        (0, 0, Hit.TOP_LEFT),
        (400, 0, Hit.TOP),
        (799, 0, Hit.TOP_RIGHT),
        (0, 300, Hit.LEFT),
        (799, 300, Hit.RIGHT),
        (0, 599, Hit.BOTTOM_LEFT),
        (400, 599, Hit.BOTTOM),
        (799, 599, Hit.BOTTOM_RIGHT),
        (400, 300, None),
    ],
)
def test_resize_edges_and_corners_at_scaled_physical_coordinates(
    ratio,
    logical_x,
    logical_y,
    expected,
):
    assert (
        resize_hit(
            round(logical_x * ratio),
            round(logical_y * ratio),
            round(800 * ratio),
            round(600 * ratio),
            round(6 * ratio),
        )
        == expected
    )


@pytest.mark.parametrize(("x", "y"), [(-1, 10), (10, -1), (800, 10), (10, 600)])
def test_resize_rejects_points_outside_window(x, y):
    assert resize_hit(x, y, 800, 600, 6) is None


@pytest.mark.parametrize(("x", "y"), [(-2400, -100), (-1, 1), (1920, 1080)])
def test_signed_native_coordinates_for_monitors_left_and_above_primary(x, y):
    packed = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    assert screen_point(packed) == (x, y)


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(1.0, (960, 826)), (1.25, (1200, 1033)), (1.5, (1440, 1239)), (2.0, (1920, 1652))],
)
def test_native_minimum_has_no_hidden_frame_and_never_rounds_below_client_minimum(ratio, expected):
    assert native_minimum(960, 826, ratio) == expected


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        (20, 20, Hit.SYSTEM_MENU),
        (500, 20, Hit.CAPTION),
        (35, 20, Hit.SYSTEM_MENU),
        (36, 20, Hit.CAPTION),
        (884, 20, Hit.CLIENT),
        (907, 20, Hit.CLIENT),
        (908, 20, Hit.MAXIMIZE),
        (953, 20, Hit.MAXIMIZE),
        (954, 20, Hit.CLIENT),
        (500, 40, Hit.CLIENT),
    ],
)
def test_caption_buttons_and_icon_are_excluded_from_drag(x, y, expected):
    assert (
        title_hit(
            QPoint(x, y),
            QRect(0, 0, 1000, 40),
            QRect(8, 4, 28, 32),
            QRect(862, 0, 46, 40),
            QRect(908, 0, 46, 40),
            QRect(954, 0, 46, 40),
        )
        == expected
    )
