"""Compact reusable page title and description header."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from dlsite_organizer.ui.theme.tokens import FONT_ROLES, FontRole, Spacing


class PageHeader(QWidget):
    """Present a page title, optional description, and one optional action area."""

    def __init__(
        self,
        title: str,
        description: str | None = None,
        *,
        secondary_action: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Spacing.LG)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(Spacing.XS)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("pageTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._set_font_role(self.title_label, FontRole.PAGE_TITLE)
        text.addWidget(self.title_label)

        self.description_label: QLabel | None = None
        if description:
            self.description_label = QLabel(description)
            self.description_label.setObjectName("pageDescription")
            self.description_label.setWordWrap(True)
            self._set_font_role(self.description_label, FontRole.PAGE_DESCRIPTION)
            text.addWidget(self.description_label)
        row.addLayout(text, 1)

        self.secondary_action_area: QWidget | None = None
        if secondary_action is not None:
            self.secondary_action_area = QWidget(self)
            action_layout = QHBoxLayout(self.secondary_action_area)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.addWidget(secondary_action)
            row.addWidget(self.secondary_action_area, 0, Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def _set_font_role(label: QLabel, role: FontRole) -> None:
        spec = FONT_ROLES[role]
        font = label.font()
        font.setPointSizeF(spec.point_size)
        font.setWeight(QFont.Weight(spec.weight))
        label.setFont(font)
