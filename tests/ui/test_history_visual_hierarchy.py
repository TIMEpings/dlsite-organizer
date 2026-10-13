"""History presentation contracts, independent of journal mutation semantics."""

from pathlib import Path

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QKeyEvent, QPainter
from PySide6.QtWidgets import QAbstractItemView, QApplication, QStyle, QStyleOptionViewItem

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
        journal.mark_operation_success(
            transaction.transaction_id, operation.sequence, transaction.created_at
        )
    journal.mark_transaction_completed(transaction.transaction_id, transaction.created_at)
    before = journal.get_transaction(transaction.transaction_id)
    page = RenameHistoryPage(RenameHistoryService(journal))
    try:
        page.resize(960, 640)
        page.refresh_history()
        page.show()
        app.processEvents()
        assert " · 0 项" not in page.journal_facts_label.text()
        assert "重命名：" in page.journal_facts_label.text()
        assert "Journal" not in page.operation_status_label.text()
        assert page.operation_sequence_label.isHidden()
        execution_group = page.execution_error_value.parentWidget()
        undo_group = page.undo_error_value.parentWidget()
        assert execution_group is not None and execution_group.isHidden()
        assert undo_group is not None and undo_group.isHidden()
        assert not hasattr(page, "observations_table")
        assert not hasattr(page, "single_observation")
        assert page.observation_summary.isHidden() is (count == 1)
        assert page.technical_section.isHidden()
        assert page.detail_scroll.horizontalScrollBar().maximum() == 0
        table = page.operations_table
        assert not table.showGrid()
        assert table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows
        assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        status = table.model().index(0, 1)
        assert status.data(Qt.ItemDataRole.ForegroundRole).color().isValid()
        if count > 1:
            table.setFocus()
            QApplication.sendEvent(
                table,
                QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier),
            )
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
        page.root_path_value.set_path("H:/Library")
        app.processEvents()
        assert page.root_path_value.width() <= (
            page.root_path_value.fontMetrics().horizontalAdvance("H:/Library") + 8
        )
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


def test_semantic_status_color_survives_delegate_selection(history) -> None:
    app, journal, root = history
    transaction = journal.create_transaction(root, ((root / "source", root / "target"),))
    journal.mark_operation_success(transaction.transaction_id, 1, transaction.created_at)
    page = RenameHistoryPage(RenameHistoryService(journal))
    try:
        page.refresh_history()
        table = page.operations_table
        index = table.model().index(0, 1)
        color = index.data(Qt.ItemDataRole.ForegroundRole).color().rgba()
        counts = []
        for selected in (False, True):
            image = QImage(120, 32, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.white)
            painter = QPainter(image)
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, 120, 32)
            option.font = table.font()
            option.palette = app.palette()
            option.state = QStyle.StateFlag.State_Enabled
            if selected:
                option.state |= QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus
            table.itemDelegate().paint(painter, option, index)
            painter.end()
            counts.append(sum(image.pixel(x, y) == color for x in range(120) for y in range(32)))
        assert counts[0] > 0 and counts[0] == counts[1]
    finally:
        page.close()


def test_history_keyboard_navigation_and_disclosure_remain_read_only(history) -> None:
    app, journal, root = history
    for _ in range(2):
        transaction = journal.create_transaction(
            root, tuple((root / f"source-{i}", root / f"target-{i}") for i in range(20))
        )
        journal.mark_transaction_completed(transaction.transaction_id, transaction.created_at)
    before = journal.list_transactions(limit=50)
    page = RenameHistoryPage(RenameHistoryService(journal))
    try:
        page.resize(960, 640)
        page.refresh_history()
        page.show()
        app.processEvents()

        def key(widget, value, modifiers=Qt.KeyboardModifier.NoModifier):
            QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.Type.KeyPress, value, modifiers))
            QApplication.sendEvent(widget, QKeyEvent(QKeyEvent.Type.KeyRelease, value, modifiers))

        page.transaction_list.setFocus()
        key(page.transaction_list, Qt.Key.Key_Down)
        assert page.transaction_list.currentRow() == 1
        key(page.transaction_list, Qt.Key.Key_Up)
        assert page.transaction_list.currentRow() == 0
        table = page.operations_table
        table.setFocus()
        key(table, Qt.Key.Key_PageDown)
        assert table.currentIndex().row() > 0
        key(table, Qt.Key.Key_PageUp)
        assert table.currentIndex().row() == 0
        key(table, Qt.Key.Key_Delete)
        assert table.model().rowCount() == 20
        page.copy_source_path_button.setFocus()
        key(page.copy_source_path_button, Qt.Key.Key_Tab)
        assert app.focusWidget() is page.target_path_value
        key(page.target_path_value, Qt.Key.Key_Tab, Qt.KeyboardModifier.ShiftModifier)
        assert app.focusWidget() is page.copy_source_path_button
        page.technical_toggle.setFocus()
        key(page.technical_toggle, Qt.Key.Key_Space)
        assert not page.technical_section.isHidden()
        key(page.technical_toggle, Qt.Key.Key_Return)
        assert page.technical_section.isHidden()
        assert journal.list_transactions(limit=50) == before
    finally:
        page.close()
