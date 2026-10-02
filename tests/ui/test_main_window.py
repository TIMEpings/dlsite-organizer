import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
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
from dlsite_organizer.ui.main_window import MainPage, MainWindow
from dlsite_organizer.ui.widgets.navigation import NavigationItemDelegate


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


def _select_navigation_label(window: MainWindow, label: str) -> None:
    matches = [
        window.navigation_list.item(index)
        for index in range(window.navigation_list.count())
        if window.navigation_list.item(index).text() == label
    ]
    assert len(matches) == 1
    window.navigation_list.setCurrentItem(matches[0])


def test_main_window_exposes_organizer_page_without_running_filesystem_work(
    qapp: QApplication,
) -> None:
    lookup_service = LookupService(FakeProvider(), NamingService())
    window = MainWindow(lookup_service, CoverService())

    assert [
        window.navigation_list.item(index).text()
        for index in range(window.navigation_list.count())
    ] == ["整理", "查询", "重命名历史"]
    assert window.about_button.text() == f"关于 · v{__version__}"
    assert window.pages.currentWidget() is window.organizer_page
    assert window.organizer_page.preview_model.rowCount() == 0
    assert not window.organizer_page.is_busy()
    assert window.current_page is MainPage.ORGANIZER
    assert window.about_page.version_value.text() == f"v{__version__}"
    window.close()


def test_about_footer_opens_about_destination_without_main_nav_entry(
    qapp: QApplication,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())

    window.about_button.click()

    assert window.pages.currentWidget() is window.about_page
    assert window.current_page is MainPage.ABOUT
    assert window.about_button.isChecked()
    assert window.navigation_list.currentRow() == -1
    assert [
        window.navigation_list.item(index).text()
        for index in range(window.navigation_list.count())
    ] == ["整理", "查询", "重命名历史"]
    window.close()


def test_main_window_lightweight_settings_selects_settings_page(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    refresh_calls = []
    monkeypatch.setattr(
        window.settings_page,
        "refresh_explorer_registration",
        lambda: refresh_calls.append(True),
    )

    window.show_settings_page()

    assert window.navigation_list.currentRow() == -1
    assert window.current_page is MainPage.SETTINGS
    assert window.settings_button.isChecked()
    assert window.pages.currentWidget() is window.settings_page
    assert refresh_calls == [True]
    window.close()


def test_settings_footer_routes_without_refreshing_external_state(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    refresh_calls = []
    monkeypatch.setattr(
        window.settings_page,
        "refresh_explorer_registration",
        lambda: refresh_calls.append(True),
    )

    window.settings_button.click()

    assert window.current_page is MainPage.SETTINGS
    assert window.pages.currentWidget() is window.settings_page
    assert window.settings_button.isChecked()
    assert refresh_calls == []
    window.close()


def test_main_page_identity_routes_each_public_page_and_startup_mounts_each_once(
    qapp: QApplication,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    expected_pages = {
        MainPage.ORGANIZER: window.organizer_page,
        MainPage.LOOKUP: window.lookup_page,
        MainPage.HISTORY: window.rename_history_page,
        MainPage.SETTINGS: window.settings_page,
        MainPage.ABOUT: window.about_page,
    }

    try:
        assert window.current_page is MainPage.ORGANIZER
        assert window.navigation_list.focusPolicy() != Qt.FocusPolicy.NoFocus
        assert isinstance(window.navigation_list.itemDelegate(), NavigationItemDelegate)
        assert len(window._pages_by_identity) == len(expected_pages) == 5
        assert window.pages.count() == len(expected_pages)
        mounted_pages = [window.pages.widget(index) for index in range(window.pages.count())]
        assert len({id(page) for page in mounted_pages}) == 5
        assert set(mounted_pages) == set(expected_pages.values())

        for identity, page in expected_pages.items():
            window.navigate_to(identity)
            assert window.current_page is identity
            assert window._pages_by_identity[identity] is page
            assert window.pages.currentWidget() is page
            if identity in {MainPage.ORGANIZER, MainPage.LOOKUP, MainPage.HISTORY}:
                current_item = window.navigation_list.currentItem()
                assert current_item is not None
                assert current_item.data(Qt.ItemDataRole.UserRole) == identity.value
                assert not window.settings_button.isChecked()
                assert not window.about_button.isChecked()
            elif identity is MainPage.SETTINGS:
                assert window.navigation_list.currentRow() == -1
                assert window.settings_button.isChecked()
                assert not window.about_button.isChecked()
            else:
                assert window.navigation_list.currentRow() == -1
                assert not window.settings_button.isChecked()
                assert window.about_button.isChecked()
    finally:
        window.close()


def test_sidebar_arrow_keys_keep_keyboard_navigation_functional(
    qapp: QApplication,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    window.show()
    qapp.processEvents()

    try:
        window.navigation_list.setFocus()
        QTest.keyClick(window.navigation_list, Qt.Key.Key_Down)
        assert window.navigation_list.hasFocus()
        assert window.current_page is MainPage.LOOKUP
        assert window.pages.currentWidget() is window.lookup_page

        QTest.keyClick(window.navigation_list, Qt.Key.Key_Down)
        assert window.current_page is MainPage.HISTORY
        assert window.pages.currentWidget() is window.rename_history_page
    finally:
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
    assert not window.organizer_page.status_banner.isHidden()
    assert window.organizer_page.status_banner.property("state") == "blocked"
    assert window.organizer_page.status_banner.title_label.text() == "存在未解决的重命名事务"
    window.organizer_page.view_transaction_button.click()

    assert window.pages.currentWidget() is window.rename_history_page
    expected_heading = "事务详情"
    assert window.rename_history_page.detail_heading.text() == expected_heading
    assert window.rename_history_page.transaction_id_value.text() == transaction.transaction_id
    recovery_text = window.rename_history_page.recovery_label.text()
    assert "success journal update was interrupted" in recovery_text
    assert journal.get_transaction(transaction.transaction_id) == before
    assert sorted((path.name, path.is_dir()) for path in root.iterdir()) == before_entries
    assert source.is_dir()
    assert not target.exists()
    window.close()
    database.dispose()


def test_organizer_minimum_height_keeps_table_and_footer_disjoint(
    qapp: QApplication, tmp_path: Path,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    page = window.organizer_page
    from tests.ui.test_organizer_preview_model import synthetic_preview

    page.set_preview(synthetic_preview(tmp_path, 8))

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
        _select_navigation_label(window, "整理")
        service.release.set()
        _wait_until(qapp, lambda: not window.about_page.is_busy())
        window.show_about_page()
        assert window.about_page.update_status_label.text() == "已是最新版本"
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not window.about_page.is_busy())
        window.close()
