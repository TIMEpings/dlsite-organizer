"""Qt palette construction and application for the neutral light theme."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QProxyStyle, QPushButton, QStyle, QStyleOption

from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS


def use_custom_focus_border(button: QPushButton) -> None:
    """Opt in only where the owning UI supplies a verified visible QSS border.

    A visual role or a sizing stylesheet alone is not this guarantee. Keep this
    call paired with the owner's focus rules and rendered characterization.
    The proxy reads the property at paint time; no QSS repolish is needed.
    """
    button.setProperty("customFocusBorder", True)


class _SingleButtonFocusStyle(QProxyStyle):
    """Omit native button focus only for explicitly owned custom focus borders."""

    def drawPrimitive(
        self,
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget=None,
    ) -> None:
        if (
            element == QStyle.PrimitiveElement.PE_FrameFocusRect
            and isinstance(widget, QPushButton)
            and widget.property("customFocusBorder") is True
        ):
            return
        super().drawPrimitive(element, option, painter, widget)


def create_light_palette() -> QPalette:
    """Build a palette with light surfaces and explicit disabled/selection roles."""
    colors = LIGHT_COLORS
    palette = QPalette()
    active_roles = {
        QPalette.ColorRole.Window: colors.background,
        QPalette.ColorRole.WindowText: colors.text_primary,
        QPalette.ColorRole.Base: colors.surface,
        QPalette.ColorRole.AlternateBase: colors.surface_secondary,
        QPalette.ColorRole.ToolTipBase: colors.surface,
        QPalette.ColorRole.ToolTipText: colors.text_primary,
        QPalette.ColorRole.Text: colors.text_primary,
        QPalette.ColorRole.Button: colors.surface,
        QPalette.ColorRole.ButtonText: colors.text_primary,
        QPalette.ColorRole.BrightText: colors.error,
        QPalette.ColorRole.Link: colors.accent,
        QPalette.ColorRole.Highlight: colors.selection,
        QPalette.ColorRole.HighlightedText: colors.text_primary,
        QPalette.ColorRole.PlaceholderText: colors.text_muted,
    }
    for role, value in active_roles.items():
        palette.setColor(role, QColor(value))
        palette.setColor(QPalette.ColorGroup.Inactive, role, QColor(value))

    disabled_roles = {
        QPalette.ColorRole.WindowText: colors.text_muted,
        QPalette.ColorRole.Text: colors.text_muted,
        QPalette.ColorRole.ButtonText: colors.text_muted,
        QPalette.ColorRole.Base: colors.surface_secondary,
        QPalette.ColorRole.Button: colors.surface_secondary,
        QPalette.ColorRole.Highlight: colors.surface_secondary,
        QPalette.ColorRole.HighlightedText: colors.text_muted,
        QPalette.ColorRole.PlaceholderText: colors.text_muted,
    }
    for role, value in disabled_roles.items():
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(value))
    return palette


def apply_light_theme(application: QApplication) -> None:
    """Set shared defaults, preserving native focus unless explicitly opted out."""
    if not isinstance(application.style(), _SingleButtonFocusStyle):
        application.setStyle(_SingleButtonFocusStyle(application.style()))
    application.setPalette(create_light_palette())
    font = QFont("Segoe UI")
    font.setPointSizeF(10)
    application.setFont(font)
