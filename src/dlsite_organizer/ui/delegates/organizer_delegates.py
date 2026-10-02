"""Structured, widget-free delegates for the Organizer preview table."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.ui.models.organizer_preview_model import (
    OrganizerPreviewModel,
    plan_status_label,
)
from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS


def paint_status_mark(painter: QPainter, rect: QRect, kind: str, color: QColor) -> None:
    """Small deterministic vector marks shared by status cells and summary."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.center())
    painter.scale(rect.width() / 18, rect.height() / 18)
    painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "ready":
        painter.drawPolyline(QPolygonF([QPointF(-5, 0), QPointF(-1, 4), QPointF(6, -4)]))
    elif kind == "unchanged":
        painter.drawLine(QPointF(-5, -2), QPointF(5, -2))
        painter.drawLine(QPointF(-5, 2), QPointF(5, 2))
    elif kind == "warning":
        painter.drawPolygon(QPolygonF([QPointF(0, -7), QPointF(7, 6), QPointF(-7, 6)]))
        painter.drawLine(QPointF(0, -2), QPointF(0, 1))
        painter.drawPoint(QPointF(0, 4))
    else:
        painter.drawEllipse(QPointF(0, 0), 7, 7)
        painter.drawLine(QPointF(0, -4), QPointF(0, 1))
        painter.drawPoint(QPointF(0, 4))
    painter.restore()


def paint_item_background(painter: QPainter, option: QStyleOptionViewItem) -> None:
    item_option = QStyleOptionViewItem(option)
    item_option.text = ""
    item_option.icon = QIcon()
    item_option.state &= ~QStyle.StateFlag.State_HasFocus
    item_option.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
    style = item_option.widget.style() if item_option.widget is not None else QApplication.style()
    style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item_option, painter,
                      item_option.widget)


class OrganizerCheckDelegate(QStyledItemDelegate):
    """Own indicator painting/hit geometry; the model alone owns execution checks."""

    @staticmethod
    def indicator_rect(option: QStyleOptionViewItem) -> QRect:
        style = option.widget.style() if option.widget is not None else QApplication.style()
        width = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, None, option.widget)
        height = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight, None, option.widget)
        rect = QRect(0, 0, width, height)
        rect.moveCenter(option.rect.center())
        return rect

    @staticmethod
    def check_projection(index, option: QStyleOptionViewItem) -> tuple[bool, bool]:
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        editable = bool(index.flags() & Qt.ItemFlag.ItemIsUserCheckable) and bool(
            option.state & QStyle.StateFlag.State_Enabled
        )
        return checked, editable

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        paint_item_background(painter, option)
        checked, editable = self.check_projection(index, option)
        rect = self.indicator_rect(option)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(QColor(LIGHT_COLORS.focus), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect.adjusted(-3, -3, 3, 3)), 4, 4)
        border = LIGHT_COLORS.border_strong if editable else LIGHT_COLORS.border
        fill = LIGHT_COLORS.surface if editable else LIGHT_COLORS.surface_secondary
        if checked:
            border = fill = LIGHT_COLORS.accent if editable else LIGHT_COLORS.text_muted
        elif editable and option.state & QStyle.StateFlag.State_MouseOver:
            border = LIGHT_COLORS.accent
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        if checked:
            paint_status_mark(painter, rect.adjusted(2, 2, -2, -2), "ready",
                              QColor(LIGHT_COLORS.surface))
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        _, editable = self.check_projection(index, option)
        if not editable:
            return False
        if event.type() in (
            QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        ):
            if event.button() != Qt.MouseButton.LeftButton or not self.indicator_rect(
                option
            ).adjusted(-3, -3, 3, 3).contains(event.position().toPoint()):
                return False
            if event.type() != QEvent.Type.MouseButtonRelease:
                return True
        elif event.type() == QEvent.Type.KeyPress:
            if event.key() not in (Qt.Key.Key_Space, Qt.Key.Key_Select):
                return False
        else:
            return False
        value = (Qt.CheckState.Unchecked if index.data(Qt.ItemDataRole.CheckStateRole)
                 == Qt.CheckState.Checked else Qt.CheckState.Checked)
        return model.setData(index, value, Qt.ItemDataRole.CheckStateRole)


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
        paint_item_background(painter, option)
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.save()
            painter.setPen(QColor(LIGHT_COLORS.focus))
            painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
            painter.restore()


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
        has_warning = self.additional_warning_marker(plan)
        rect = option.rect.adjusted(8, 0, -5, 0)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        foreground = option.palette.color(
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        )
        if selected:
            mark_color = foreground
        painter.save()
        painter.setClipRect(option.rect)
        mark_rect = QRect(rect.left(), rect.center().y() - 8, 18, 18)
        paint_status_mark(painter, mark_rect, mark, mark_color)
        text_width = min(option.fontMetrics.horizontalAdvance(status),
                         max(0, rect.width() - 23 - (22 if has_warning else 0)))
        text_rect = QRect(rect.left() + 23, rect.top(), text_width, rect.height())
        painter.setFont(option.font)
        painter.setPen(foreground)
        metrics = QFontMetrics(option.font)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(status, Qt.TextElideMode.ElideRight, text_rect.width()),
        )
        if has_warning:
            warning_rect = QRect(text_rect.right() + 5, mark_rect.top(), 18, 18)
            paint_status_mark(painter, warning_rect, "warning", QColor(LIGHT_COLORS.warning))
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        del index
        return QSize(136, max(40, option.fontMetrics.height() + 8))

    @staticmethod
    def _mark(plan: RenamePlan) -> tuple[str, QColor]:
        if plan.status is RenamePlanStatus.READY:
            return "ready", QColor(LIGHT_COLORS.success)
        if plan.status is RenamePlanStatus.UNCHANGED:
            return "unchanged", QColor(LIGHT_COLORS.text_muted)
        return "blocked", QColor(LIGHT_COLORS.blocked)

    @staticmethod
    def additional_warning_marker(plan: RenamePlan) -> bool:
        return bool(plan.warnings) and plan.status in (
            RenamePlanStatus.READY, RenamePlanStatus.UNCHANGED,
        )

    @staticmethod
    def _paint_item_background(painter: QPainter, option: QStyleOptionViewItem) -> None:
        OrganizerWorkDelegate._paint_item_background(painter, option)
