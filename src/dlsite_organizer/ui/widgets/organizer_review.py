"""Small Organizer-only review surfaces; no global theme or input changes."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QStyle, QStyleOptionFrame

from dlsite_organizer.ui.delegates.organizer_delegates import paint_status_mark
from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS
from dlsite_organizer.ui.widgets.path_display import PathDisplay


class OrganizerBulkMenuButton(QPushButton):
    """A native menu button with explicit Enter activation outside dialogs."""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        menu = self.menu()
        if self.isEnabled() and menu is not None and event.key() in (
            Qt.Key.Key_Return, Qt.Key.Key_Enter,
        ):
            menu.popup(self.mapToGlobal(QPoint(0, self.height())))
            event.accept()
            return
        super().keyPressEvent(event)


class OrganizerPathDisplay(PathDisplay):
    """Keep full selection/copy/scroll access, with contextual rather than input chrome."""

    def __init__(self, *, copy_button: bool = True) -> None:
        super().__init__(copy_button=copy_button, compact=True)
        self.path_input.setFrame(False)
        self.path_input.setMinimumWidth(0)
        self.path_input.installEventFilter(self)
        if self.copy_button is not None:
            self.copy_button.setToolTip("")
        self.setStyleSheet(f"""
            QWidget#pathDisplay QLineEdit#pathDisplayInput {{
              background: transparent;
              color: {LIGHT_COLORS.text_secondary};
              border: 0;
              border-bottom: 1px solid transparent;
              border-radius: 0;
              min-height: 22px;
              padding: 1px 0;
            }}
            QWidget#pathDisplay QLineEdit#pathDisplayInput:focus {{
              border-bottom: 1px solid {LIGHT_COLORS.focus};
            }}
            QWidget#pathDisplay QPushButton#copyPathButton {{
              min-height: 22px;
              padding: 1px 6px;
            }}
        """)

    def set_path(self, value: str) -> None:
        super().set_path(value)
        self._update_path_tooltip()

    def _update_path_tooltip(self) -> None:
        option = QStyleOptionFrame()
        option.initFrom(self.path_input)
        rect = self.path_input.style().subElementRect(
            QStyle.SubElement.SE_LineEditContents, option, self.path_input)
        margins = self.path_input.textMargins()
        width = max(0, rect.width() - margins.left() - margins.right() - 4)
        text = self.text()
        self.path_input.setToolTip(
            text if self.path_input.fontMetrics().horizontalAdvance(text) > width else "")

    def eventFilter(self, watched, event) -> bool:
        if watched is self.path_input and event.type() in (QEvent.Type.Resize, QEvent.Type.ToolTip):
            self._update_path_tooltip()
        return super().eventFilter(watched, event)


class OrganizerElidedLabel(QLabel):
    """One line of secondary context, with full text accessible on hover and to AT."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._extra_tooltip = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setAccessibleDescription(text)

    def setText(self, text: str) -> None:
        super().setText(text)
        self._extra_tooltip = ""
        self._update_tooltip()
        self.setAccessibleDescription(text)

    def _update_tooltip(self) -> None:
        self.setToolTip(self._extra_tooltip or (
            self.text() if self.fontMetrics().horizontalAdvance(self.text())
            > self.contentsRect().width() else ""))

    def set_extra_tooltip(self, text: str) -> None:
        """Preserve genuinely additional diagnostics when available width changes."""
        self._extra_tooltip = text
        self._update_tooltip()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_tooltip()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().height() + 4)

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.text()),
                     self.minimumSizeHint().height())

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignLeft
                         | Qt.AlignmentFlag.AlignVCenter,
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight,
                                                       self.contentsRect().width()))


class OrganizerSummary(QLabel):
    """Six bounded count marks, priority first; secondary counts stay in accessible text."""

    def __init__(self) -> None:
        super().__init__("尚未生成预览")
        self._items: tuple[tuple[str, str, str], ...] = ()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_counts(self, total: int, ready: int, blocked: int, warning: int,
                   unchanged: int, skipped: int, *, completed: int = 0) -> None:
        self._items = (
            ("", f"{total} 项", LIGHT_COLORS.text_primary),
            *((("completed", f"本轮已完成 {completed}", LIGHT_COLORS.success),)
              if completed else ()),
            ("ready", f"可执行 {ready}", LIGHT_COLORS.success),
            ("blocked", f"不可执行 {blocked}", LIGHT_COLORS.blocked),
            ("warning", f"警告 {warning}", LIGHT_COLORS.warning),
            ("unchanged", f"无需重命名 {unchanged}", LIGHT_COLORS.text_muted),
            ("", f"扫描跳过 {skipped}", LIGHT_COLORS.text_muted),
        )
        self.setText(f"共 {total} 项 · 本轮已完成 {completed} · 可执行 {ready} · "
                     f"不可执行 {blocked} · 含警告 {warning} · "
                     f"无需重命名 {unchanged} · 扫描跳过 {skipped}")
        self.setToolTip("")
        self.setAccessibleDescription(
            self.text() + "\n警告仅统计仍可执行、但建议检查的项目；与不可执行互斥。")
        self.update()

    def clear_counts(self) -> None:
        self._items = ()
        self.setText("尚未生成预览")
        self.setToolTip("")
        self.setAccessibleDescription("尚未生成预览")

    def visible_items(self) -> tuple[tuple[str, str, str], ...]:
        available = self.contentsRect().width()
        used = 0
        visible = []
        for kind, text, color in self._items:
            width = self.fontMetrics().horizontalAdvance(text) + (23 if kind else 0) + 20
            if used + width > available:
                break
            visible.append((kind, text, color))
            used += width
        return tuple(visible)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().height() + 10)

    def sizeHint(self) -> QSize:
        return QSize(660, self.minimumSizeHint().height())

    def paintEvent(self, event) -> None:
        if not self._items:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        left = self.contentsRect().left()
        for kind, text, color in self.visible_items():
            if kind:
                paint_status_mark(painter, QRect(left, self.rect().center().y() - 8, 18, 18),
                                  kind, QColor(color))
                left += 23
            painter.setPen(QColor(LIGHT_COLORS.text_secondary if kind in (
                "ready", "blocked", "warning"
            ) else color))
            font = QFont(self.font())
            if not kind and left == self.contentsRect().left():
                font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            width = self.fontMetrics().horizontalAdvance(text)
            painter.drawText(QRect(left, 0, width + 4, self.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            left += width + 20
