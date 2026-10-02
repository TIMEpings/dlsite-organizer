"""Navigation list painting that keeps focus distinct from active selection."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS, Shape


class NavigationItemDelegate(QStyledItemDelegate):
    """Use a palette-colored focus ring instead of the platform's inner frame."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        if not option.state & QStyle.StateFlag.State_HasFocus:
            super().paint(painter, option, index)
            return

        content_option = QStyleOptionViewItem(option)
        content_option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, content_option, index)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(LIGHT_COLORS.focus), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        focus_rect = option.rect.adjusted(1, 1, -2, -2)
        painter.drawRoundedRect(
            focus_rect,
            Shape.CONTROL_RADIUS,
            Shape.CONTROL_RADIUS,
        )
        painter.restore()
