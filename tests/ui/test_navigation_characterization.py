import threading
import time
from itertools import pairwise
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.ui.main_window import MainWindow
from dlsite_organizer.ui.pages.lookup_page import LookupPage


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def _wait_until(qapp: QApplication, predicate, timeout_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    qapp.processEvents()
    assert predicate(), "timed out waiting for Qt state"


def _select_navigation_label(window: MainWindow, label: str) -> None:
    matches = [
        window.navigation_list.item(index)
        for index in range(window.navigation_list.count())
        if window.navigation_list.item(index).text() == label
    ]
    assert len(matches) == 1
    window.navigation_list.setCurrentItem(matches[0])


class BlockingLookupProvider:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []

    def fetch_work(self, workno: str) -> Work:
        self.calls.append(workno)
        self.started.set()
        self.release.wait(timeout=3)
        return Work(workno=workno, title="Navigation fixture")


@pytest.mark.parametrize(
    ("force_refresh", "expected_status"),
    [(False, "正在查询…"), (True, "正在强制刷新…")],
)
def test_lookup_busy_guard_and_loading_feedback(
    qapp: QApplication,
    force_refresh: bool,
    expected_status: str,
) -> None:
    provider = BlockingLookupProvider()
    page = LookupPage(LookupService(provider, NamingService()), CoverService())
    page.code_input.setText("RJ01609020")

    try:
        (page.force_refresh if force_refresh else page.start_lookup)()
        assert provider.started.wait(timeout=2)
        active_thread = page._thread
        assert page.status_label.text() == expected_status

        page.start_lookup()
        page.force_refresh()

        assert page._thread is active_thread
        assert provider.calls == ["RJ01609020"]
        assert not page.code_input.isEnabled()
        assert not page.query_button.isEnabled()
        assert not page.refresh_button.isEnabled()
    finally:
        provider.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


def test_lookup_restores_input_focus_only_when_focus_was_not_moved(
    qapp: QApplication,
) -> None:
    provider = BlockingLookupProvider()
    page = LookupPage(LookupService(provider, NamingService()), CoverService())
    page.code_input.setText("RJ01609020")
    page.show()
    page.code_input.setFocus()
    qapp.processEvents()

    try:
        page.start_lookup()
        assert provider.started.wait(timeout=2)
        provider.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert qapp.focusWidget() is page.code_input

        provider.release.clear()
        provider.started.clear()
        page.start_lookup()
        assert provider.started.wait(timeout=2)
        other_control = QPushButton("other", page)
        other_control.show()
        other_control.setFocus()
        qapp.processEvents()
        assert qapp.focusWidget() is other_control
        time.sleep(0.06)
        qapp.processEvents()

        provider.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert qapp.focusWidget() is other_control
    finally:
        provider.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


def test_lookup_history_walks_branches_and_only_records_success(
    qapp: QApplication,
) -> None:
    a, b, c, d = ("RJ00000001", "RJ00000002", "RJ00000003", "RJ00000004")
    provider = BlockingLookupProvider()
    provider.release.set()
    page = LookupPage(LookupService(provider, NamingService()), CoverService())
    page.resize(480, 520)
    page.show()

    def query(code: str, *, refresh: bool = False) -> None:
        page.code_input.setText(code)
        (page.force_refresh if refresh else page.start_lookup)()
        _wait_until(qapp, lambda: not page.is_busy())

    def check(code: str, *, back: bool, forward: bool) -> None:
        assert page.code_input.text() == page.workno_value.text() == code
        assert code in page.name_output.text()
        assert page.back_button.isEnabled() == back
        assert page.forward_button.isEnabled() == forward

    try:
        assert not page.back_button.isEnabled() and not page.forward_button.isEnabled()
        for code in (a, b, c):
            query(code)
        check(c, back=True, forward=False)
        page.code_input.setFocus()
        qapp.processEvents()
        QTest.keyClick(page.code_input, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
        check(b, back=True, forward=True)
        page.back_button.click()
        check(a, back=False, forward=True)
        page.code_input.setFocus()
        QTest.keyClick(page.code_input, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
        check(b, back=True, forward=True)
        page.forward_button.click()
        check(c, back=True, forward=False)
        assert provider.calls == [a, b, c]

        page.back_button.click()
        query("invalid")
        check(b, back=True, forward=True)
        assert page.status_label.property("state") == "error"
        query(b)
        query(b, refresh=True)
        check(b, back=True, forward=True)
        query(d)
        check(d, back=True, forward=False)
        page.back_button.click()
        check(b, back=True, forward=True)
        page.back_button.click()
        check(a, back=False, forward=True)
        assert provider.calls == [a, b, c, b, b, d]

        controls = (
            page.back_button, page.forward_button, page.code_input,
            page.query_button, page.refresh_button,
        )
        assert all(left.geometry().right() < right.geometry().left()
                   for left, right in pairwise(controls))
        assert page._scroll_area.horizontalScrollBar().maximum() == 0
    finally:
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


def test_lookup_history_rejects_late_metadata_when_navigating(qapp: QApplication) -> None:
    a, b, c, d = ("RJ00000001", "RJ00000002", "RJ00000003", "RJ00000004")
    provider = BlockingLookupProvider()
    provider.release.set()
    page = LookupPage(LookupService(provider, NamingService()), CoverService())
    page.show()
    try:
        for code in (a, b):
            page.code_input.setText(code)
            page.start_lookup()
            _wait_until(qapp, lambda: not page.is_busy())
        provider.started.clear()
        provider.release.clear()
        page.code_input.setText(c)
        page.start_lookup()
        assert provider.started.wait(timeout=2)
        thread = page._thread
        worker = page._worker
        assert worker is not None and hasattr(worker, "result_ready")
        page.back_button.click()
        assert page.is_busy() and page.code_input.text() == page.workno_value.text() == a
        page.forward_button.click()
        assert page.code_input.text() == page.workno_value.text() == b
        page._open_related_work(d)
        page._open_related_work(d)
        assert page._thread is thread
        stale_projections: list[str] = []
        worker.result_ready.connect(
            lambda _: stale_projections.append(page.workno_value.text())
        )
        provider.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert stale_projections == [b]
        assert page.code_input.text() == page.workno_value.text() == d
        assert provider.calls == [a, b, c, d]
        page.back_button.click()
        assert page.workno_value.text() == b
    finally:
        provider.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


def test_public_destinations_round_trip_and_reuse_the_same_page_instances(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    provider = FakeProvider()
    lookup_service = LookupService(provider, NamingService())
    window = MainWindow(lookup_service, CoverService())
    root = tmp_path / "library"
    root.mkdir()
    preview = window.organizer_page._organizer_service.preview(root)
    window.organizer_page.set_preview(preview)

    pages = (
        window.organizer_page,
        window.lookup_page,
        window.rename_history_page,
        window.settings_page,
        window.about_page,
    )
    page_ids = tuple(id(page) for page in pages)
    preview_before = window.organizer_page.preview
    revision_before = window.organizer_page.state.preview_revision

    try:
        assert window.pages.currentWidget() is window.organizer_page
        _select_navigation_label(window, "查询")
        assert window.pages.currentWidget() is window.lookup_page
        _select_navigation_label(window, "重命名历史")
        assert window.pages.currentWidget() is window.rename_history_page
        window.show_settings_page()
        assert window.pages.currentWidget() is window.settings_page
        window.about_button.click()
        assert window.pages.currentWidget() is window.about_page
        _select_navigation_label(window, "整理")
        assert window.pages.currentWidget() is window.organizer_page

        assert tuple(id(page) for page in pages) == page_ids
        assert window.organizer_page.preview is preview_before
        assert window.organizer_page.state.preview_revision == revision_before
        assert provider.received_workno is None
        assert not window.organizer_page.is_busy()
        assert not window.lookup_page.is_busy()
    finally:
        window.close()


def test_lookup_worker_continues_while_another_page_is_selected(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = BlockingLookupProvider()
    window = MainWindow(
        LookupService(provider, NamingService()),
        CoverService(),
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information",
        lambda *_args: None,
    )
    window.lightweight_requested.connect(lambda: pytest.fail("busy work switched mode"))
    window.lookup_page.code_input.setText("RJ01609020")
    lookup_page = window.lookup_page

    try:
        window.lookup_page.start_lookup()
        assert provider.started.wait(timeout=2)
        active_thread = window.lookup_page._thread

        _select_navigation_label(window, "整理")
        assert window.pages.currentWidget() is window.organizer_page
        assert window.lookup_page is lookup_page
        assert window.lookup_page._thread is active_thread
        assert window.lookup_page.is_busy()
        assert window.is_busy()

        window.request_lightweight_mode()
        close_event = QCloseEvent()
        window.closeEvent(close_event)
        assert not close_event.isAccepted()

        provider.release.set()
        _wait_until(qapp, lambda: not window.lookup_page.is_busy())
        assert provider.calls == ["RJ01609020"]
        assert window.lookup_page.workno_value.text() == "RJ01609020"

        _select_navigation_label(window, "查询")
        assert window.pages.currentWidget() is lookup_page
        assert window.lookup_page.workno_value.text() == "RJ01609020"
    finally:
        provider.release.set()
        _wait_until(qapp, lambda: not window.lookup_page.is_busy())
        window.close()
