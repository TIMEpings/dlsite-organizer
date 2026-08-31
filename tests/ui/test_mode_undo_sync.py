from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.app.runtime import RuntimeSignals
from dlsite_organizer.app.settings import AppSettings, SettingsService
from dlsite_organizer.domain.rename_execution import TransactionStatus
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupResult, LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.quick_rename import QuickRenameService, QuickRenameStatus
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.lightweight_window import LightweightWindow
from dlsite_organizer.ui.main_window import MainWindow


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
        assert result.transaction_id in full.organizer_page.recent_transaction_label.text()
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
        assert full.organizer_page._last_undo_result is not None
        assert full.organizer_page._last_undo_result.status is TransactionStatus.UNDONE
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

        execution = organizer._last_execution_result
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


def test_recovery_required_state_is_refreshed_in_both_modes(
    qapp: QApplication, tmp_path: Path
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
        journal.mark_recovery_required(
            transaction.transaction_id,
            None,
            "fixture recovery state",
            datetime.now(UTC),
        )
        signals.notify_mutation_history_changed()
        qapp.processEvents()

        assert not organizer.execute_button.isEnabled()
        assert not organizer.undo_button.isEnabled()
        assert not lightweight.drop_zone.isEnabled()
        assert not lightweight.undo_button.isEnabled()
    finally:
        full.close()
        lightweight.close()
        database.dispose()
