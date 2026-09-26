import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication
from tests.services.test_lookup import FakeProvider

from dlsite_organizer import __version__
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.rename_history import RenameHistoryService
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.services.update_checker import UpdateCheckResult, UpdateCheckStatus
from dlsite_organizer.ui.main_window import MainWindow


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


class BlockingUpdateService:
    def __init__(self, result: UpdateCheckResult) -> None:
        self.result = result
        self.started = threading.Event()
        self.release = threading.Event()

    def check(self) -> UpdateCheckResult:
        self.started.set()
        self.release.wait(timeout=2)
        return self.result


def _wait_until(qapp: QApplication, predicate, timeout_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    qapp.processEvents()
    assert predicate(), "timed out waiting for Qt state"


def test_main_window_exposes_organizer_page_without_running_filesystem_work(
    qapp: QApplication,
) -> None:
    lookup_service = LookupService(FakeProvider(), NamingService())
    window = MainWindow(lookup_service, CoverService())

    assert [
        window.navigation_list.item(index).text()
        for index in range(window.navigation_list.count())
    ] == ["整理", "查询", "设置", "重命名历史"]
    assert window.about_button.text() == f"关于 · v{__version__}"
    assert window.pages.widget(0) is window.organizer_page
    assert window.organizer_page.table.rowCount() == 0
    assert not window.organizer_page.is_busy()
    assert window.navigation_list.currentRow() == window.page_indices["organizer"]
    assert window.about_page.version_value.text() == f"v{__version__}"
    window.close()


def test_about_footer_opens_about_destination_without_main_nav_entry(
    qapp: QApplication,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())

    window.about_button.click()

    assert window.pages.currentWidget() is window.about_page
    assert window.navigation_list.currentRow() == -1
    assert [
        window.navigation_list.item(index).text()
        for index in range(window.navigation_list.count())
    ] == ["整理", "查询", "设置", "重命名历史"]
    window.close()


def test_main_window_lightweight_settings_selects_settings_page(
    qapp: QApplication,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())

    window.show_settings_page()

    assert window.navigation_list.currentRow() == window.page_indices["settings"]
    assert window.pages.currentWidget() is window.settings_page
    window.close()


def test_unresolved_organizer_link_opens_read_only_transaction_history(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    source = root / "old RJ01609020"
    source.mkdir()
    target = root / "new RJ01609020"
    transaction = journal.create_transaction(root, ((source, target),))
    journal.mark_recovery_required(
        transaction.transaction_id,
        1,
        "success journal update was interrupted",
        transaction.created_at,
    )
    lookup_service = LookupService(
        FakeProvider(work=Work(workno="RJ01609020", title="Fixture title")),
        NamingService(),
    )
    organizer_service = OrganizerService(lookup_service)
    window = MainWindow(
        lookup_service,
        CoverService(),
        organizer_service=organizer_service,
        rename_executor=RenameExecutor(journal),
        undo_service=UndoService(journal),
        rename_history_service=RenameHistoryService(journal),
    )
    window.organizer_page.set_root_path(root)
    window.organizer_page.set_preview(organizer_service.preview(root))
    before = journal.get_transaction(transaction.transaction_id)
    before_entries = sorted((path.name, path.is_dir()) for path in root.iterdir())

    assert window.organizer_page.selected_ready_count() == 1
    assert not window.organizer_page.execute_button.isEnabled()
    assert not window.organizer_page.undo_button.isEnabled()
    assert not window.organizer_page.view_transaction_button.isHidden()
    window.organizer_page.view_transaction_button.click()

    assert window.pages.currentWidget() is window.rename_history_page
    expected_heading = f"事务详情 · {transaction.transaction_id}"
    assert window.rename_history_page.detail_heading.text() == expected_heading
    recovery_text = window.rename_history_page.recovery_label.text()
    assert "success journal update was interrupted" in recovery_text
    assert journal.get_transaction(transaction.transaction_id) == before
    assert sorted((path.name, path.is_dir()) for path in root.iterdir()) == before_entries
    assert source.is_dir()
    assert not target.exists()
    window.close()
    database.dispose()


def test_organizer_minimum_height_keeps_table_and_footer_disjoint(
    qapp: QApplication,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    page = window.organizer_page

    try:
        window.show()
        qapp.processEvents()
        assert window.minimumHeight() >= page.minimumSizeHint().height()

        for width, height in (
            (window.minimumWidth(), window.minimumHeight()),
            (1080, 900),
        ):
            window.resize(width, height)
            qapp.processEvents()

            table_rect = page.table.geometry()
            assert not table_rect.intersects(page.footer.geometry())
            assert page.table.isVisible()
            assert page.table.viewport().isVisible()
            assert page.table.viewport().height() > 0
            assert page.footer.isVisible()

            footer_widgets = (
                page.summary_label,
                page.recent_transaction_label,
                page.undo_button,
            )
            for widget in footer_widgets:
                widget_rect = QRect(widget.mapTo(page, QPoint(0, 0)), widget.size())
                assert not table_rect.intersects(widget_rect)
                assert widget_rect.bottom() <= page.rect().bottom()
    finally:
        window.close()


def test_main_window_close_guard_includes_about_update_worker(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BlockingUpdateService(
        UpdateCheckResult(
            status=UpdateCheckStatus.UP_TO_DATE,
            current_version=__version__,
        )
    )
    window = MainWindow(
        LookupService(FakeProvider(), NamingService()),
        CoverService(),
        update_check_service=service,
    )
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *_args: None)
    window.show()

    try:
        window.show_about_page()
        window.about_page.check_update_button.click()
        _wait_until(qapp, service.started.is_set)

        close_while_busy = QCloseEvent()
        window.closeEvent(close_while_busy)
        assert not close_while_busy.isAccepted()
        assert window.is_busy()

        service.release.set()
        _wait_until(qapp, lambda: not window.about_page.is_busy())
        close_after_finish = QCloseEvent()
        window.closeEvent(close_after_finish)
        assert close_after_finish.isAccepted()
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not window.about_page.is_busy())
        window.close()


def test_about_update_survives_navigation_away_and_back(qapp: QApplication) -> None:
    service = BlockingUpdateService(
        UpdateCheckResult(
            status=UpdateCheckStatus.UP_TO_DATE,
            current_version=__version__,
        )
    )
    window = MainWindow(
        LookupService(FakeProvider(), NamingService()),
        CoverService(),
        update_check_service=service,
    )
    window.show()

    try:
        window.show_about_page()
        window.about_page.check_update_button.click()
        _wait_until(qapp, service.started.is_set)
        window.navigation_list.setCurrentRow(window.page_indices["organizer"])
        service.release.set()
        _wait_until(qapp, lambda: not window.about_page.is_busy())
        window.show_about_page()
        assert window.about_page.update_status_label.text() == "已是最新版本"
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not window.about_page.is_busy())
        window.close()
