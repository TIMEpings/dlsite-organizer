"""Bounded working column for form and read-heavy page content."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget


class ContentAlignment(Enum):
    """Horizontal placement for bounded page content."""

    LEFT_BOUNDED_WORKSPACE = Qt.AlignmentFlag.AlignLeft
    CENTERED_INFORMATIONAL = Qt.AlignmentFlag.AlignHCenter


class ConstrainedContent(QWidget):
    """A bounded, vertically laid out page column with an explicit alignment."""

    def __init__(
        self,
        max_width: int,
        parent: QWidget | None = None,
        *,
        alignment: ContentAlignment = ContentAlignment.CENTERED_INFORMATIONAL,
    ) -> None:
        if max_width <= 0:
            raise ValueError("max_width must be positive")
        super().__init__(parent)
        self.setObjectName("constrainedContent")
        self.alignment = alignment
        self.setMaximumWidth(max_width)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
