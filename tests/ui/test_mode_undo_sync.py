from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from tests.services.test_lookup import FakeProvider
from tests.services.test_undo_service import FailOnceFilesystem

from dlsite_organizer.app.runtime import RuntimeSignals
from dlsite_organizer.app.settings import AppSettings, SettingsService
from dlsite_organizer.domain.rename_execution import TransactionStatus, UndoStatus
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupResult, LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService
from dlsite_organizer.services.quick_rename import QuickRenameService, QuickRenameStatus
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.lightweight_window import LightweightWindow
from dlsite_organizer.ui.main_window import MainWindow
from dlsite_organizer.ui.pages.organizer_page import PreviewStaleReason


class FakeOrganizerLookup:
    def lookup(self, raw_workno: str) -> LookupResult:
        work = Work(workno=raw_workno, title="Target", maker_name="Circle")
        return LookupResult(work=work, formatted_name=f"[{raw_workno}] Target")


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def _wait_for(qapp: QApplication, predicate, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate(), "timed out waiting for Qt state"


def _windows(tmp_path: Path) -> tuple[
    Database,
    TransactionJournal,
    RuntimeSignals,
    MainWindow,
    LightweightWindow,
]:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    signals = RuntimeSignals()
    executor = RenameExecutor(
        journal,
        mutation_history_changed=signals.notify_mutation_history_changed,
    )
    undo_service = UndoService(
        journal,
        mutation_history_changed=signals.notify_mutation_history_changed,
    )
    organizer_service = OrganizerService(FakeOrganizerLookup())
    quick_rename_service = QuickRenameService(organizer_service, executor)
    settings = SettingsService(
        AppSettings(database_path=tmp_path / "metadata.sqlite3")
    )
    full = MainWindow(
        LookupService(FakeProvider(), NamingService()),
        CoverService(),
        organizer_service=organizer_service,
        rename_executor=executor,
        undo_service=undo_service,
        runtime_signals=signals,
    )
    lightweight = LightweightWindow(
        quick_rename_service,
        undo_service,
        settings,
        runtime_signals=signals,
    )
    return database, journal, signals, full, lightweight


def _connect_mode_switch(full: MainWindow, lightweight: LightweightWindow) -> None:
    def show_full() -> None:
        full.show()
        lightweight.hide()

    def show_lightweight() -> None:
        lightweight.show()
        full.hide()

    lightweight.full_mode_requested.connect(show_full)
    full.lightweight_requested.connect(show_lightweight)


@pytest.mark.parametrize("outcome", ["preflight", "cancel", "first_failure", "resumed_failure",
                                    "success", "partial", "refresh_failure", "recovery",
                                    "different_root"])
def test_organizer_undo_uses_result_despite_runtime_journal_notifications(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    database, journal, signals, full, lightweight = _windows(tmp_path)
    page = full.organizer_page
    service = page._organizer_service
    executor = page._execution_service
    assert executor is not None
    root = tmp_path / "library"
    root.mkdir()
    for code in ("RJ000001", "RJ000002", "RJ000003", "RJ000005"):
        (root / f"old {code}").mkdir()
    notifications = []
    signals.mutation_history_changed.connect(lambda: notifications.append(True))
    try:
        full.show()
        initial = service.preview(root)
        execution = executor.execute(root, initial.plans[:2], confirmed=True)
        qapp.processEvents()
        if outcome == "resumed_failure":
            partial = UndoService(journal, filesystem=FailOnceFilesystem(
                execution.operations[0].target_path.name)).undo(
                    execution.transaction_id, confirmed=True)
            assert partial.success_count == 1
        if outcome == "different_root":
            other = tmp_path / "other"
            other.mkdir()
            (other / "old RJ000004").mkdir()
            executor.execute(other, service.preview(other).plans, confirmed=True)
            qapp.processEvents()
        if outcome == "preflight":
            execution.operations[0].source_path.mkdir()  # Real preflight occupancy rejection.
        page.set_preview(service.preview(root))
        # Keep the unrelated READY row unchecked to prove check intent survives too.
        ready_row = next(i for i, p in enumerate(page.state.plans) if p.work_code == "RJ000003")
        # Leave a READY row checked so Execute eligibility can be compared.
        page.preview_model.bulk_check("select")
        page.preview_model.setData(page.preview_model.index(ready_row, 0),
                                   Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        preview = page.preview
        plans, keys = page.state.plans, page.state.row_keys
        checks = tuple(page.state.checked(i) for i in range(len(plans)))
        scope = dict(page.state.review_scope)
        request, revision = page.state.request_id, page.state.preview_revision
        eligible = page.execute_button.isEnabled()
        assert eligible and page.state.preview_current
        refreshes = []
        refresh = service.refresh_preview_scope

        def scoped(*args, **kwargs):
            refreshes.append(args)
            if outcome == "refresh_failure":
                raise RuntimeError("controlled refresh failure")
            return refresh(*args, **kwargs)

        monkeypatch.setattr(service, "refresh_preview_scope", scoped)
        if outcome in {"partial", "first_failure", "resumed_failure"}:
            index = 1 if outcome == "first_failure" else 0
            page._undo_service = UndoService(
                journal,
                filesystem=FailOnceFilesystem(execution.operations[index].target_path.name),
                mutation_history_changed=signals.notify_mutation_history_changed)
        if outcome == "recovery":
            def fail(*args):
                raise OSError("controlled journal failure after filesystem mutation")

            monkeypatch.setattr(journal, "mark_undo_success", fail)
        monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: (
            QMessageBox.StandardButton.Cancel if outcome == "cancel"
            else QMessageBox.StandardButton.Yes))
        notifications.clear()
        stale_transitions = []
        invalidate = page._invalidate_preview

        def invalidated(reason):
            stale_transitions.append(reason)
            invalidate(reason)

        monkeypatch.setattr(page, "_invalidate_preview", invalidated)
        page.undo_button.click()
        _wait_for(qapp, lambda: not page.is_busy())
        result = page.state.last_undo_result
        no_mutation = outcome in {"preflight", "cancel", "first_failure", "resumed_failure"}
        if outcome != "cancel":
            assert result is not None and notifications
        if outcome == "preflight":
            assert result is not None and result.status is TransactionStatus.FAILED
            assert result.success_count == 0
            assert all(op.undo_status is UndoStatus.CONFLICT for op in result.operations)
            assert "整批未执行" in page.status_label.text()
        if outcome == "resumed_failure":
            assert result is not None and result.success_count == 1  # Historical success only.
        if no_mutation or outcome == "different_root":
            assert page.preview is preview and page.state.preview_current
            assert page.state.plans == plans and page.state.row_keys == keys
            assert tuple(page.state.checked(i) for i in range(len(plans))) == checks
            assert page.state.review_scope == scope
            assert (page.state.request_id, page.state.preview_revision) == (request, revision)
            assert not refreshes and not stale_transitions
            assert page.state.stale_reason is None and page.status_banner.isHidden()
            assert page.execute_button.isEnabled() == eligible and page.state.checks_editable
            if outcome == "preflight":
                # Process the actual 6500 ms timer, queued runtime signals and worker cleanup.
                _wait_for(qapp, lambda: not page._feedback_timer.isActive(), timeout_seconds=8)
                qapp.processEvents()
                assert not page.status_label.text()
                assert page.state.preview_current and page.state.stale_reason is None
                assert page.status_banner.isHidden() and not stale_transitions and not refreshes
                assert page.state.plans == plans and page.state.row_keys == keys
                assert tuple(page.state.checked(i) for i in range(len(plans))) == checks
                assert page.execute_button.isEnabled() == eligible
        elif outcome == "recovery":
            assert result is not None and result.status is TransactionStatus.RECOVERY_REQUIRED
            assert not refreshes and page.state.unresolved_transaction_id is not None
            assert page.state.stale and not page.execute_button.isEnabled()
            assert page.status_banner.title_label.text() == "存在未解决的重命名事务"
            assert page.retry_refresh_button.isHidden()
        else:
            assert len(refreshes) == 1 and page.state.request_id == request + 1
            assert result is not None
            assert result.success_count == (1 if outcome == "partial" else 2)
            expected_scope = tuple(
                op.source_path if op.undo_status is UndoStatus.SUCCESS else op.target_path
                for op in result.operations)
            assert tuple(page.state.review_scope.values())[:2] == expected_scope
            if outcome == "refresh_failure":
                assert page.state.stale_reason is PreviewStaleReason.REFRESH_FAILED
                assert page.retry_refresh_button.isEnabled() and not page.execute_button.isEnabled()
                assert page.preview is preview
            else:
                assert page.state.preview_current and page.state.stale_reason is None
                assert page.state.checks_editable
                assert page.execute_button.isEnabled() == page.state.can_execute
    finally:
        _wait_for(qapp, lambda: not page.is_busy())
        full.close()
        lightweight.close()
        database.dispose()


def test_quick_rename_then_full_mode_undo_needs_no_restart(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _journal, _signals, full, lightweight = _windows(tmp_path)
    _connect_mode_switch(full, lightweight)
    source = tmp_path / "RJ01609020 old"
    source.mkdir()

    try:
        assert not full.organizer_page.undo_button.isEnabled()
        assert not lightweight.undo_button.isEnabled()
        lightweight.show()

        lightweight.start_quick_rename(source)
        _wait_for(qapp, lambda: lightweight._last_result is not None and not lightweight.is_busy())

        result = lightweight._last_result
        assert result is not None
        assert result.status is QuickRenameStatus.SUCCESS
        assert result.execution is not None
        assert result.execution.status is TransactionStatus.COMPLETED
        assert result.transaction_id is not None
        target = result.execution.operations[0].target_path
        assert target.is_dir()
        assert not source.exists()

        lightweight.full_mode_button.click()
        _wait_for(qapp, lambda: full.isVisible() and not lightweight.isVisible())
        assert full.organizer_page.undo_button.isEnabled()
        assert full.organizer_page.recent_transaction_label.toolTip() == result.transaction_id
        assert "未解决的重命名事务" not in full.organizer_page.status_label.text()

        monkeypatch.setattr(
            QMessageBox,
            "exec",
            lambda _dialog: QMessageBox.StandardButton.Yes,
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        full.organizer_page.undo_recent()
        _wait_for(qapp, lambda: not full.organizer_page.is_busy())
        _wait_for(qapp, lambda: not lightweight.undo_button.isEnabled())

        assert source.is_dir()
        assert not target.exists()
        assert full.organizer_page.state.last_undo_result is not None
        assert full.organizer_page.state.last_undo_result.status is TransactionStatus.UNDONE
        assert full.organizer_page.preview is None
        assert not full.organizer_page.preview_stale
        assert full.organizer_page.preview_stale_reason is None

        # This is the reported regression: with no full-mode preview, a
        # successful undo must not turn the next valid drop into a settings
        # stale rejection.
        full.organizer_page._handle_drop_paths((source,))
        assert full.organizer_page.is_busy()
        _wait_for(qapp, lambda: not full.organizer_page.is_busy())

        preview = cast(OrganizerPreview, full.organizer_page.preview)
        assert [plan.source_path for plan in preview.plans] == [source]
        assert not full.organizer_page.preview_stale
        assert "设置变化失效" not in full.organizer_page.status_label.text()
    finally:
        full.close()
        lightweight.close()
        database.dispose()


def test_existing_full_preview_becomes_filesystem_stale_after_quick_undo(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _journal, _signals, full, lightweight = _windows(tmp_path)
    _connect_mode_switch(full, lightweight)
    source = tmp_path / "RJ01609020 old"
    source.mkdir()

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    try:
        full.show()
        organizer = full.organizer_page
        organizer.set_root_path(tmp_path)
        organizer.set_preview(organizer._organizer_service.preview(tmp_path))
        assert not organizer.preview_stale

        full.lightweight_button.click()
        _wait_for(qapp, lambda: lightweight.isVisible() and not full.isVisible())
        lightweight.start_quick_rename(source)
        _wait_for(qapp, lambda: lightweight._last_result is not None and not lightweight.is_busy())
        assert lightweight._last_result is not None
        assert lightweight._last_result.status is QuickRenameStatus.SUCCESS

        lightweight.undo_recent()
        _wait_for(qapp, lambda: not lightweight.is_busy())
        assert source.is_dir()

        lightweight.full_mode_button.click()
        _wait_for(qapp, lambda: full.isVisible() and not lightweight.isVisible())

        assert organizer.preview is not None
        assert organizer.preview_stale
        assert organizer.preview_stale_reason is PreviewStaleReason.FILESYSTEM_CHANGED
        assert "设置变化失效" not in organizer.status_label.text()
        organizer._handle_drop_paths((source,))
        assert "文件系统变化失效" in organizer.status_label.text()
    finally:
        full.close()
        lightweight.close()
        database.dispose()


def test_full_mode_rename_then_lightweight_undo_is_immediately_available(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _journal, _signals, full, lightweight = _windows(tmp_path)
    _connect_mode_switch(full, lightweight)
    source = tmp_path / "RJ01609020 old"
    source.mkdir()

    try:
        full.show()
        organizer = full.organizer_page
        organizer.set_root_path(tmp_path)
        organizer.set_preview(organizer._organizer_service.preview(tmp_path))
        assert organizer.execute_button.isEnabled()

        monkeypatch.setattr(
            QMessageBox,
            "exec",
            lambda _dialog: QMessageBox.StandardButton.Yes,
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        organizer.execute_rename()
        _wait_for(qapp, lambda: not organizer.is_busy())

        execution = organizer.state.last_execution_result
        assert execution is not None
        assert execution.status is TransactionStatus.COMPLETED
        assert execution.operations[0].target_path.is_dir()

        full.lightweight_button.click()
        _wait_for(qapp, lambda: lightweight.isVisible() and not full.isVisible())
        assert lightweight.undo_button.isEnabled()

        lightweight.undo_recent()
        _wait_for(qapp, lambda: not lightweight.is_busy())
        assert source.is_dir()
        assert not lightweight.undo_button.isEnabled()
    finally:
        full.close()
        lightweight.close()
        database.dispose()


@pytest.mark.parametrize("stage", ["forward", "undo_intent", "undo_recovery"])
def test_recovery_required_state_is_refreshed_in_both_modes(
    qapp: QApplication, tmp_path: Path, stage: str,
) -> None:
    database, journal, signals, full, lightweight = _windows(tmp_path)
    source = tmp_path / "RJ01609020 old"
    target = tmp_path / "[RJ01609020] Target"
    source.mkdir()

    try:
        full.show()
        organizer = full.organizer_page
        organizer.set_root_path(tmp_path)
        organizer.set_preview(organizer._organizer_service.preview(tmp_path))
        assert organizer.execute_button.isEnabled()

        transaction = journal.create_transaction(tmp_path, ((source, target),))
        if stage == "forward":
            journal.mark_recovery_required(
                transaction.transaction_id, None, "fixture recovery state", datetime.now(UTC),
            )
        else:
            journal.mark_operation_success(transaction.transaction_id, 1, datetime.now(UTC))
            journal.mark_transaction_completed(transaction.transaction_id, datetime.now(UTC))
            journal.begin_undo(transaction.transaction_id, datetime.now(UTC))
            if stage == "undo_recovery":
                journal.mark_undo_recovery_required(
                    transaction.transaction_id, 1, "fixture undo recovery", datetime.now(UTC),
                )
        signals.notify_mutation_history_changed()
        qapp.processEvents()

        assert not organizer.execute_button.isEnabled()
        assert not organizer.undo_button.isEnabled()
        assert not lightweight.drop_zone.isEnabled()
        assert not lightweight.undo_button.isEnabled()
        if stage != "forward":
            assert journal.get_transaction(transaction.transaction_id).status is (
                TransactionStatus.COMPLETED
            )
            assert "recovery_required" in organizer.recent_transaction_label.text()
    finally:
        full.close()
        lightweight.close()
        database.dispose()
