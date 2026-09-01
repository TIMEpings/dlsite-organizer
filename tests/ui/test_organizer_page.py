import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import cast

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QTableWidgetItem
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
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage, PreviewStaleReason


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
    assert headers == ["状态", "当前目录名", "作品编号", "社团", "标题", "目标目录名"]
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


def test_organizer_page_removes_duplicate_subtitle_but_keeps_drop_safety_copy(
    qapp: QApplication,
) -> None:
    page = OrganizerPage(OrganizerService(FakeLookupService()))

    labels = "\n".join(label.text() for label in page.findChildren(QLabel))

    assert "可选择根目录，也可将文件夹拖入此窗口" not in labels
    assert "将作品文件夹或作品根目录拖到这里" in labels
    assert "完整模式只生成预览，不会因拖放立即重命名。" in labels
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
    assert page.preview_stale_reason is PreviewStaleReason.FILESYSTEM_CHANGED
    assert page.undo_button.isEnabled()
    assert '可撤销：1' in page.recent_transaction_label.text()

    undone = undo_service.undo(execution.transaction_id, confirmed=True)
    page._show_undo_result(undone)

    assert undone.status is TransactionStatus.UNDONE
    assert page.preview_stale
    assert page.preview_stale_reason is PreviewStaleReason.FILESYSTEM_CHANGED
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


def test_organizer_status_cells_distinguish_normal_warning_and_blocked_states(
    qapp: QApplication, tmp_path: Path
) -> None:
    for code in ("RJ01609020", "RJ01636949", "RJ01637033"):
        (tmp_path / f"old {code}").mkdir()
    service = OrganizerService(FakeLookupService())
    preview = service.preview(tmp_path)
    warning = replace(
        preview.plans[1],
        warnings=("目标路径长度超过保守阈值。",),
    )
    blocked = replace(
        preview.plans[2],
        status=RenamePlanStatus.CONFLICT,
        error="目标目录已存在，未生成可执行的重命名计划。",
    )
    page = OrganizerPage(service)
    page.set_preview(replace(preview, plans=(preview.plans[0], warning, blocked)))

    normal_item = cast(QTableWidgetItem, page.table.item(0, 0))
    warning_item = cast(QTableWidgetItem, page.table.item(1, 0))
    blocked_item = cast(QTableWidgetItem, page.table.item(2, 0))

    assert normal_item.text() == "可执行"
    assert normal_item.icon().isNull()
    assert warning_item.icon().isNull() is False
    assert warning_item.font().bold()
    assert warning_item.text() == "可执行"
    assert "悬停查看原因" not in warning_item.text()
    assert "目标路径长度" in warning_item.toolTip()
    assert "悬停查看具体原因" in warning_item.toolTip()
    assert "目标路径长度" in str(warning_item.data(Qt.ItemDataRole.AccessibleTextRole))
    assert blocked_item.icon().isNull() is False
    assert blocked_item.font().bold()
    assert blocked_item.text() == "冲突"
    assert "悬停查看原因" not in blocked_item.text()
    assert "目标目录已存在" in blocked_item.toolTip()
    page.close()


def test_remove_selected_rows_updates_authoritative_preview_and_execution(
    qapp: QApplication, tmp_path: Path
) -> None:
    sources = [tmp_path / f"old {code}" for code in ("RJ01609020", "RJ01636949", "RJ01637033")]
    for source in sources:
        source.mkdir()
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    executor = RenameExecutor(journal)
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service, execution_service=executor)
    page.set_root_path(tmp_path)
    page.set_preview(service.preview(tmp_path))

    page.table.selectRow(1)
    assert page.remove_button.isEnabled()
    page.remove_button.click()

    assert page.preview is not None
    assert [plan.source_path for plan in page.preview.plans] == [sources[0], sources[2]]
    assert [plan.source_path for plan in page.selected_ready_plans()] == [sources[0], sources[2]]
    execution = executor.execute(tmp_path, page.selected_ready_plans(), confirmed=True)
    assert execution.success_count == 2
    assert sources[0].exists() is False
    assert sources[1].exists()
    assert sources[2].exists() is False
    page.close()
    database.dispose()


