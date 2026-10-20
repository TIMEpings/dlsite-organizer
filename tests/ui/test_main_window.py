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
from dlsite_organizer.app.quick_action_controller import QuickActionFinished, QuickActionRequest
from dlsite_organizer.app.runtime import RuntimeSignals
from dlsite_organizer.domain.organizer import ScanResult
from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameExecutionResult,
    RenameOperation,
    TransactionStatus,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import (
    OrganizerPreview,
    OrganizerService,
    WorkLookup,
    WorkLookupStatus,
)
from dlsite_organizer.services.quick_rename import QuickRenameResult, QuickRenameStatus
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


def _quick_completion(result=None, error=None, request_id=1) -> QuickActionFinished:
    return QuickActionFinished(
        QuickActionRequest(request_id, Path(f"X{request_id}"), f"x{request_id}"), result, error,
    )


@pytest.mark.parametrize("page", list(MainPage))
def test_quick_failure_is_accessible_across_full_pages_without_focus_change(
    qapp: QApplication, page: MainPage, tmp_path: Path,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    if page is MainPage.ORGANIZER:
        from tests.ui.test_organizer_preview_model import synthetic_preview

        window.organizer_page.set_preview(synthetic_preview(tmp_path, 8))
    window.show()
    window.navigate_to(page)
    window.settings_button.setFocus()
    qapp.processEvents()
    focus = qapp.focusWidget()
    reason = "controlled lookup failure " + "long reason " * 300
    preview = OrganizerPreview(
        Path("X"), ScanResult(Path("X"), (), ()),
        (WorkLookup("RJ123456", WorkLookupStatus.FAILED, error="specific lookup reason"),), (),
    )
    result = QuickRenameResult(QuickRenameStatus.LOOKUP_FAILED, preview=preview, error=reason)
    window.present_quick_action_failure(_quick_completion(result))
    qapp.processEvents()

    assert window.current_page is page
    assert window.quick_failure_panel.isVisible()
    assert "controlled lookup failure" in window.quick_failure_summary.text()
    assert "未修改任何文件" in window.quick_failure_summary.text()
    assert reason in window.quick_failure_details.toPlainText()
    assert "specific lookup reason" in window.quick_failure_details.toPlainText()
    assert qapp.focusWidget() is focus
    assert window.quick_failure_summary.accessibleDescription()
    assert not window.quick_failure_dialog.isVisible()
    button = window.quick_failure_details_button
    assert button.focusPolicy() is Qt.FocusPolicy.StrongFocus
    # Tab traversal reaches the action using the existing shell focus chain.
    for _ in range(100):
        if qapp.focusWidget() is button:
            break
        QTest.keyClick(qapp.focusWidget() or window, Qt.Key.Key_Tab)
    assert qapp.focusWidget() is button
    QTest.keyClick(button, Qt.Key.Key_Space)
    qapp.processEvents()
    assert window.quick_failure_dialog.isVisible()
    assert not window.quick_failure_dialog.isModal()
    assert window.quick_failure_details.isReadOnly()
    assert window.quick_failure_details.height() > 120
    assert reason in window.quick_failure_dialog_summary.text()
    window.resize(960, 749)
    qapp.processEvents()
    pages_geometry = window.pages.geometry()
    assert window.quick_failure_panel.height() <= 70
    central = window.centralWidget()
    assert central is not None
    assert window.quick_failure_panel.geometry().bottom() <= central.height()
    assert window.quick_failure_details.viewport().height() > 0
    if page is MainPage.ORGANIZER:
        organizer = window.organizer_page
        assert not organizer.table.geometry().intersects(organizer.footer.geometry())
    old_dialog_detail = window.quick_failure_details.toPlainText()
    viewer_focus = qapp.focusWidget()
    window.present_quick_action_failure(
        _quick_completion(error="newest background failure", request_id=2),
    )
    qapp.processEvents()
    assert qapp.focusWidget() is viewer_focus
    assert window.quick_failure_details.toPlainText() == old_dialog_detail
    assert "#2" in window.quick_failure_summary.text()
    assert window.quick_failure_dialog_summary.text().startswith("Quick Rename #1")
    assert window.pages.geometry() == pages_geometry
    window.quick_failure_dialog.close()
    assert not window.quick_failure_dialog.isVisible()
    window.quick_failure_details_button.click()
    qapp.processEvents()
    assert window.quick_failure_dialog.isVisible()
    assert "controlled lookup failure" in window.quick_failure_details.toPlainText()
    assert "newest background failure" not in window.quick_failure_details.toPlainText()
    assert window.quick_failure_selector.count() == 2
    window.navigate_to(MainPage.LOOKUP)
    assert window.quick_failure_panel.isVisible()
    window.hide()
    qapp.processEvents()
    assert not window.quick_failure_dialog.isVisible()
    window.close()


@pytest.mark.parametrize("status", list(QuickRenameStatus))
def test_quick_failure_classifies_typed_status_with_no_worker_exception(
    qapp: QApplication, status: QuickRenameStatus,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    result = QuickRenameResult(status, error="reason")
    window.present_quick_action_failure(_quick_completion(result))
    if status in {QuickRenameStatus.SUCCESS, QuickRenameStatus.NO_CHANGE}:
        assert window.quick_failure_selector.count() == 0
        assert window.quick_failure_panel.isHidden()
    else:
        assert window.quick_failure_selector.count() == 1
        assert "reason" in window.quick_failure_details.toPlainText()
        if status is QuickRenameStatus.RECOVERY_REQUIRED:
            assert "需要恢复" in window.quick_failure_summary.text()
            assert "未修改任何文件" not in window.quick_failure_summary.text()
    window.close()


@pytest.mark.parametrize("status", [TransactionStatus.PARTIAL, TransactionStatus.RECOVERY_REQUIRED])
def test_quick_failure_preserves_partial_and_recovery_facts(
    qapp: QApplication, status: TransactionStatus,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    execution = RenameExecutionResult(status, None, (
        RenameOperation("tx", 0, Path("A"), Path("B"), ExecutionStatus.SUCCESS),
        RenameOperation("tx", 1, Path("C"), Path("D"), ExecutionStatus.FAILED, "denied"),
    ), error="stopped")
    result = QuickRenameResult(
        QuickRenameStatus.RECOVERY_REQUIRED if status is TransactionStatus.RECOVERY_REQUIRED
        else QuickRenameStatus.EXECUTION_FAILED, execution=execution,
    )
    window.present_quick_action_failure(_quick_completion(result))
    text = window.quick_failure_details.toPlainText()
    outcome = "需要恢复" if status is TransactionStatus.RECOVERY_REQUIRED else "部分操作已完成"
    assert outcome in text
    assert "已完成 1 项" in text and "denied" in text and "stopped" in text
    assert "未修改任何文件" not in text
    window.close()


def test_quick_failure_retains_bounded_matching_request_details(qapp: QApplication) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    for request_id in range(1, 13):
        window.present_quick_action_failure(_quick_completion(
            error=f"runner diagnostic {request_id}", request_id=request_id,
        ))
    selector = window.quick_failure_selector
    assert selector.count() == 10
    assert selector.currentText() == "Quick Rename #12"
    assert "runner diagnostic 12" in window.quick_failure_details.toPlainText()
    selector.setCurrentIndex(0)
    assert "#3" in window.quick_failure_dialog_summary.text()
    assert "runner diagnostic 3" in window.quick_failure_details.toPlainText()
    assert "runner diagnostic 12" not in window.quick_failure_details.toPlainText()
    assert "未修改任何文件" not in window.quick_failure_details.toPlainText()
    window.quick_failure_details_button.click()
    qapp.processEvents()
    assert window.quick_failure_dialog.isVisible()
    window.present_quick_action_failure(
        _quick_completion(error="runner diagnostic 13", request_id=13),
    )
    assert selector.count() == 10
    assert selector.currentText() == "Quick Rename #4"
    assert "#4" in window.quick_failure_dialog_summary.text()
    assert "runner diagnostic 4" in window.quick_failure_details.toPlainText()
    assert "runner diagnostic 3" not in window.quick_failure_details.toPlainText()
    window.present_quick_action_failure(_quick_completion(QuickRenameResult(QuickRenameStatus.SUCCESS)))
    assert selector.count() == 10 and selector.currentIndex() == 0
    window.close()


def test_quick_failure_viewer_keeps_organizer_geometry_and_scrolls_long_details(
    qapp: QApplication, tmp_path: Path,
) -> None:
    from tests.ui.test_organizer_preview_model import synthetic_preview

    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    window.organizer_page.set_preview(synthetic_preview(tmp_path, 8))
    window.show()
    window.resize(960, 749)
    minimum_before_feedback = window.minimumSize()
    qapp.processEvents()
    no_feedback_pages = window.pages.geometry()
    window.present_quick_action_failure(
        _quick_completion(error="diagnostic detail " * 500),
    )
    qapp.processEvents()
    assert window.minimumSize() == minimum_before_feedback
    baseline_pages = window.pages.geometry()
    assert baseline_pages.height() < no_feedback_pages.height()
    organizer = window.organizer_page
    assert not organizer.table.geometry().intersects(organizer.footer.geometry())

    window.quick_failure_details_button.click()
    qapp.processEvents()
    viewer = window.quick_failure_dialog
    assert viewer.isVisible() and not viewer.isModal()
    assert window.pages.geometry() == baseline_pages
    assert not organizer.table.geometry().intersects(organizer.footer.geometry())
    assert window.quick_failure_details.verticalScrollBar().maximum() > 0
    window.resize(1160, 800)
    qapp.processEvents()
    medium_pages = window.pages.geometry()
    assert medium_pages.height() == 742
    assert not organizer.table.geometry().intersects(organizer.footer.geometry())
    assert window.quick_failure_details.verticalScrollBar().maximum() > 0
    screen = QApplication.screenAt(window.frameGeometry().center()) or QApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        assert viewer.width() <= available.width()
        assert viewer.height() <= available.height()

    viewer.close()
    qapp.processEvents()
    assert window.pages.geometry() == medium_pages
    window.resize(960, 749)
    qapp.processEvents()
    assert window.pages.geometry() == baseline_pages
    assert not organizer.table.geometry().intersects(organizer.footer.geometry())
    window.close()


def test_quick_failure_viewer_placement_fits_constrained_available_geometry(
    qapp: QApplication,
) -> None:
    available = QRect(40, 70, 640, 420)
    geometry = MainWindow._quick_failure_viewer_geometry(available)
    assert available.contains(geometry)
    assert geometry.size() == available.size()

    short_screen = QRect(-300, 20, 640, 360)
    geometry = MainWindow._quick_failure_viewer_geometry(short_screen)
    assert short_screen.contains(geometry)
    assert geometry.height() <= 360
    assert geometry.width() <= 640

    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    window.present_quick_action_failure(
        _quick_completion(error="constrained screen detail " * 200),
    )
    window.quick_failure_details_button.click()
    window.quick_failure_dialog.setMaximumSize(640, 360)
    window.quick_failure_dialog.resize(640, 360)
    qapp.processEvents()
    assert window.quick_failure_dialog.width() <= 640
    assert window.quick_failure_dialog.height() <= 360
    assert window.quick_failure_details.viewport().height() > 0
    assert window.quick_failure_details.verticalScrollBar().maximum() > 0
    window.close()


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


def test_history_selection_survives_navigation_and_hidden_journal_refresh(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    for index in range(3):
        transaction = journal.create_transaction(
            root,
            ((root / f"source-{index}", root / f"target-{index}"),),
        )
        journal.mark_operation_success(
            transaction.transaction_id,
            1,
            transaction.created_at,
        )
        journal.mark_transaction_completed(transaction.transaction_id, transaction.created_at)

    signals = RuntimeSignals()
    window = MainWindow(
        LookupService(FakeProvider(), NamingService()),
        CoverService(),
        runtime_signals=signals,
        rename_history_service=RenameHistoryService(journal),
    )
    try:
        window.navigate_to(MainPage.HISTORY)
        history = window.rename_history_page
        history.transaction_list.setCurrentRow(1)
        selected = history._selected_transaction
        assert selected is not None
        selected_id = selected.transaction_id

        window.navigate_to(MainPage.ORGANIZER)
        newest = journal.create_transaction(
            root,
            ((root / "new-source", root / "new-target"),),
        )
        journal.mark_operation_success(
            newest.transaction_id,
            1,
            newest.created_at,
        )
        journal.mark_transaction_completed(newest.transaction_id, newest.created_at)
        signals.notify_mutation_history_changed()
        assert history._selected_transaction is not None
        assert history._selected_transaction.transaction_id == selected_id

        window.navigate_to(MainPage.HISTORY)
        current_item = history.transaction_list.currentItem()
        assert current_item is not None
        assert current_item.data(history._TRANSACTION_ID_ROLE) == selected_id
        assert history._selected_transaction is not None
        assert history._selected_transaction.transaction_id == selected_id
        assert history.transaction_list.count() == 4
    finally:
        window.close()
        database.dispose()


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
