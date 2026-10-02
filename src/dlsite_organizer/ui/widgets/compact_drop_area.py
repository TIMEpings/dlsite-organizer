"""Compact root-input surface that accepts local directory drops."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QSizePolicy

from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS, Shape
from dlsite_organizer.ui.widgets.drop_zone import local_directory_paths


class CompactDirectoryDropArea(QFrame):
    """A low-profile directory drop target with a temporary valid-drop highlight."""

    paths_dropped = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("organizerInputPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAcceptDrops(True)
        self.setProperty("dropActive", False)
        colors = LIGHT_COLORS
        self.setStyleSheet(
            f"""
            QFrame#organizerInputPanel {{
              background: {colors.surface};
              border: 1px solid {colors.border};
              border-radius: {Shape.SOFT_RADIUS}px;
            }}
            QFrame#organizerInputPanel[dropActive="true"] {{
              background: {colors.selection};
              border: 1px solid {colors.focus};
            }}
            """
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if local_directory_paths(event.mimeData()):
            self._set_drop_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if local_directory_paths(event.mimeData()):
            self._set_drop_active(True)
            event.acceptProposedAction()
        else:
            self._set_drop_active(False)
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._set_drop_active(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        paths: tuple[Path, ...] = local_directory_paths(event.mimeData())
        self._set_drop_active(False)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.paths_dropped.emit(paths)

    def _set_drop_active(self, active: bool) -> None:
        if self.property("dropActive") == active:
            return
        self.setProperty("dropActive", active)
        self.style().unpolish(self)
        self.style().polish(self)
