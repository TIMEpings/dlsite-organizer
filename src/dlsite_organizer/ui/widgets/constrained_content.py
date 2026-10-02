"""Bounded working column for form and read-heavy page content."""

from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget


class ConstrainedContent(QWidget):
    """A vertically laid out page column that stops growing beyond its policy."""

    def __init__(
        self,
        max_width: int,
        parent: QWidget | None = None,
    ) -> None:
        if max_width <= 0:
            raise ValueError("max_width must be positive")
        super().__init__(parent)
        self.setObjectName("constrainedContent")
        self.setMaximumWidth(max_width)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(0, 0, 0, 0)

