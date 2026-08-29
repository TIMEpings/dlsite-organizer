from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem

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
    def lookup(self, raw_workno: str) -> LookupResult:
        work = Work(workno=raw_workno, title="Preview Title", maker_name="Circle")
        return LookupResult(work=work, formatted_name=f"[Circle][{raw_workno}] Preview Title")


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


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
    status_item = cast(QTableWidgetItem, page.table.item(0, 0))
    current_item = cast(QTableWidgetItem, page.table.item(0, 1))
    code_item = cast(QTableWidgetItem, page.table.item(0, 2))
    proposed_item = cast(QTableWidgetItem, page.table.item(0, 5))
    assert status_item.text() == "READY"
    assert current_item.text() == "old RJ01609020"
    assert code_item.text() == "RJ01609020"
    assert proposed_item.text() == "[Circle][RJ01609020] Preview Title"
    assert "Ready 1" in page.summary_label.text()
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
    assert 'stale' in page.summary_label.text().lower()
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
    stale_details = cast(QTableWidgetItem, stale_page.table.item(0, 6))
    assert stale_page._preview is not None
    assert stale_page._preview.plans[0].status is RenamePlanStatus.READY
    assert "使用旧缓存 metadata" in stale_details.text()
    stale_page.close()

    fresh_page = OrganizerPage(
        OrganizerService(FreshnessLookupService(LookupFreshness.CACHE_FRESH))
    )
    fresh_page.set_preview(fresh_page._organizer_service.preview(tmp_path))
    fresh_details = cast(QTableWidgetItem, fresh_page.table.item(0, 6))
    assert "使用旧缓存 metadata" not in fresh_details.text()
    fresh_page.close()
