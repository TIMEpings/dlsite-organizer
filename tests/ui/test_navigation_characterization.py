import threading
import time
from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.ui.main_window import MainWindow


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
    revision_before = window.organizer_page._preview_revision

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
        assert window.organizer_page._preview_revision == revision_before
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
    delivered_results = []
    show_result = window.lookup_page._show_result
    monkeypatch.setattr(
        window.lookup_page,
        "_show_result",
        lambda value: (delivered_results.append(value), show_result(value)),
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
        assert len(delivered_results) == 1
        assert window.lookup_page.workno_value.text() == "RJ01609020"

        _select_navigation_label(window, "查询")
        assert window.pages.currentWidget() is lookup_page
        assert window.lookup_page.workno_value.text() == "RJ01609020"
    finally:
        provider.release.set()
        _wait_until(qapp, lambda: not window.lookup_page.is_busy())
        window.close()
