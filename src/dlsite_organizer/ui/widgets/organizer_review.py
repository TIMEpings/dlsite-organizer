"""Small Organizer-only review surfaces; no global theme or input changes."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy

from dlsite_organizer.ui.delegates.organizer_delegates import paint_status_mark
from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS
from dlsite_organizer.ui.widgets.path_display import PathDisplay


class OrganizerPathDisplay(PathDisplay):
    """Keep full selection/copy/scroll access, with contextual rather than input chrome."""

    def __init__(self, *, copy_button: bool = True) -> None:
        super().__init__(copy_button=copy_button, compact=True)
        self.path_input.setFrame(False)
        self.path_input.setMinimumWidth(0)
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


class OrganizerElidedLabel(QLabel):
    """One line of secondary context, with full text accessible on hover and to AT."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

    def setText(self, text: str) -> None:
        super().setText(text)
        self.setToolTip(text)
        self.setAccessibleDescription(text)

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
                   unchanged: int, skipped: int) -> None:
        self._items = (
            ("", f"{total} 项", LIGHT_COLORS.text_primary),
            ("ready", f"可执行 {ready}", LIGHT_COLORS.success),
            ("blocked", f"不可执行 {blocked}", LIGHT_COLORS.blocked),
            ("warning", f"警告 {warning}", LIGHT_COLORS.warning),
            ("unchanged", f"无需重命名 {unchanged}", LIGHT_COLORS.text_muted),
            ("", f"扫描跳过 {skipped}", LIGHT_COLORS.text_muted),
        )
        self.setText(f"共 {total} 项 · 可执行 {ready} · 不可执行 {blocked} · 含警告 {warning} · "
                     f"无需重命名 {unchanged} · 扫描跳过 {skipped}")
        self.setToolTip(self.text() + "\n警告可与任一状态重叠，不参与项目总数的互斥分组。")
        self.setAccessibleDescription(self.toolTip())
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
