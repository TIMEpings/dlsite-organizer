"""Structured, widget-free delegates for the Organizer preview table."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.ui.models.organizer_preview_model import (
    OrganizerPreviewModel,
    StatusPresentation,
    plan_presentation,
    plan_status_label,
)
from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS


class OrganizerWorkDelegate(QStyledItemDelegate):
    """Paint work identity and title on two stable lines without child widgets."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        model = index.model()
        if not isinstance(model, OrganizerPreviewModel) or not 0 <= index.row() < len(
            model.state.plans
        ):
            super().paint(painter, option, index)
            return
        plan = model.state.plans[index.row()]
        self._paint_item_background(painter, option)

        code = plan.work_code or "、".join(plan.work_codes) or "—"
        title = plan.work.title if plan.work is not None else plan.current_name
        rect = option.rect.adjusted(8, 2, -6, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        palette = option.palette
        primary_color = palette.color(
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        )
        secondary_color = QColor(primary_color)
        if not selected:
            secondary_color = palette.color(QPalette.ColorRole.PlaceholderText)

        line_height = max(1, option.fontMetrics.height())
        block_height = min(rect.height(), line_height * 2)
        top = rect.top() + max(0, (rect.height() - block_height) // 2)
        primary_font = QFont(option.font)
        primary_font.setWeight(QFont.Weight.DemiBold)
        secondary_font = QFont(option.font)

        painter.save()
        painter.setClipRect(option.rect)
        painter.setFont(primary_font)
        painter.setPen(primary_color)
        primary_metrics = QFontMetrics(primary_font)
        primary_rect = QRect(rect.left(), top, rect.width(), line_height)
        painter.drawText(
            primary_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            primary_metrics.elidedText(code, Qt.TextElideMode.ElideRight, rect.width()),
        )
        painter.setFont(secondary_font)
        painter.setPen(secondary_color)
        secondary_metrics = QFontMetrics(secondary_font)
        secondary_rect = QRect(rect.left(), top + line_height, rect.width(), line_height)
        painter.drawText(
            secondary_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            secondary_metrics.elidedText(title, Qt.TextElideMode.ElideRight, rect.width()),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        del index
        return QSize(220, max(40, option.fontMetrics.height() * 2 + 6))

    @staticmethod
    def _paint_item_background(painter: QPainter, option: QStyleOptionViewItem) -> None:
        item_option = QStyleOptionViewItem(option)
        item_option.text = ""
        item_option.icon = QIcon()
        style = (
            item_option.widget.style()
            if item_option.widget is not None else QApplication.style()
        )
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            item_option,
            painter,
            item_option.widget,
        )


class OrganizerStatusDelegate(QStyledItemDelegate):
    """Paint concise status marks beside the model's real domain status."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        model = index.model()
        if not isinstance(model, OrganizerPreviewModel) or not 0 <= index.row() < len(
            model.state.plans
        ):
            super().paint(painter, option, index)
            return
        plan = model.state.plans[index.row()]
        self._paint_item_background(painter, option)

        mark, mark_color = self._mark(plan)
        status = plan_status_label(plan.status)
        has_warning = bool(plan.warnings)
        trailing_mark = "  ⚠" if has_warning else ""
        text = status + trailing_mark
        rect = option.rect.adjusted(8, 0, -5, 0)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        foreground = option.palette.color(
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        )
        if selected:
            mark_color = foreground
        painter.save()
        painter.setClipRect(option.rect)
        mark_font = QFont(option.font)
        mark_font.setWeight(QFont.Weight.Bold)
        painter.setFont(mark_font)
        painter.setPen(mark_color)
        mark_width = max(14, option.fontMetrics.horizontalAdvance(mark) + 3)
        mark_rect = QRect(rect.left(), rect.top(), mark_width, rect.height())
        painter.drawText(
            mark_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            mark,
        )
        text_rect = QRect(mark_rect.right() + 3, rect.top(), max(0, rect.width() - mark_width - 3),
                          rect.height())
        painter.setFont(option.font)
        painter.setPen(foreground)
        metrics = QFontMetrics(option.font)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width()),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        del index
        return QSize(136, max(40, option.fontMetrics.height() + 8))

    @staticmethod
    def _mark(plan: RenamePlan) -> tuple[str, QColor]:
        if plan.status is RenamePlanStatus.READY:
            return "✓", QColor(LIGHT_COLORS.success)
        if plan.status is RenamePlanStatus.UNCHANGED:
            return "＝", QColor(LIGHT_COLORS.text_muted)
        if plan_presentation(plan) is StatusPresentation.WARNING:
            return "!", QColor(LIGHT_COLORS.warning)
        if plan_presentation(plan) is StatusPresentation.BLOCKED:
            return "!", QColor(LIGHT_COLORS.blocked)
        return "·", QColor(LIGHT_COLORS.text_secondary)

    @staticmethod
    def _paint_item_background(painter: QPainter, option: QStyleOptionViewItem) -> None:
        item_option = QStyleOptionViewItem(option)
        item_option.text = ""
        item_option.icon = QIcon()
        style = (
            item_option.widget.style()
            if item_option.widget is not None else QApplication.style()
        )
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem,
            item_option,
            painter,
            item_option.widget,
        )
