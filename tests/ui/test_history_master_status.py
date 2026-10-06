"""Durable master status contracts and representative delegate rendering."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from dlsite_organizer.domain.rename_execution import (
    RenameTransactionSummary,
    TransactionStatus,
    UndoTransactionState,
)
from dlsite_organizer.ui.pages.rename_history_page import RenameHistoryPage


@pytest.mark.parametrize("status,label,category", [
    (TransactionStatus.PENDING, "进行中", "pending"),
    (TransactionStatus.COMPLETED, "已完成", "success"),
    (TransactionStatus.PARTIAL, "部分完成", "partial"),
    (TransactionStatus.FAILED, "失败", "error"),
    (TransactionStatus.UNDONE, "已撤销", "reversed"),
    (TransactionStatus.UNDO_PARTIAL, "部分撤销", "partial"),
    (TransactionStatus.RECOVERY_REQUIRED, "需要检查", "attention"),
])
def test_master_durable_status_contract(status, label, category):
    app = QApplication.instance() or QApplication([])
    page = RenameHistoryPage(None)
    try:
        item = page._add_transaction_item(RenameTransactionSummary(
            "durable", Path("library"), datetime(2026, 10, 6, tzinfo=UTC), status,
            operation_count=3,
        ))
        assert item.text().endswith(f"3 项 · {label}")
        assert item.data(Qt.ItemDataRole.AccessibleTextRole).endswith(f"，3 项，{label}")
        assert item.sizeHint().height() == 58
        assert not item.toolTip()
        assert page.transaction_list.itemWidget(item) is None
        from dlsite_organizer.ui.pages.rename_history_page import _master_status_visual

        shape, color = _master_status_visual(status)
        assert shape == category
        index = page.transaction_list.model().index(0, 0)
        assert index.data(page._MASTER_STATUS_ROLE) == status
        masks = []
        for state in (QStyle.StateFlag.State_Enabled,
                      QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_MouseOver,
                      QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected
                      | QStyle.StateFlag.State_HasFocus):
            image = QImage(248, 58, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.white)
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, 248, 58)
            option.font = page.font()
            option.palette = app.palette()
            option.state = state
            painter = QPainter(image)
            page.transaction_list.itemDelegate().paint(painter, option, index)
            painter.end()
            from PySide6.QtGui import QColor

            # Solid stroke interiors avoid antialiased edge/background differences.
            ink = {(x, y) for x in range(248) for y in range(30, 52)
                   if image.pixel(x, y) == QColor(color).rgba()}
            assert ink
            icon_left = 12 + option.fontMetrics.horizontalAdvance("3 项 · ")
            assert any(icon_left <= x < icon_left + 14 for x, _ in ink)
            assert any(x >= icon_left + 18 for x, _ in ink)
            assert all(12 <= x < 239 and 30 <= y < 52 for x, y in ink)
            masks.append(ink)
        assert masks[0] == masks[1] == masks[2]
    finally:
        page.close()


@pytest.mark.parametrize("undo_state", [
    UndoTransactionState.IN_PROGRESS, UndoTransactionState.RECOVERY_REQUIRED,
])
def test_unresolved_undo_uses_existing_durable_projection(undo_state):
    app = QApplication.instance() or QApplication([])
    page = RenameHistoryPage(None)
    try:
        item = page._add_transaction_item(RenameTransactionSummary(
            "undo", Path("library"), datetime(2026, 10, 6, tzinfo=UTC),
            TransactionStatus.COMPLETED, undo_state, 1,
        ))
        assert item.text().endswith("1 项 · 需要检查")
        assert item.data(page._MASTER_STATUS_ROLE) == TransactionStatus.RECOVERY_REQUIRED
        assert app is not None
    finally:
        page.close()