def test_remove_selected_supports_multiple_rows(qapp: QApplication, tmp_path: Path) -> None:
    for code in ("RJ01609020", "RJ01636949", "RJ01637033"):
        (tmp_path / f"old {code}").mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_preview(service.preview(tmp_path))
    selection = page.table.selectionModel()
    assert selection is not None
    for row in (0, 2):
        selection.select(
            page.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )

    page.remove_selected()

    assert page.table.rowCount() == 1
    assert page.preview is not None
    assert [plan.current_name for plan in page.preview.plans] == ["old RJ01636949"]
    page.close()


def test_clear_preview_is_immediate_preview_only_and_keeps_root(
    qapp: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / "old RJ01609020"
    source.mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_root_path(tmp_path)
    page.set_preview(service.preview(tmp_path))

    page.clear_button.click()

    assert page.preview is None
    assert page.table.rowCount() == 0
    assert not page.execute_button.isEnabled()
    assert not page.remove_button.isEnabled()
    assert not page.clear_button.isEnabled()
    assert page.root_input.text() == str(tmp_path)
    assert "未发现作品" not in page.status_label.text()
    assert source.exists()
    page.close()


def test_full_mode_drop_appends_and_redrop_upserts_without_reordering(
    qapp: QApplication, tmp_path: Path
) -> None:
    sources = [tmp_path / f"old {code}" for code in ("RJ01609020", "RJ01636949", "RJ01637033")]
    for source in sources:
        source.mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_root_path(tmp_path)
    page.set_preview(service.preview_paths(tmp_path, (sources[0], sources[1])))

    page._handle_drop_paths((sources[2],))
    _wait_for_scan_idle(qapp, page)
    assert page.preview is not None
    assert [plan.source_path for plan in page.preview.plans] == sources

    page._handle_drop_paths((str(sources[1]).upper(),))
    _wait_for_scan_idle(qapp, page)
    assert page.preview is not None
    assert [plan.source_path for plan in page.preview.plans] == sources
    assert page.table.rowCount() == 3
    page.close()


def test_full_mode_root_drop_appends_and_button_scan_replaces(
    qapp: QApplication, tmp_path: Path
) -> None:
    first = tmp_path / "old RJ01609020"
    second = tmp_path / "old RJ01636949"
    third = tmp_path / "old RJ01637033"
    for source in (first, second, third):
        source.mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_root_path(tmp_path)
    page.set_preview(service.preview_paths(tmp_path, (first,)))

    page._handle_drop_paths((tmp_path,))
    _wait_for_scan_idle(qapp, page)
    assert page.preview is not None
    assert [plan.current_name for plan in page.preview.plans] == [
        first.name,
        second.name,
        third.name,
    ]

    replacement_root = tmp_path / "replacement"
    replacement_root.mkdir()
    replacement = replacement_root / "new RJ01609020"
    replacement.mkdir()
    page.set_root_path(replacement_root)
    page.scan_button.click()
    _wait_for_scan_idle(qapp, page)
    assert page.preview is not None
    assert [plan.current_name for plan in page.preview.plans] == [replacement.name]
    page.close()


def test_append_failure_preserves_existing_preview(
    qapp: QApplication, tmp_path: Path
) -> None:
    class FailingAppendService:
        def __init__(self) -> None:
            self._fallback = OrganizerService(FakeLookupService())

        def preview(self, root_path, **kwargs):
            return self._fallback.preview(root_path, **kwargs)

        def preview_paths(self, root_path, paths, **kwargs):
            raise RuntimeError("append fixture failure")

    first = tmp_path / "old RJ01609020"
    second = tmp_path / "old RJ01636949"
    first.mkdir()
    second.mkdir()
    fallback = OrganizerService(FakeLookupService())
    page = OrganizerPage(FailingAppendService())  # type: ignore[arg-type]
    page.set_root_path(tmp_path)
    page.set_preview(fallback.preview_paths(tmp_path, (first,)))

    page._handle_drop_paths((second,))
    _wait_for_scan_idle(qapp, page)

    assert page.preview is not None
    assert [plan.source_path for plan in page.preview.plans] == [first]
    assert "当前预览保持不变" in page.status_label.text()
    page.close()


def test_append_cancel_preserves_existing_preview(
    qapp: QApplication, tmp_path: Path
) -> None:
    first = tmp_path / "old RJ01609020"
    second = tmp_path / "old RJ01636949"
    first.mkdir()
    second.mkdir()
    existing = OrganizerService(FakeLookupService()).preview_paths(tmp_path, (first,))
    lookup = BlockingLookupService()
    page = OrganizerPage(OrganizerService(lookup))
    page.set_root_path(tmp_path)
    page.set_preview(existing)

    try:
        page._handle_drop_paths((second,))
        assert lookup.entered.wait(timeout=2)
        page.cancel_button.click()
        lookup.release.set()
        _wait_for_scan_idle(qapp, page)
        assert page.preview is not None
        assert [plan.source_path for plan in page.preview.plans] == [first]
    finally:
        lookup.release.set()
        page.close()


def test_scan_failure_preserves_existing_preview_until_replacement_succeeds(
    qapp: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / "old RJ01609020"
    source.mkdir()
    fallback = OrganizerService(FakeLookupService())
    service = FlakyOrganizerService()
    page = OrganizerPage(service)  # type: ignore[arg-type]
    page.set_root_path(tmp_path)
    page.set_preview(fallback.preview(tmp_path))

    page.scan_button.click()
    _wait_for_scan_idle(qapp, page)

    assert page.preview is not None
    assert [plan.source_path for plan in page.preview.plans] == [source]
    assert "当前预览保持不变" in page.status_label.text()
    page.close()


def test_stale_preview_rejects_append_but_can_be_cleared(
    qapp: QApplication, tmp_path: Path
) -> None:
    first = tmp_path / "old RJ01609020"
    second = tmp_path / "old RJ01636949"
    first.mkdir()
    second.mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_root_path(tmp_path)
    page.set_preview(service.preview_paths(tmp_path, (first,)))
    page.invalidate_preview()

    assert page.preview_stale_reason is PreviewStaleReason.SETTINGS_CHANGED
    page._handle_drop_paths((second,))
    assert not page.is_busy()
    assert page.preview is not None
    assert [plan.source_path for plan in page.preview.plans] == [first]
    assert "设置变化失效" in page.status_label.text()
    assert page.clear_button.isEnabled()

    page.clear_button.click()
    assert page.preview is None
    assert not page.preview_stale
    assert page.preview_stale_reason is None
    page._handle_drop_paths((second,))
    _wait_for_scan_idle(qapp, page)
    preview = cast(OrganizerPreview, page.preview)
    assert [plan.source_path for plan in preview.plans] == [second]
    page.close()


def test_empty_preview_resets_settings_stale_and_accepts_new_root_drop(
    qapp: QApplication, tmp_path: Path
) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    new_root = tmp_path / "new"
    source = new_root / "old RJ01609020"
    source.mkdir(parents=True)
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_root_path(empty_root)
    page.set_preview(service.preview(empty_root))
    page.invalidate_preview()

    try:
        assert page.preview is not None
        assert not page.preview.plans
        assert not page.preview_stale
        assert page.preview_stale_reason is None

        page._handle_drop_paths((source,))
        _wait_for_scan_idle(qapp, page)

        assert page.preview is not None
        assert page.preview.root_path == new_root.absolute()
        assert [plan.source_path for plan in page.preview.plans] == [source.absolute()]
        assert not page.preview_stale
        assert page.preview_stale_reason is None
    finally:
        page.close()


def test_stale_preview_explicit_scan_replaces_and_allows_later_append(
    qapp: QApplication, tmp_path: Path
) -> None:
    first = tmp_path / "old RJ01609020"
    second = tmp_path / "old RJ01636949"
    third = tmp_path / "old RJ01637033"
    for source in (first, second, third):
        source.mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_root_path(tmp_path)
    page.set_preview(service.preview_paths(tmp_path, (first,)))
    page.invalidate_preview()

    try:
        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)

        assert page.preview is not None
        assert not page.preview_stale
        assert page.preview_stale_reason is None
        assert [plan.source_path for plan in page.preview.plans] == [
            first.absolute(),
            second.absolute(),
            third.absolute(),
        ]
        page._handle_drop_paths((third,))
        _wait_for_scan_idle(qapp, page)
        assert page.preview is not None
        assert not page.preview_stale
    finally:
        page.close()
