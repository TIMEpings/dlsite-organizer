"""Compact persistent status banner for blocking and stale page state."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS, Shape


class BannerState(StrEnum):
    INFO = "info"
    WARNING = "warning"
    STALE = "stale"
    BLOCKED = "blocked"


class StatusBanner(QFrame):
    """One compact, accessible message for a persistent page condition."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBanner")
        self.setProperty("state", BannerState.INFO.value)
        self.setAccessibleName("页面状态")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(9)

        self.mark_label = QLabel("i")
        self.mark_label.setObjectName("statusBannerMark")
        self.mark_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.mark_label, 0)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        self.title_label = QLabel()
        self.title_label.setObjectName("statusBannerTitle")
        self.message_label = QLabel()
        self.message_label.setObjectName("statusBannerMessage")
        self.message_label.setWordWrap(True)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.message_label)
        layout.addLayout(text_layout, 1)

        colors = LIGHT_COLORS
        self.setStyleSheet(
            f"""
            QFrame#statusBanner {{
              background: {colors.surface};
              border: 1px solid {colors.border};
              border-radius: {Shape.SOFT_RADIUS}px;
            }}
            QFrame#statusBanner[state="info"] {{ background: {colors.surface_secondary}; }}
            QFrame#statusBanner[state="warning"] {{ border-left: 3px solid {colors.warning}; }}
            QFrame#statusBanner[state="stale"] {{ border-left: 3px solid {colors.stale}; }}
            QFrame#statusBanner[state="blocked"] {{ border-left: 3px solid {colors.blocked}; }}
            QLabel#statusBannerMark {{ color: {colors.accent}; font-weight: 700; }}
            QFrame#statusBanner[state="warning"] QLabel#statusBannerMark {{
              color: {colors.warning};
            }}
            QFrame#statusBanner[state="stale"] QLabel#statusBannerMark {{ color: {colors.stale}; }}
            QFrame#statusBanner[state="blocked"] QLabel#statusBannerMark {{
              color: {colors.blocked};
            }}
            QLabel#statusBannerTitle {{ color: {colors.text_primary}; font-weight: 600; }}
            QLabel#statusBannerMessage {{ color: {colors.text_secondary}; }}
            """
        )
        self.hide()

    def show_status(self, state: BannerState, title: str, message: str) -> None:
        """Present one persistent state and expose its full text to assistive tech."""
        self.setProperty("state", state.value)
        self.mark_label.setText(
            "!" if state in {BannerState.WARNING, BannerState.STALE, BannerState.BLOCKED}
            else "i"
        )
        self.title_label.setText(title)
        self.message_label.setText(message)
        self.setAccessibleDescription(f"{title}。{message}")
        self.style().unpolish(self)
        self.style().polish(self)
        self.show()

    def clear_status(self) -> None:
        """Hide the banner when no persistent condition needs attention."""
        self.title_label.clear()
        self.message_label.clear()
        self.setAccessibleDescription("")
        self.hide()
