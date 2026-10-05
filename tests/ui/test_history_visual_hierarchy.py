"""History presentation contracts, independent of journal mutation semantics."""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication

from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.rename_history import RenameHistoryService
from dlsite_organizer.ui.pages.rename_history_page import RenameHistoryPage


@pytest.fixture
def history(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "history.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    yield app, journal, root
    database.dispose()


@pytest.mark.parametrize("count", [1, 20])
def test_observation_layout_and_compact_facts(history, count: int) -> None:
    app, journal, root = history
    transaction = journal.create_transaction(
        root, tuple((root / f"source-{i}", root / f"target-{i}") for i in range(count))
    )
    for operation in transaction.operations:
        journal.mark_operation_success(transaction.transaction_id, operation.sequence,
                                       transaction.created_at)
    journal.mark_transaction_completed(transaction.transaction_id, transaction.created_at)
    before = journal.get_transaction(transaction.transaction_id)
    page = RenameHistoryPage(RenameHistoryService(journal))
    try:
        page.resize(960, 640)
        page.refresh_history()
        page.show()
        app.processEvents()
        assert "0 项" not in page.journal_facts_label.text()
        assert "重命名：" in page.journal_facts_label.text()
        assert "Journal" not in page.operation_status_label.text()
        assert page.operation_sequence_label.isHidden()
        assert page.execution_error_value.parentWidget().isHidden()
        assert page.undo_error_value.parentWidget().isHidden()
        assert page.observations_table.isHidden() is (count == 1)
        assert page.single_observation.isHidden() is (count != 1)
        assert page.technical_section.isHidden()
        assert page.detail_scroll.horizontalScrollBar().maximum() == 0
        table = page.operations_table
        assert not table.showGrid()
        assert table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows
        assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        assert table.item(0, 1).foreground().color().isValid()
        if count > 1:
            table.setFocus()
            QApplication.sendEvent(table, QKeyEvent(
                QKeyEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier
            ))
            assert page.source_path_value.full_path == str(before.operations[1].source_path)
        assert journal.get_transaction(transaction.transaction_id) == before
    finally:
        page.close()


def test_copy_actions_stay_local_in_wide_detail(history) -> None:
    app, journal, root = history
    journal.create_transaction(root, ((root / "RJ370190", root / "renamed"),))
    page = RenameHistoryPage(RenameHistoryService(journal))
    try:
        page.resize(1540, 900)
        page.refresh_history()
        page.show()
        app.processEvents()
        for field, button in (
            (page.root_path_value, page.copy_root_path_button),
            (page.source_path_value, page.copy_source_path_button),
            (page.target_path_value, page.copy_target_path_button),
        ):
            assert field.width() <= 560
            assert button.geometry().left() - field.geometry().right() <= 16
            assert not button.toolTip()
    finally:
        page.close()
