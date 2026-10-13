"""Durable master status contracts and representative delegate rendering."""

from datetime import UTC, datetime
from math import ceil, floor
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from dlsite_organizer.domain.rename_execution import (
    RenameTransactionSummary,
    TransactionStatus,
    UndoTransactionState,
)
from dlsite_organizer.ui.pages.rename_history_page import (
    RenameHistoryPage,
    _master_status_visual,
    _paint_master_status_mark,
    _undo_arrow_geometry,
)


@pytest.mark.parametrize(
    "status,label,category",
    [
        (TransactionStatus.PENDING, "进行中", "pending"),
        (TransactionStatus.COMPLETED, "已完成", "success"),
        (TransactionStatus.PARTIAL, "部分完成", "partial"),
        (TransactionStatus.FAILED, "失败", "error"),
        (TransactionStatus.UNDONE, "已撤销", "reversed"),
        (TransactionStatus.UNDO_PARTIAL, "部分撤销", "partial"),
        (TransactionStatus.RECOVERY_REQUIRED, "需要检查", "attention"),
    ],
)
def test_master_durable_status_contract(status, label, category):
    app = QApplication.instance() or QApplication([])
    page = RenameHistoryPage(None)
    try:
        item = page._add_transaction_item(
            RenameTransactionSummary(
                "durable",
                Path("library"),
                datetime(2026, 10, 6, tzinfo=UTC),
                status,
                operation_count=3,
            )
        )
        assert item.text().endswith(f"3 项 · {label}")
        assert item.data(Qt.ItemDataRole.AccessibleTextRole).endswith(f"，3 项，{label}")
        assert item.sizeHint().height() == 58
        assert not item.toolTip()
        assert page.transaction_list.itemWidget(item) is None
        shape, color = _master_status_visual(status)
        assert shape == category
        index = page.transaction_list.model().index(0, 0)
        assert index.data(page._MASTER_STATUS_ROLE) == status
        masks = []
        for state in (
            QStyle.StateFlag.State_Enabled,
            QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_MouseOver,
            QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected,
            QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_HasFocus,
        ):
            image = QImage(248, 58, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.white)
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, 248, 58)
            option.font = page.font()
            option.palette = page.palette()
            option.state = state
            painter = QPainter(image)
            page.transaction_list.itemDelegate().paint(painter, option, index)
            painter.end()
            # Solid stroke interiors avoid antialiased edge/background differences.
            ink = {
                (x, y)
                for x in range(248)
                for y in range(30, 52)
                if image.pixel(x, y) == QColor(color).rgba()
            }
            assert ink
            icon_left = 12 + option.fontMetrics.horizontalAdvance("3 项 · ")
            assert any(icon_left <= x < icon_left + 14 for x, _ in ink)
            assert any(x >= icon_left + 18 for x, _ in ink)
            assert all(12 <= x < 239 and 30 <= y < 52 for x, y in ink)
            masks.append(ink)
        assert all(mask == masks[0] for mask in masks)
        assert app is not None
    finally:
        page.close()


def test_undone_arrowhead_and_shaft_form_one_connected_mark():
    image = QImage(20, 20, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    shape, color = _master_status_visual(TransactionStatus.UNDONE)
    _paint_master_status_mark(painter, QRectF(3, 3, 14, 14), shape, color)
    painter.end()
    ink = {
        (x, y)
        for x in range(20)
        for y in range(20)
        if image.pixelColor(x, y).alpha() > 24
    }
    assert ink
    components = 0
    remaining = set(ink)
    while remaining:
        components += 1
        stack = [remaining.pop()]
        while stack:
            x, y = stack.pop()
            for neighbor in (
                (x - 1, y - 1), (x, y - 1), (x + 1, y - 1),
                (x - 1, y),                 (x + 1, y),
                (x - 1, y + 1), (x, y + 1), (x + 1, y + 1),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
    assert components == 1


def test_undone_arrowhead_uses_the_shaft_endpoint_tangent():
    shaft, tip, wings = _undo_arrow_geometry()
    start = shaft.elementAt(0)
    control = shaft.elementAt(1)
    assert (start.x, start.y) == pytest.approx((tip.x(), tip.y()))

    tangent_x = tip.x() - control.x
    tangent_y = tip.y() - control.y
    tangent_length = (tangent_x**2 + tangent_y**2) ** 0.5
    direction_x = tangent_x / tangent_length
    direction_y = tangent_y / tangent_length
    wing_vectors = [(wing.x() - tip.x(), wing.y() - tip.y()) for wing in wings]
    bisector_x = sum(vector[0] for vector in wing_vectors)
    bisector_y = sum(vector[1] for vector in wing_vectors)
    bisector_length = (bisector_x**2 + bisector_y**2) ** 0.5
    alignment = (
        bisector_x / bisector_length * -direction_x
        + bisector_y / bisector_length * -direction_y
    )
    assert alignment == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("status", list(TransactionStatus))
def test_vector_mark_is_unclipped_under_fractional_dpr_scale(status):
    image = QImage(30, 30, QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(1.25)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    shape, color = _master_status_visual(status)
    _paint_master_status_mark(painter, QRectF(5, 5, 14, 14), shape, color)
    painter.end()
    ink = [(x, y) for x in range(30) for y in range(30) if image.pixelColor(x, y).alpha() > 0]
    assert ink
    # This image exercises a fractional painter transform; it is not native DPI coverage.
    # All antialiased ink stays inside the assigned 14-logical-pixel square.
    assert all(
        floor(5 * 1.25) <= x < ceil(19 * 1.25) and floor(5 * 1.25) <= y < ceil(19 * 1.25)
        for x, y in ink
    )


@pytest.mark.parametrize(
    "undo_state",
    [
        UndoTransactionState.IN_PROGRESS,
        UndoTransactionState.RECOVERY_REQUIRED,
    ],
)
def test_unresolved_undo_uses_existing_durable_projection(undo_state):
    app = QApplication.instance() or QApplication([])
    page = RenameHistoryPage(None)
    try:
        item = page._add_transaction_item(
            RenameTransactionSummary(
                "undo",
                Path("library"),
                datetime(2026, 10, 6, tzinfo=UTC),
                TransactionStatus.COMPLETED,
                undo_state,
                1,
            )
        )
        assert item.text().endswith("1 项 · 需要检查")
        assert item.data(page._MASTER_STATUS_ROLE) == TransactionStatus.RECOVERY_REQUIRED
        assert app is not None
    finally:
        page.close()
