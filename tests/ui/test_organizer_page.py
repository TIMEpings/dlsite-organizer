import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem
from tests.services.test_lookup_cache import (
    MutableClock,
    SequencedProvider,
    make_lookup,
    open_service,
)

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.rename_execution import RenameExecutionResult, TransactionStatus
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal, UnavailableRenameJournal
from dlsite_organizer.services.lookup import LookupFreshness, LookupResult
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage


class FakeLookupService:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.thread_ids: list[int] = []

    def lookup(self, raw_workno: str) -> LookupResult:
        self.calls.append(raw_workno)
        self.thread_ids.append(threading.get_ident())
        work = Work(workno=raw_workno, title="Preview Title", maker_name="Circle")
        return LookupResult(work=work, formatted_name=f"[Circle][{raw_workno}] Preview Title")


class BlockingLookupService(FakeLookupService):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()
        self.block_next = True

    def lookup(self, raw_workno: str) -> LookupResult:
        if self.block_next:
            self.block_next = False
            self.entered.set()
            self.release.wait(timeout=5)
        return super().lookup(raw_workno)


class FlakyOrganizerService:
    def __init__(self) -> None:
        self._fallback = OrganizerService(FakeLookupService())
        self.fail_next = True

    def preview(self, root_path, **kwargs):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("fixture failure")
        return self._fallback.preview(root_path, **kwargs)

    def preview_paths(self, root_path, paths, **kwargs):
        return self._fallback.preview_paths(root_path, paths, **kwargs)


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def _wait_for(qapp: QApplication, predicate, *, timeout_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate(), "timed out waiting for organizer UI state"


def _wait_for_scan_idle(qapp: QApplication, page: OrganizerPage) -> None:
    _wait_for(
        qapp,
        lambda: (
            not page.is_busy()
            and page.scan_button.isEnabled()
            and not page.cancel_button.isEnabled()
        ),
    )


def test_organizer_page_scan_button_empty_root_completes_and_clears_busy(
    qapp: QApplication, tmp_path: Path
) -> None:
    root = tmp_path / "empty-root"
    root.mkdir()
    lookup = FakeLookupService()
    page = OrganizerPage(OrganizerService(lookup))
    page.set_root_path(root)

    try:
        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)

        assert page._preview is not None
        assert page._preview.scan.candidates == ()
        assert page._preview.plans == ()
        assert lookup.calls == []
        assert page.table.rowCount() == 0
        assert page.status_label.text() == "未发现可整理的作品文件夹；未修改本地文件。"
        assert not page.is_busy()
        assert page.scan_button.isEnabled()
        assert not page.cancel_button.isEnabled()
    finally:
        page.close()


def test_organizer_page_scan_button_uses_fresh_cache_without_provider_call(
    qapp: QApplication, tmp_path: Path
) -> None:
    root = tmp_path / "one-work-root"
    (root / "RJ01609020 test").mkdir(parents=True)
    provider = SequencedProvider([make_lookup(Work(workno="RJ01609020", title="Cached"))])
    database, _store, lookup_service = open_service(
        tmp_path / "metadata.sqlite3",
        provider,
        MutableClock(datetime(2026, 8, 31, 10, 0, tzinfo=UTC)),
    )
    lookup_service.lookup("RJ01609020")
    assert provider.calls == ["RJ01609020"]
    assert provider.calls is not None
    provider.calls.clear()
    page = OrganizerPage(OrganizerService(lookup_service))
    page.set_root_path(root)

    try:
        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)

        assert provider.calls == []
        assert page._preview is not None
        assert len(page._preview.plans) == 1
        assert page._preview.plans[0].status is RenamePlanStatus.READY
        assert page.table.rowCount() == 1
    finally:
        page.close()
        database.dispose()


def test_organizer_page_scan_lookup_runs_outside_gui_thread(
    qapp: QApplication, tmp_path: Path
) -> None:
    (tmp_path / "RJ01609020 test").mkdir()
    lookup = FakeLookupService()
    page = OrganizerPage(OrganizerService(lookup))
    page.set_root_path(tmp_path)
    gui_thread_id = threading.get_ident()

    try:
        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)

        assert lookup.thread_ids
        assert all(thread_id != gui_thread_id for thread_id in lookup.thread_ids)
    finally:
        page.close()


def test_organizer_page_cancel_is_cooperative_and_leaves_idle_state(
    qapp: QApplication, tmp_path: Path
) -> None:
    (tmp_path / "one RJ01609020").mkdir()
    (tmp_path / "two RJ01636949").mkdir()
    lookup = BlockingLookupService()
    page = OrganizerPage(OrganizerService(lookup))
    page.set_root_path(tmp_path)

    try:
        page.scan_button.click()
        assert lookup.entered.wait(timeout=2)
        assert not page.scan_button.isEnabled()
        assert page.cancel_button.isEnabled()

        page.cancel_button.click()
        assert not page.cancel_button.isEnabled()
        lookup.release.set()
        _wait_for_scan_idle(qapp, page)

        assert page._preview is not None
        assert page._preview.cancelled
        assert lookup.calls == ["RJ01609020"]
        assert page.status_label.text().startswith("扫描已取消")
    finally:
        lookup.release.set()
        page.close()


def test_organizer_page_cancel_then_rescan_succeeds(
    qapp: QApplication, tmp_path: Path
) -> None:
    (tmp_path / "one RJ01609020").mkdir()
    (tmp_path / "two RJ01636949").mkdir()
    lookup = BlockingLookupService()
    page = OrganizerPage(OrganizerService(lookup))
    page.set_root_path(tmp_path)

    try:
        page.scan_button.click()
        assert lookup.entered.wait(timeout=2)
        page.cancel_button.click()
        lookup.release.set()
        _wait_for_scan_idle(qapp, page)

        lookup.entered.clear()
        lookup.release.clear()
        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)

        assert page._preview is not None
        assert not page._preview.cancelled
        assert lookup.calls == ["RJ01609020", "RJ01609020", "RJ01636949"]
    finally:
        lookup.release.set()
        page.close()


def test_organizer_page_repeated_scans_do_not_leave_stale_workers(
    qapp: QApplication, tmp_path: Path
) -> None:
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.set_root_path(tmp_path)

    try:
        for _ in range(5):
            page.scan_button.click()
            _wait_for_scan_idle(qapp, page)
            assert not page.is_busy()
    finally:
        page.close()


def test_organizer_page_scan_exception_clears_busy_and_allows_retry(
    qapp: QApplication, tmp_path: Path
) -> None:
    service = FlakyOrganizerService()
    page = OrganizerPage(service)  # type: ignore[arg-type]
    page.set_root_path(tmp_path)

    try:
        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)
        assert "扫描时发生意外错误" in page.status_label.text()

        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)
        assert page.status_label.text() == "未发现可整理的作品文件夹；未修改本地文件。"
    finally:
        page.close()


def test_organizer_page_initializes_and_renders_preview(qapp: QApplication, tmp_path: Path) -> None:
    source = tmp_path / "old RJ01609020"
    source.mkdir()
    before = sorted(path.name for path in tmp_path.iterdir())
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.set_root_path(tmp_path)

    preview = OrganizerService(FakeLookupService()).preview(tmp_path)
    page.set_preview(preview)

    assert page.root_input.text() == str(tmp_path)
    assert page.table.rowCount() == 1
    headers = []
    for index in range(page.table.columnCount()):
        header = page.table.horizontalHeaderItem(index)
        assert header is not None
        headers.append(header.text())
    assert headers == ["状态", "当前目录名", "RJcode", "社团", "标题", "目标目录名"]
    assert "详情" not in headers
    status_item = cast(QTableWidgetItem, page.table.item(0, 0))
    current_item = cast(QTableWidgetItem, page.table.item(0, 1))
    code_item = cast(QTableWidgetItem, page.table.item(0, 2))
    proposed_item = cast(QTableWidgetItem, page.table.item(0, 5))
    assert status_item.text() == "可执行"
    assert current_item.text() == "old RJ01609020"
    assert code_item.text() == "RJ01609020"
    assert proposed_item.text() == "[Circle][RJ01609020] Preview Title"
    assert "可执行 1" in page.summary_label.text()
    assert page.status_label.text() == "预览生成完成；未修改本地文件。"
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    page.close()


def test_organizer_page_selects_ready_rows_and_disables_non_ready_rows(
    qapp: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / 'old RJ01609020'
    source.mkdir()
    database = Database(tmp_path / 'journal.sqlite3')
    database.initialize()
    journal = TransactionJournal(database)
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(
        service,
        execution_service=RenameExecutor(journal),
        undo_service=UndoService(journal),
    )
    preview = service.preview(tmp_path)

    page.set_preview(preview)

    assert page.selected_ready_count() == 1
    assert page.execute_button.isEnabled()
    ready_item = cast(QTableWidgetItem, page.table.item(0, 0))
    assert ready_item.checkState().name == 'Checked'

    ready_item.setCheckState(ready_item.checkState().Unchecked)
    assert page.selected_ready_count() == 0
    assert not page.execute_button.isEnabled()

    non_ready_preview = replace(
        preview,
        plans=(replace(preview.plans[0], status=RenamePlanStatus.CONFLICT),),
    )
    page.set_preview(non_ready_preview)
    non_ready_item = cast(QTableWidgetItem, page.table.item(0, 0))
    assert not (non_ready_item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert not page.execute_button.isEnabled()
    page.close()


def test_organizer_page_disables_execute_without_journal_and_marks_stale_after_action(
    qapp: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / 'old RJ01609020'
    source.mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(
        service,
        execution_service=RenameExecutor(UnavailableRenameJournal()),
        undo_service=UndoService(UnavailableRenameJournal()),
    )
    preview = service.preview(tmp_path)

    page.set_preview(preview)
    assert page.selected_ready_count() == 1
    assert not page.execute_button.isEnabled()

    page._show_execution_result(
        RenameExecutionResult(status=TransactionStatus.COMPLETED, transaction=None)
    )

    assert page.preview_stale
    assert not page.execute_button.isEnabled()
    assert not page.table.isEnabled()
    assert "预览已失效" in page.summary_label.text()
    page.close()


def test_organizer_page_confirmation_starts_only_selected_ready_action(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / 'old RJ01609020'
    source.mkdir()
    database = Database(tmp_path / 'journal.sqlite3')
    database.initialize()
    journal = TransactionJournal(database)
    execution_service = RenameExecutor(journal)
    page = OrganizerPage(
        OrganizerService(FakeLookupService()),
        execution_service=execution_service,
    )
    page.set_root_path(tmp_path)
    page.set_preview(OrganizerService(FakeLookupService()).preview(tmp_path))
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        QMessageBox,
        'exec',
        lambda _dialog: QMessageBox.StandardButton.Yes,
    )
    page._start_action = lambda action, result_handler: captured.update(
        action=action,
        handler=result_handler,
    )

    page.execute_rename()

    action = captured['action']
    assert callable(action)
    result = action(lambda *_args: None)
    assert isinstance(result, RenameExecutionResult)
    assert result.status is TransactionStatus.COMPLETED
    assert not source.exists()
    assert (tmp_path / '[Circle][RJ01609020] Preview Title').exists()
    page.close()


def test_organizer_page_shows_recent_transaction_and_disables_undo_after_success(
    qapp: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / 'old RJ01609020'
    source.mkdir()
    database = Database(tmp_path / 'journal.sqlite3')
    database.initialize()
    journal = TransactionJournal(database)
    executor = RenameExecutor(journal)
    undo_service = UndoService(journal)
    page = OrganizerPage(
        OrganizerService(FakeLookupService()),
        execution_service=executor,
        undo_service=undo_service,
    )
    preview = OrganizerService(FakeLookupService()).preview(tmp_path)
    page.set_preview(preview)
    execution = executor.execute(tmp_path, page.selected_ready_plans(), confirmed=True)

    page._show_execution_result(execution)

    assert page.preview_stale
    assert page.undo_button.isEnabled()
    assert '可撤销：1' in page.recent_transaction_label.text()

    undone = undo_service.undo(execution.transaction_id, confirmed=True)
    page._show_undo_result(undone)

    assert undone.status is TransactionStatus.UNDONE
    assert not page.undo_button.isEnabled()
    assert (tmp_path / 'old RJ01609020').exists()
    page.close()


def test_organizer_page_shows_stale_metadata_warning_but_not_fresh_cache_warning(
    qapp: QApplication, tmp_path: Path
) -> None:
    (tmp_path / "old RJ01609020").mkdir()

    class FreshnessLookupService:
        def __init__(self, freshness: LookupFreshness) -> None:
            self.freshness = freshness

        def lookup(self, raw_workno: str) -> LookupResult:
            work = Work(workno=raw_workno, title="Preview Title", maker_name="Circle")
            return LookupResult(
                work=work,
                formatted_name=f"[Circle][{raw_workno}] Preview Title",
                freshness=self.freshness,
            )

    stale_page = OrganizerPage(
        OrganizerService(FreshnessLookupService(LookupFreshness.CACHE_STALE_FALLBACK))
    )
    stale_page.set_preview(stale_page._organizer_service.preview(tmp_path))
    stale_details = cast(QTableWidgetItem, stale_page.table.item(0, 0))
    assert stale_page._preview is not None
    assert stale_page._preview.plans[0].status is RenamePlanStatus.READY
    assert "使用旧缓存 metadata" in stale_details.toolTip()
    stale_page.close()

    fresh_page = OrganizerPage(
        OrganizerService(FreshnessLookupService(LookupFreshness.CACHE_FRESH))
    )
    fresh_page.set_preview(fresh_page._organizer_service.preview(tmp_path))
    fresh_status = cast(QTableWidgetItem, fresh_page.table.item(0, 0))
    assert "使用旧缓存 metadata" not in fresh_status.toolTip()
    fresh_page.close()


def test_full_mode_drop_interprets_work_folder_selectively_and_root_ordinarily(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "RJ01609020 old"
    work.mkdir()
    sibling = tmp_path / "RJ01636949 sibling"
    sibling.mkdir()
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    captured: list[tuple[Path | str, ...] | None] = []
    monkeypatch.setattr(
        page,
        "start_scan",
        lambda selected_paths=None: captured.append(selected_paths),
    )

    page._handle_drop_paths((work,))

    assert page.root_input.text() == str(tmp_path)
    assert captured == [(work.absolute(),)]

    captured.clear()
    page._handle_drop_paths((tmp_path,))
    assert page.root_input.text() == str(tmp_path)
    assert captured == [None]
    page.close()


def test_full_mode_drop_rejects_different_parents_without_starting_scan(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "one" / "RJ01609020"
    second = tmp_path / "two" / "RJ01636949"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    started = []
    monkeypatch.setattr(page, "start_scan", lambda *_args, **_kwargs: started.append(True))

    page._handle_drop_paths((first, second))

    assert started == []
    assert "同一父目录" in page.status_label.text()
    page.close()
